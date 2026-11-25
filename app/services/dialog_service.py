from __future__ import annotations

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.db.models import Dialog, User
from app.utils.text import escape

VALID_MODES = {"relay", "ai"}


async def start_dialog(
    session: AsyncSession,
    settings: Settings,
    *,
    admin_id: int,
    peer_id: int,
    mode: str,
    instruction: str | None,
    model: str | None = None,
    chat_id: int | None = None,
    thread_id: int | None = None,
) -> tuple[Dialog | None, str | None]:
    mode = mode.lower().strip()
    if mode not in VALID_MODES:
        return None, "Режим должен быть relay или ai."
    if peer_id == admin_id and chat_id is None and mode != "ai":
        return None, (
            "Диалог с самим собой в личке в режиме пересылки не имеет смысла. "
            "Для испытания выберите AI-режим или диалог в теме чата."
        )
    if (chat_id is None) != (thread_id is None):
        return None, "Для диалога в теме нужны и чат, и тема."

    peer = await repo.get_user(session, peer_id)
    if peer is None:
        return None, "Пользователь не найден в базе. Он должен написать в чат или нажать /start."
    if peer.is_bot:
        return None, "Нельзя начать диалог с ботом."
    if mode == "ai" and not settings.openrouter_key:
        return None, "OPENROUTER_API_KEY не задан в .env — AI-режим недоступен."

    if chat_id is not None and thread_id is not None:
        chat = await repo.get_chat(session, chat_id)
        topic = await repo.get_topic(session, chat_id, thread_id)
        if chat is None or topic is None:
            return None, (
                "Тема не найдена в базе. Напишите любое сообщение в нужную тему и попробуйте снова."
            )

    dialog = await repo.create_dialog(
        session,
        admin_id=admin_id,
        peer_id=peer_id,
        mode=mode,
        instruction=instruction,
        model=model,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    await repo.add_audit(
        session,
        action="dialog_started",
        actor_id=admin_id,
        details=f"dialog={dialog.id} peer={peer_id} mode={mode}",
    )
    return dialog, None


def peer_not_started_warning() -> str:
    return (
        "Пользователь ещё не нажимал /start, поэтому бот не может написать ему первым. "
        "Диалог назначен и начнёт работать, как только пользователь откроет бота."
    )


def dialog_created_text(dialog: Dialog, peer: User) -> str:
    target = f"@{peer.username}" if peer.username else str(peer.id)
    mode_text = "пересылка сообщений" if dialog.mode == "relay" else "AI-диалог"
    lines = [
        f"💬 <b>Диалог #{dialog.id} начат</b>",
        f"Пользователь: {target} (<code>{peer.id}</code>)",
        f"Режим: {mode_text}",
    ]
    in_topic = dialog.chat_id is not None and dialog.thread_id is not None
    if dialog.peer_id == dialog.admin_id:
        lines.append("")
        lines.append("🧪 Тестовый диалог с самим собой: ответы бота будут приходить вам же.")
    if in_topic:
        lines.append(
            f"Где: чат <code>{dialog.chat_id}</code> → тема <code>{dialog.thread_id}</code>"
        )
    if not peer.started_bot:
        lines.append("")
        lines.append(f"⚠️ {escape(peer_not_started_warning())}")
    if in_topic and dialog.mode == "relay":
        lines.append("")
        lines.append(
            "Пишите сюда — бот опубликует сообщение в теме. "
            "Ответы собеседника из темы придут вам в личку."
        )
    elif in_topic:
        lines.append("")
        lines.append(
            "Бот отвечает в теме только назначенному собеседнику. "
            "Остальные сообщения в теме он не трогает."
        )
    elif dialog.mode == "relay":
        lines.append("Пишите сюда — сообщения будут доставлены пользователю.")
    else:
        lines.append("Бот будет отвечать пользователю автоматически.")
    if dialog.mode == "ai" and dialog.instruction:
        lines.append(f"Инструкция: {escape(dialog.instruction)}")
    return "\n".join(lines)


async def notify_peer_dialog_started(bot: Bot, dialog: Dialog) -> None:
    if dialog.chat_id is not None or dialog.peer_id == dialog.admin_id:
        # Topic dialogs live in a public topic; self-dialogs are tests.
        return
    try:
        await bot.send_message(
            dialog.peer_id,
            "ℹ️ С вами начал диалог администратор. Сообщения в этом чате получает администратор.",
        )
    except Exception:
        # The user may have blocked the bot; the admin sees send errors later.
        pass
