from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import repo
from app.services.moderation import ModerationOutcome, evaluate_message
from app.utils.text import display_user, escape, message_preview

logger = logging.getLogger(__name__)
router = Router(name="group")

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


# ---------------------------------------------------------------------------
# Silent moderation
# ---------------------------------------------------------------------------


@router.message(F.chat.type.in_(GROUP_TYPES))
async def moderate_message(message: Message, session: AsyncSession) -> None:
    await _moderate(message, session)


@router.edited_message(F.chat.type.in_(GROUP_TYPES))
async def moderate_edited_message(message: Message, session: AsyncSession) -> None:
    await _moderate(message, session)


async def _moderate(message: Message, session: AsyncSession) -> None:
    user = message.from_user
    if user is None or user.is_bot:
        return

    text = message.text or ""
    if text.startswith("/"):
        return

    settings = get_settings()
    if settings.ignore_admin_messages and user.id in settings.admin_id_set:
        return

    thread_id = message.message_thread_id
    if not await repo.is_monitored(session, message.chat.id, thread_id):
        return

    outcome = await evaluate_message(
        session,
        chat_id=message.chat.id,
        thread_id=thread_id,
        user_id=user.id,
        text=message.text,
        caption=message.caption,
    )
    if not outcome.delete:
        if outcome.ai_error:
            await _notify_ai_error(message, session, outcome.ai_error)
        return

    is_ai = outcome.ai_rule_id is not None
    details = (
        f"user={user.id} reason={outcome.reason} thread={thread_id} text={message_preview(message)}"
    )
    try:
        await message.delete()
    except TelegramBadRequest as exc:
        logger.warning("Cannot delete message %s: %s", message.message_id, exc.message)
        await repo.add_audit(
            session,
            action="message_delete_failed",
            chat_id=message.chat.id,
            details=details,
        )
        if is_ai:
            await _notify_ai_delete_failed(message, session, outcome, exc.message)
        return

    await repo.add_audit(
        session,
        action="message_deleted",
        chat_id=message.chat.id,
        details=details,
    )

    if is_ai:
        await _notify_ai_deletion(message, session, outcome)
        return

    if settings.warn_on_delete:
        try:
            warning_kwargs: dict[str, int] = {}
            if thread_id is not None:
                warning_kwargs["message_thread_id"] = thread_id
            warning = await message.answer(
                f"⚠️ Сообщение пользователя {user.id} удалено: нарушение правил чата.",
                **warning_kwargs,
            )
            task = asyncio.create_task(
                _delete_later(
                    bot=message.bot, chat_id=warning.chat.id, message_id=warning.message_id
                )
            )
            _delete_tasks.add(task)
            task.add_done_callback(_delete_tasks.discard)
        except TelegramBadRequest:
            pass


_ai_error_notified_at: dict[tuple[int, int | None], float] = {}


def _message_body(message: Message, limit: int = 1200) -> str:
    raw = " ".join((message.text or message.caption or "").split())
    return raw if len(raw) <= limit else raw[: limit - 1] + "…"


def _message_author(message: Message) -> str:
    user = message.from_user
    if user is None:
        return "неизвестный"
    return f"{display_user(user.id, user.username, user.first_name)} ({user.id})"


async def _scope_line(session: AsyncSession, message: Message) -> str:
    chat_title = escape(message.chat.title or str(message.chat.id))
    thread_id = message.message_thread_id
    if thread_id is None:
        return chat_title
    topic = await repo.get_topic(session, message.chat.id, thread_id)
    if topic is not None and topic.title:
        return f"{chat_title} → тема «{escape(topic.title)}»"
    return f"{chat_title} → тема {thread_id}"


async def _send_to_admins(bot: Bot, text: str) -> None:
    for admin_id in get_settings().admin_id_set:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001 - one unavailable admin must not break others
            pass


async def _notify_ai_deletion(
    message: Message,
    session: AsyncSession,
    outcome: ModerationOutcome,
) -> None:
    lines = [
        "🤖 <b>AI-модерация удалила сообщение</b>",
        f"Где: {await _scope_line(session, message)}",
        f"Кто: {_message_author(message)}",
        f"Причина: {escape(outcome.ai_reason or 'без пояснения')}",
        f"Правило: #{outcome.ai_rule_id}",
        "",
        f"<blockquote>{escape(_message_body(message))}</blockquote>",
    ]
    await _send_to_admins(message.bot, "\n".join(lines))


async def _notify_ai_error(
    message: Message,
    session: AsyncSession,
    error: str,
) -> None:
    settings = get_settings()
    key = (message.chat.id, message.message_thread_id)
    now = time.monotonic()
    last = _ai_error_notified_at.get(key)
    if last is not None and now - last < settings.ai_moderation_error_cooldown:
        return
    _ai_error_notified_at[key] = now

    lines = [
        "⚠️ <b>AI-модерация не смогла проверить сообщение</b>",
        f"Где: {await _scope_line(session, message)}",
        f"Кто: {_message_author(message)}",
        f"Ошибка: {escape(error)}",
        "",
        "Сообщение оставлено в чате.",
        f"<blockquote>{escape(_message_body(message))}</blockquote>",
    ]
    await _send_to_admins(message.bot, "\n".join(lines))


async def _notify_ai_delete_failed(
    message: Message,
    session: AsyncSession,
    outcome: ModerationOutcome,
    error: str,
) -> None:
    lines = [
        "⚠️ <b>AI-модерация: не удалось удалить сообщение</b>",
        f"Где: {await _scope_line(session, message)}",
        f"Кто: {_message_author(message)}",
        f"Причина: {escape(outcome.ai_reason or 'без пояснения')}",
        f"Ошибка Telegram: {escape(error)}",
        "",
        "Проверьте права бота «Удалять сообщения» и возраст сообщения (до 48 часов).",
    ]
    await _send_to_admins(message.bot, "\n".join(lines))


_delete_tasks: set[asyncio.Task[None]] = set()


async def _delete_later(bot: Bot, chat_id: int, message_id: int) -> None:
    await asyncio.sleep(get_settings().warn_ttl_seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass
