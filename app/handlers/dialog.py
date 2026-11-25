from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import repo
from app.db.models import Dialog, User
from app.filters.admin import IsAdmin, IsPrivateChat
from app.keyboards.admin import (
    back_menu,
    dialog_ai_instruction,
    dialog_cancel,
    dialog_controls,
    dialog_edit_instruction,
    dialog_mode_menu,
    dialog_peers,
    dialog_scope_menu,
    dialog_topic_chats,
    dialog_topic_topics,
    dialogs_menu,
)
from app.services.dialog_service import (
    VALID_MODES,
    dialog_created_text,
    notify_peer_dialog_started,
    peer_not_started_warning,
    start_dialog,
)
from app.services.openrouter import OpenRouterError, OpenRouterService
from app.utils.text import chunk_text, display_user, escape, fmt_dt

logger = logging.getLogger(__name__)
router = Router(name="dialogs")

PEERS_PER_PAGE = 8
INSTRUCTION_PREVIEW = 300

_ai_locks: dict[int, asyncio.Lock] = {}

# Content types that a text-only dialog cannot process itself.
PEER_MEDIA = F.photo | F.document | F.voice | F.video | F.video_note | F.audio


class NewDialogFlow(StatesGroup):
    target = State()
    instruction = State()


class EditInstructionFlow(StatesGroup):
    instruction = State()


class ActiveAdminRelayDialog(BaseFilter):
    async def __call__(self, message: Message, **data: Any) -> bool:
        session: AsyncSession | None = data.get("session")
        if session is None or message.from_user is None:
            return False
        dialog = await repo.get_active_dialog_for_admin(session, message.from_user.id)
        return dialog is not None and dialog.mode == "relay"


class ActivePeerRelayDialog(BaseFilter):
    async def __call__(self, message: Message, **data: Any) -> bool:
        session: AsyncSession | None = data.get("session")
        if session is None or message.from_user is None:
            return False
        dialog = await repo.get_active_dialog_for_peer(
            session, message.from_user.id, private_only=True
        )
        return dialog is not None and dialog.mode == "relay"


class ActivePeerAIDialog(BaseFilter):
    async def __call__(self, message: Message, **data: Any) -> bool:
        session: AsyncSession | None = data.get("session")
        if session is None or message.from_user is None:
            return False
        dialog = await repo.get_active_dialog_for_peer(
            session, message.from_user.id, private_only=True
        )
        return dialog is not None and dialog.mode == "ai"


class ActiveTopicAIDialog(BaseFilter):
    """Active AI dialog that runs in the topic this message was sent to."""

    async def __call__(self, message: Message, **data: Any) -> bool:
        session: AsyncSession | None = data.get("session")
        user = message.from_user
        if session is None or user is None or user.is_bot:
            return False
        thread_id = message.message_thread_id
        if thread_id is None:
            return False
        dialog = await repo.get_active_topic_dialog(session, message.chat.id, thread_id)
        return dialog is not None and dialog.mode == "ai" and dialog.peer_id == user.id


class ActiveTopicRelayDialog(BaseFilter):
    """Active relay dialog that runs in the topic this message was sent to."""

    async def __call__(self, message: Message, **data: Any) -> bool:
        session: AsyncSession | None = data.get("session")
        user = message.from_user
        if session is None or user is None or user.is_bot:
            return False
        thread_id = message.message_thread_id
        if thread_id is None:
            return False
        dialog = await repo.get_active_topic_dialog(session, message.chat.id, thread_id)
        return dialog is not None and dialog.mode == "relay" and dialog.peer_id == user.id


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@router.message(Command("dialog"), IsAdmin(), IsPrivateChat())
async def cmd_dialog_private(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "Использование: <code>/dialog 123456789 [relay|ai] [инструкция]</code>\n"
            "Также можно указать @username.\n\n"
            "Удобнее запускать диалоги кнопками: /menu → «💬 Диалоги»."
        )
        return

    target, rest = _split_target(raw)
    peer_id, error = await _resolve_peer(session, target)
    if error or peer_id is None:
        await message.answer(f"❌ {error or 'Пользователь не найден.'}")
        return

    mode = "relay"
    instruction: str | None = None
    if rest:
        mode_part, _, tail = rest.partition(" ")
        if mode_part.lower() in VALID_MODES:
            mode = mode_part.lower()
            instruction = tail.strip() or None
        else:
            instruction = rest.strip() or None

    dialog, peer, error = await _launch_dialog(
        session,
        admin_id=message.from_user.id,
        peer_id=peer_id,
        mode=mode,
        instruction=instruction,
    )
    if error or dialog is None or peer is None:
        await message.answer(f"❌ {error}")
        return

    await _send_created(message, dialog, peer)


@router.message(Command("dialogs"), IsAdmin(), IsPrivateChat())
async def cmd_dialogs(message: Message, session: AsyncSession) -> None:
    text, markup = await _dialogs_view(session, admin_id=message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.message(Command("stopdialog"), IsAdmin(), IsPrivateChat())
async def cmd_stopdialog(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
) -> None:
    raw = (command.args or "").strip()
    dialog: Dialog | None
    if raw:
        try:
            dialog_id = int(raw)
        except ValueError:
            await message.answer("Использование: <code>/stopdialog 12</code>")
            return
        dialog = await repo.get_dialog(session, dialog_id)
        if dialog is None or dialog.admin_id != message.from_user.id:
            await message.answer("Диалог не найден.")
            return
    else:
        dialog = await repo.get_active_dialog_for_admin(session, message.from_user.id)
        if dialog is None:
            await message.answer("У вас нет активного диалога.")
            return

    stopped = await repo.stop_dialog(session, dialog.id)
    if stopped is None:
        await message.answer("Диалог уже завершён.")
        return
    await repo.add_audit(
        session,
        action="dialog_stopped",
        actor_id=message.from_user.id,
        details=f"dialog={dialog.id}",
    )
    await _notify_peer_finished(message.bot, dialog)
    await message.answer(f"⛔ Диалог #{dialog.id} завершён.", reply_markup=back_menu())


# ---------------------------------------------------------------------------
# Button-driven dialog creation
# ---------------------------------------------------------------------------


@router.message(NewDialogFlow.target, IsAdmin(), IsPrivateChat(), F.text, ~F.text.startswith("/"))
async def new_dialog_target(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    mode = str(data.get("mode", "relay"))
    if mode not in VALID_MODES:
        await state.clear()
        await message.answer("Состояние сброшено. Начните заново.", reply_markup=back_menu())
        return

    raw = (message.text or "").strip()
    peer_id, error = await _resolve_peer(session, raw)
    if error or peer_id is None:
        await message.answer(
            f"❌ {error or 'Пользователь не найден.'}",
            reply_markup=dialog_cancel(),
        )
        return

    peer = await repo.get_user(session, peer_id)
    if peer is None:
        await state.clear()
        await message.answer("Пользователь не найден.", reply_markup=back_menu())
        return

    if mode == "ai":
        if not get_settings().openrouter_key:
            await state.clear()
            await message.answer(
                "❌ OPENROUTER_API_KEY не задан в .env — AI-режим недоступен.",
                reply_markup=back_menu(),
            )
            return
        await state.set_state(NewDialogFlow.instruction)
        await state.set_data({"mode": "ai", "peer_id": peer_id})
        await message.answer(
            _instruction_prompt(peer),
            reply_markup=dialog_ai_instruction(peer_id),
        )
        return

    dialog, peer, error = await _launch_dialog(
        session,
        admin_id=message.from_user.id,
        peer_id=peer_id,
        mode=mode,
        instruction=None,
    )
    await state.clear()
    if error or dialog is None or peer is None:
        await message.answer(f"❌ {error}", reply_markup=back_menu())
        return
    await _send_created(message, dialog, peer)


@router.message(
    NewDialogFlow.instruction, IsAdmin(), IsPrivateChat(), F.text, ~F.text.startswith("/")
)
async def new_dialog_instruction(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    try:
        peer_id = int(data["peer_id"])
        mode = str(data.get("mode", "ai"))
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer("Состояние сброшено. Начните заново.", reply_markup=back_menu())
        return
    chat_id = data.get("chat_id")
    thread_id = data.get("thread_id")

    instruction = (message.text or "").strip()
    if not instruction:
        await message.answer(
            "Отправьте задание текстом или нажмите «Без задания».",
            reply_markup=dialog_ai_instruction(peer_id),
        )
        return

    dialog, peer, error = await _launch_dialog(
        session,
        admin_id=message.from_user.id,
        peer_id=peer_id,
        mode=mode,
        instruction=instruction,
        chat_id=int(chat_id) if chat_id is not None else None,
        thread_id=int(thread_id) if thread_id is not None else None,
    )
    await state.clear()
    if error or dialog is None or peer is None:
        await message.answer(f"❌ {error}", reply_markup=back_menu())
        return
    await _send_created(message, dialog, peer)


@router.message(
    EditInstructionFlow.instruction,
    IsAdmin(),
    IsPrivateChat(),
    F.text,
    ~F.text.startswith("/"),
)
async def edit_instruction(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    try:
        dialog_id = int(data["dialog_id"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer("Состояние сброшено.", reply_markup=back_menu())
        return

    dialog = await repo.get_dialog(session, dialog_id)
    if dialog is None or dialog.mode != "ai":
        await state.clear()
        await message.answer("AI-диалог не найден.", reply_markup=back_menu())
        return

    instruction = (message.text or "").strip()
    if not instruction:
        await message.answer(
            "Отправьте задание текстом или нажмите «Без задания».",
            reply_markup=dialog_edit_instruction(dialog_id),
        )
        return

    await repo.set_dialog_instruction(session, dialog_id, instruction)
    await repo.add_audit(
        session,
        action="dialog_instruction_changed",
        actor_id=message.from_user.id if message.from_user else None,
        details=f"dialog={dialog_id}",
    )
    await state.clear()
    text, markup = await _dialog_card_view(session, dialog)
    await message.answer("✅ Задание для AI обновлено.")
    await message.answer(text, reply_markup=markup)


# ---------------------------------------------------------------------------
# Relay: admin -> peer
# ---------------------------------------------------------------------------


@router.message(
    IsAdmin(), IsPrivateChat(), F.text, ~F.text.startswith("/"), ActiveAdminRelayDialog()
)
async def relay_from_admin(message: Message, session: AsyncSession) -> None:
    dialog = await repo.get_active_dialog_for_admin(session, message.from_user.id)
    if dialog is None:
        return
    text = message.text or ""
    if not text.strip():
        return

    in_topic = dialog.chat_id is not None and dialog.thread_id is not None
    try:
        if in_topic:
            sent = await message.bot.send_message(
                dialog.chat_id,
                text,
                message_thread_id=dialog.thread_id,
                parse_mode=None,
            )
        else:
            sent = await message.bot.send_message(dialog.peer_id, text, parse_mode=None)
    except Exception as exc:  # noqa: BLE001 - Telegram errors vary
        logger.warning("Relay to peer %s failed: %s", dialog.peer_id, type(exc).__name__)
        await message.reply("❌ Не удалось доставить сообщение пользователю.")
        return

    await repo.add_dialog_message(
        session,
        dialog_id=dialog.id,
        direction="from_admin",
        text=text,
        tg_message_id=sent.message_id,
    )
    await message.reply("✅ Опубликовано в теме." if in_topic else "✅ Доставлено пользователю.")


# ---------------------------------------------------------------------------
# Relay: peer -> admin
# ---------------------------------------------------------------------------


@router.message(IsPrivateChat(), F.text, ~F.text.startswith("/"), ActivePeerRelayDialog())
async def relay_from_peer(message: Message, session: AsyncSession) -> None:
    dialog = await repo.get_active_dialog_for_peer(session, message.from_user.id, private_only=True)
    if dialog is None:
        return
    text = message.text or ""
    if not text.strip():
        return

    await repo.add_dialog_message(
        session,
        dialog_id=dialog.id,
        direction="from_peer",
        text=text,
        tg_message_id=message.message_id,
    )

    peer = message.from_user
    header = (
        f"💬 Ответ в диалоге #{dialog.id}\n"
        f"От: {display_user(peer.id, peer.username, peer.first_name)} "
        f"(<code>{peer.id}</code>)"
    )
    try:
        await message.bot.send_message(
            dialog.admin_id,
            f"{header}\n\n{escape(text)}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Relay to admin %s failed: %s", dialog.admin_id, type(exc).__name__)


# ---------------------------------------------------------------------------
# AI dialog
# ---------------------------------------------------------------------------


@router.message(IsPrivateChat(), F.text, ~F.text.startswith("/"), ActivePeerAIDialog())
async def ai_from_peer(message: Message, session: AsyncSession) -> None:
    dialog = await repo.get_active_dialog_for_peer(session, message.from_user.id, private_only=True)
    if dialog is None:
        return
    text = message.text or ""
    if not text.strip():
        return

    lock = _ai_locks.setdefault(dialog.id, asyncio.Lock())

    async with lock:
        await repo.add_dialog_message(
            session,
            dialog_id=dialog.id,
            direction="from_peer",
            text=text,
            tg_message_id=message.message_id,
        )

        settings = get_settings()
        history_rows = await repo.dialog_messages(session, dialog.id, limit=settings.ai_max_history)
        history = [(row.direction, row.text) for row in history_rows]
        # Release the SQLite transaction before the external network call.
        await session.commit()
        service = OpenRouterService(settings)

        try:
            async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
                answer = await service.reply(
                    instruction=dialog.instruction,
                    history=history,
                    model=dialog.model,
                )
        except OpenRouterError as exc:
            logger.warning("AI dialog %s failed: %s", dialog.id, exc)
            # Do not post technical errors to the peer: only the admin is notified.
            await _notify_admin_ai_error(message.bot, dialog, str(exc), peer_text=text)
            return

        sent = await message.answer(escape(answer))
        await repo.add_dialog_message(
            session,
            dialog_id=dialog.id,
            direction="from_ai",
            text=answer,
            tg_message_id=sent.message_id,
        )

        if dialog.admin_id != message.from_user.id:
            try:
                await message.bot.send_message(
                    dialog.admin_id,
                    f"🤖 AI-диалог #{dialog.id}\n"
                    f"👤 {display_user(message.from_user.id, message.from_user.username, message.from_user.first_name)}: "
                    f"{escape(text)}\n\n"
                    f"🤖 {escape(answer)}",
                )
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Non-text messages in private dialogs
# ---------------------------------------------------------------------------


@router.message(IsPrivateChat(), PEER_MEDIA, ActivePeerRelayDialog())
async def relay_media_from_peer(message: Message, session: AsyncSession) -> None:
    dialog = await repo.get_active_dialog_for_peer(session, message.from_user.id, private_only=True)
    if dialog is None:
        return
    kind = _media_kind(message)
    await repo.add_dialog_message(
        session,
        dialog_id=dialog.id,
        direction="from_peer",
        text=_media_history_text(message, kind),
        tg_message_id=message.message_id,
    )
    await _forward_media_to_admin(message, dialog, kind=kind)


@router.message(IsPrivateChat(), PEER_MEDIA, ActivePeerAIDialog())
async def ai_media_from_peer(message: Message, session: AsyncSession) -> None:
    dialog = await repo.get_active_dialog_for_peer(session, message.from_user.id, private_only=True)
    if dialog is None:
        return
    kind = _media_kind(message)
    await repo.add_dialog_message(
        session,
        dialog_id=dialog.id,
        direction="from_peer",
        text=_media_history_text(message, kind),
        tg_message_id=message.message_id,
    )
    # The bot stays silent in the chat; the admin receives the attachment.
    await _forward_media_to_admin(message, dialog, kind=kind)


# ---------------------------------------------------------------------------
# Topic dialogs: peer <-> bot inside a forum topic
# ---------------------------------------------------------------------------

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


@router.message(
    F.chat.type.in_(GROUP_TYPES),
    F.text,
    ~F.text.startswith("/"),
    ActiveTopicRelayDialog(),
)
async def topic_relay_from_peer(message: Message, session: AsyncSession) -> None:
    if message.message_thread_id is None or message.from_user is None:
        return
    dialog = await repo.get_active_topic_dialog(session, message.chat.id, message.message_thread_id)
    if dialog is None:
        return
    text = message.text or ""
    if not text.strip():
        return

    await repo.add_dialog_message(
        session,
        dialog_id=dialog.id,
        direction="from_peer",
        text=text,
        tg_message_id=message.message_id,
    )

    user = message.from_user
    scope = await _scope_label_by_ids(session, message.chat.id, message.message_thread_id)
    header = (
        f"💬 Ответ в диалоге #{dialog.id}\n"
        f"Где: {escape(scope)}\n"
        f"От: {display_user(user.id, user.username, user.first_name)} "
        f"(<code>{user.id}</code>)"
    )
    try:
        await message.bot.send_message(
            dialog.admin_id,
            f"{header}\n\n{escape(text)}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Relay to admin %s failed: %s", dialog.admin_id, type(exc).__name__)


@router.message(
    F.chat.type.in_(GROUP_TYPES),
    F.text,
    ~F.text.startswith("/"),
    ActiveTopicAIDialog(),
)
async def topic_ai_from_peer(message: Message, session: AsyncSession) -> None:
    if message.message_thread_id is None or message.from_user is None:
        return
    dialog = await repo.get_active_topic_dialog(session, message.chat.id, message.message_thread_id)
    if dialog is None:
        return
    text = message.text or ""
    if not text.strip():
        return

    lock = _ai_locks.setdefault(dialog.id, asyncio.Lock())

    async with lock:
        await repo.add_dialog_message(
            session,
            dialog_id=dialog.id,
            direction="from_peer",
            text=text,
            tg_message_id=message.message_id,
        )

        settings = get_settings()
        history_rows = await repo.dialog_messages(session, dialog.id, limit=settings.ai_max_history)
        history = [(row.direction, row.text) for row in history_rows]
        # Release the SQLite transaction before the external network call.
        await session.commit()
        service = OpenRouterService(settings)

        try:
            async with ChatActionSender.typing(
                bot=message.bot,
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
            ):
                answer = await service.reply(
                    instruction=dialog.instruction,
                    history=history,
                    model=dialog.model,
                )
        except OpenRouterError as exc:
            logger.warning("AI topic dialog %s failed: %s", dialog.id, exc)
            # Stay silent in the topic: the admin decides how to help.
            await _notify_admin_ai_error(message.bot, dialog, str(exc), peer_text=text)
            return

        sent = await message.reply(escape(answer))
        await repo.add_dialog_message(
            session,
            dialog_id=dialog.id,
            direction="from_ai",
            text=answer,
            tg_message_id=sent.message_id,
        )

        user = message.from_user
        scope = await _scope_label_by_ids(session, message.chat.id, message.message_thread_id)
        try:
            await message.bot.send_message(
                dialog.admin_id,
                f"🤖 AI-диалог #{dialog.id}\n"
                f"Где: {escape(scope)}\n"
                f"👤 {display_user(user.id, user.username, user.first_name)}: "
                f"{escape(text)}\n\n"
                f"🤖 {escape(answer)}",
            )
        except Exception:  # noqa: BLE001
            pass


@router.message(F.chat.type.in_(GROUP_TYPES), PEER_MEDIA, ActiveTopicRelayDialog())
async def topic_relay_media(message: Message, session: AsyncSession) -> None:
    if message.message_thread_id is None or message.from_user is None:
        return
    dialog = await repo.get_active_topic_dialog(session, message.chat.id, message.message_thread_id)
    if dialog is None:
        return
    kind = _media_kind(message)
    await repo.add_dialog_message(
        session,
        dialog_id=dialog.id,
        direction="from_peer",
        text=_media_history_text(message, kind),
        tg_message_id=message.message_id,
    )
    scope = await _scope_label_by_ids(session, message.chat.id, message.message_thread_id)
    await _forward_media_to_admin(message, dialog, scope=scope, kind=kind)


@router.message(F.chat.type.in_(GROUP_TYPES), PEER_MEDIA, ActiveTopicAIDialog())
async def topic_ai_media(message: Message, session: AsyncSession) -> None:
    if message.message_thread_id is None or message.from_user is None:
        return
    dialog = await repo.get_active_topic_dialog(session, message.chat.id, message.message_thread_id)
    if dialog is None:
        return
    kind = _media_kind(message)
    await repo.add_dialog_message(
        session,
        dialog_id=dialog.id,
        direction="from_peer",
        text=_media_history_text(message, kind),
        tg_message_id=message.message_id,
    )
    # No public reply in the topic: only the admin receives the attachment.
    scope = await _scope_label_by_ids(session, message.chat.id, message.message_thread_id)
    await _forward_media_to_admin(message, dialog, scope=scope, kind=kind)


@router.message(IsPrivateChat(), F.text, ~F.text.startswith("/"))
async def private_fallback(message: Message, session: AsyncSession) -> None:
    if message.from_user is not None and message.from_user.id in get_settings().admin_id_set:
        dialog = await repo.get_active_dialog_for_admin(session, message.from_user.id)
        if dialog is None:
            paused = await repo.list_active_dialogs(
                session, admin_id=message.from_user.id, include_paused=True
            )
            paused = [item for item in paused if item.status == "paused"]
            if paused:
                await message.answer(
                    f"⏸ Диалог #{paused[0].id} выключен. Включите его в /menu → «💬 Диалоги».",
                    reply_markup=dialog_controls(paused[0].id, paused[0].mode, paused[0].status),
                )
                return
        await message.answer("Используйте /menu для панели администратора.")
    else:
        await message.answer(
            "Я бот для связи с администратором. Ожидайте сообщения или нажмите /start."
        )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


async def _dialogs_view(
    session: AsyncSession, *, admin_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    dialogs = await repo.list_active_dialogs(session, admin_id=admin_id, include_paused=True)
    active_count = sum(1 for dialog in dialogs if dialog.status == "active")
    paused_count = sum(1 for dialog in dialogs if dialog.status == "paused")
    lines = ["<b>💬 Диалоги</b>", ""]
    if not dialogs:
        lines.append("Активных диалогов нет.")
    else:
        lines.append(f"Активных: {active_count}")
        if paused_count:
            lines.append(f"Выключено: {paused_count}")

    items: list[tuple[int, str]] = []
    for dialog in dialogs:
        peer = await repo.get_user(session, dialog.peer_id)
        target = _peer_label(dialog.peer_id, peer)
        mark = "🤖" if dialog.mode == "ai" else "💬"
        if dialog.status == "paused":
            mark = "⏸"
        items.append((dialog.id, f"{mark} #{dialog.id} · {target}"))
        status_text = ", выключен" if dialog.status == "paused" else ""
        scope = await _dialog_scope_label(session, dialog)
        scope_text = f", тема: {escape(scope)}" if scope else ""
        lines.append(
            f"• #{dialog.id} — {_mode_label(dialog.mode)}{status_text}{scope_text}, "
            f"{escape(target)} (<code>{dialog.peer_id}</code>), с {fmt_dt(dialog.created_at)}"
        )

    lines.append("")
    lines.append("Нажмите «➕ Новый диалог», чтобы выбрать пользователя и режим.")
    return "\n".join(lines), dialogs_menu(items)


async def _peers_view(
    session: AsyncSession,
    *,
    mode: str,
    page: int,
    chat_id: int | None = None,
    thread_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    page = max(page, 0)
    total = await repo.count_users_for_dialog(session)
    users = await repo.list_users_for_dialog(
        session, offset=page * PEERS_PER_PAGE, limit=PEERS_PER_PAGE + 1
    )
    has_next = len(users) > PEERS_PER_PAGE
    users = users[:PEERS_PER_PAGE]

    title = "🤖 AI-диалог" if mode == "ai" else "💬 Пересылка"
    lines = [f"<b>{title}</b>", ""]
    if chat_id is not None and thread_id is not None:
        scope = await _scope_label_by_ids(session, chat_id, thread_id)
        lines.append(f"Где: {escape(scope)}")
        lines.append("Кто будет собеседником в этой теме?")
    else:
        lines.append(f"С кем начать диалог? Пользователей в базе: {total}.")
    lines.append("")
    if not users:
        lines.append("Пользователей нет. Введите ID вручную или попросите написать боту /start.")
    elif chat_id is None:
        lines.append(
            "✅ — запускал бота, ⚠️ — ещё нет (диалог можно назначить, "
            "но бот не сможет написать первым)."
        )
    else:
        lines.append("Выберите участника, с которым бот будет вести диалог в теме.")
    return "\n".join(lines), dialog_peers(
        users,
        mode=mode,
        page=page,
        has_prev=page > 0,
        has_next=has_next,
        chat_id=chat_id,
        thread_id=thread_id,
    )


def _instruction_prompt(peer: User, *, scope: str | None = None) -> str:
    where = f"\nГде: {scope}" if scope else ""
    return (
        "<b>🤖 AI-диалог</b>\n\n"
        f"С кем: {escape(_peer_label(peer.id, peer))} (<code>{peer.id}</code>){where}\n\n"
        "Отправьте задание для AI одним сообщением: как отвечать пользователю, "
        "что можно и что нельзя.\n\n"
        "Можно нажать «Без задания» — тогда AI будет вежливым помощником по умолчанию."
    )


def _mode_label(mode: str) -> str:
    return "AI-диалог" if mode == "ai" else "пересылка"


def _peer_label(peer_id: int, peer: User | None) -> str:
    if peer is None:
        return str(peer_id)
    if peer.username:
        return f"@{peer.username}"
    name = " ".join(part for part in (peer.first_name, peer.last_name) if part)
    return name or str(peer_id)


async def _scope_label_by_ids(
    session: AsyncSession,
    chat_id: int,
    thread_id: int,
) -> str:
    chat = await repo.get_chat(session, chat_id)
    topic = await repo.get_topic(session, chat_id, thread_id)
    chat_title = chat.title if chat is not None and chat.title else str(chat_id)
    topic_title = topic.title if topic is not None and topic.title else f"Тема {thread_id}"
    return f"{chat_title} → {topic_title} (thread {thread_id})"


async def _dialog_scope_label(session: AsyncSession, dialog: Dialog) -> str | None:
    if dialog.chat_id is None or dialog.thread_id is None:
        return None
    return await _scope_label_by_ids(session, dialog.chat_id, dialog.thread_id)


def _dialog_card_text(
    dialog: Dialog,
    peer: User | None,
    *,
    scope: str | None = None,
) -> str:
    target = display_user(
        dialog.peer_id,
        peer.username if peer else None,
        peer.first_name if peer else None,
    )
    lines = [
        f"<b>💬 Диалог #{dialog.id}</b>",
        f"Пользователь: {target} (<code>{dialog.peer_id}</code>)",
        f"Статус: {'⏸ выключен' if dialog.status == 'paused' else '▶️ включён'}",
        ("Режим: 🤖 AI — отвечает бот" if dialog.mode == "ai" else "Режим: 💬 пересылка сообщений"),
    ]
    if scope:
        lines.append(f"Где: {escape(scope)}")
    lines.append(f"Начат: {fmt_dt(dialog.created_at)}")
    if dialog.status == "paused":
        lines.append("")
        lines.append(
            "Диалог выключен: сообщения не пересылаются и AI не отвечает. "
            "Нажмите «▶️ Включить диалог», чтобы продолжить."
        )
    if dialog.mode == "ai":
        if dialog.instruction:
            lines.append(f"Задание AI: {escape(_preview(dialog.instruction))}")
        else:
            lines.append("Задание AI: по умолчанию (вежливый помощник)")
        model = dialog.model or get_settings().openrouter_model
        lines.append(f"Модель: <code>{escape(model)}</code>")
        lines.append("")
        if scope:
            lines.append(
                "Бот отвечает в теме только выбранному собеседнику. "
                "«✏️ Задание для AI» меняет инструкцию."
            )
        else:
            lines.append("Нажмите «✏️ Задание для AI», чтобы изменить инструкцию.")
    elif scope:
        lines.append("")
        lines.append(
            "Пишите сюда — бот публикует сообщение в теме. Ответы собеседника приходят вам сюда."
        )
    else:
        lines.append("")
        lines.append("Пишите сюда — сообщения получает пользователь, ответы приходят вам.")
    return "\n".join(lines)


async def _dialog_card_view(
    session: AsyncSession, dialog: Dialog
) -> tuple[str, InlineKeyboardMarkup]:
    peer = await repo.get_user(session, dialog.peer_id)
    scope = await _dialog_scope_label(session, dialog)
    return _dialog_card_text(dialog, peer, scope=scope), dialog_controls(
        dialog.id, dialog.mode, dialog.status
    )


async def _launch_dialog(
    session: AsyncSession,
    *,
    admin_id: int,
    peer_id: int,
    mode: str,
    instruction: str | None,
    chat_id: int | None = None,
    thread_id: int | None = None,
) -> tuple[Dialog | None, User | None, str | None]:
    dialog, error = await start_dialog(
        session,
        get_settings(),
        admin_id=admin_id,
        peer_id=peer_id,
        mode=mode,
        instruction=instruction,
        model=None,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    if error or dialog is None:
        return None, None, error or "Не удалось начать диалог."
    peer = await repo.get_user(session, peer_id)
    return dialog, peer, None


async def _send_created(message: Message, dialog: Dialog, peer: User) -> None:
    await notify_peer_dialog_started(message.bot, dialog)
    await message.answer(
        dialog_created_text(dialog, peer),
        reply_markup=dialog_controls(dialog.id, dialog.mode, dialog.status),
    )


async def _edit_created(
    callback: CallbackQuery,
    dialog: Dialog,
    peer: User,
    *,
    warning: str | None = None,
) -> None:
    await notify_peer_dialog_started(callback.bot, dialog)
    await _edit(
        callback,
        dialog_created_text(dialog, peer),
        dialog_controls(dialog.id, dialog.mode, dialog.status),
    )
    if warning is not None:
        await callback.answer(f"⚠️ {warning}", show_alert=True)
    else:
        await callback.answer("Диалог начат")


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "menu:dialogs", IsAdmin())
async def cb_dialogs(callback: CallbackQuery, session: AsyncSession) -> None:
    text, markup = await _dialogs_view(session, admin_id=callback.from_user.id)
    await _edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data == "dialog:new", IsAdmin())
async def cb_dialog_new(callback: CallbackQuery) -> None:
    await _edit(
        callback,
        "<b>➕ Новый диалог</b>\n\n"
        "Выберите режим:\n"
        "🤖 <b>AI-диалог</b> — бот сам отвечает пользователю по вашему заданию.\n"
        "💬 <b>Пересылка</b> — вы пишете пользователю, ответы приходят сюда.\n\n"
        "Новый диалог заменяет текущий активный диалог.",
        dialog_mode_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dialog:new:"), IsAdmin())
async def cb_dialog_new_mode(callback: CallbackQuery) -> None:
    parsed = _mode_and_page(callback.data)
    if parsed is None:
        await callback.answer("Некорректный режим", show_alert=True)
        return
    mode, _page = parsed
    if mode == "ai" and not get_settings().openrouter_key:
        await callback.answer("OPENROUTER_API_KEY не задан — AI-режим недоступен.", show_alert=True)
        return
    await _edit(
        callback,
        "<b>➕ Новый диалог</b>\n\n"
        "Где вести диалог?\n"
        "👤 <b>В личке</b> — бот общается с пользователем в личных сообщениях.\n"
        "🗂 <b>В теме чата</b> — бот отвечает выбранному участнику прямо в теме.",
        dialog_scope_menu(mode),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dialog:scope:"), IsAdmin())
async def cb_dialog_scope(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in VALID_MODES:
        await callback.answer("Некорректный режим", show_alert=True)
        return
    mode, kind = parts[2], parts[3]
    if mode == "ai" and not get_settings().openrouter_key:
        await callback.answer("OPENROUTER_API_KEY не задан — AI-режим недоступен.", show_alert=True)
        return

    if kind == "private":
        text, markup = await _peers_view(session, mode=mode, page=0)
        await _edit(callback, text, markup)
        await callback.answer()
        return

    if kind != "topic":
        await callback.answer("Некорректный выбор", show_alert=True)
        return
    chats = await repo.list_chats(session)
    if not chats:
        await callback.answer(
            "Чатов в базе нет. Сначала напишите сообщение в чат или тему.",
            show_alert=True,
        )
        return
    lines = [
        "<b>🗂 Диалог в теме</b>",
        "",
        "Выберите чат, в теме которого бот будет вести диалог:",
    ]
    await _edit(callback, "\n".join(lines), dialog_topic_chats(chats, mode=mode))
    await callback.answer()


@router.callback_query(F.data.startswith("dialog:peers:"), IsAdmin())
async def cb_dialog_peers_page(callback: CallbackQuery, session: AsyncSession) -> None:
    parsed = _mode_and_page(callback.data)
    if parsed is None:
        await callback.answer("Некорректный режим", show_alert=True)
        return
    mode, page = parsed
    text, markup = await _peers_view(session, mode=mode, page=page)
    await _edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith("dialog:tpage:"), IsAdmin())
async def cb_dialog_topic_page(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 6 or parts[2] not in VALID_MODES:
        await callback.answer("Некорректная страница", show_alert=True)
        return
    mode = parts[2]
    try:
        chat_id = int(parts[3])
        thread_id = int(parts[4])
        page = max(0, int(parts[5]))
    except ValueError:
        await callback.answer("Некорректная страница", show_alert=True)
        return
    text, markup = await _peers_view(
        session, mode=mode, page=page, chat_id=chat_id, thread_id=thread_id
    )
    await _edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith("dialog:tchat:"), IsAdmin())
async def cb_dialog_topic_chat(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in VALID_MODES:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    mode = parts[2]
    try:
        chat_id = int(parts[3])
    except ValueError:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return
    topics = await repo.list_topics(session, chat_id)
    if not topics:
        await callback.answer(
            "В этом чате ещё нет известных тем. Напишите сообщение в нужную тему.",
            show_alert=True,
        )
        return
    chat_title = chat.title or str(chat.id)
    lines = [
        "<b>🗂 Диалог в теме</b>",
        "",
        f"Чат: {escape(chat_title)}",
        "Выберите тему:",
    ]
    await _edit(
        callback,
        "\n".join(lines),
        dialog_topic_topics(topics, mode=mode, chat_id=chat_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dialog:ttopic:"), IsAdmin())
async def cb_dialog_topic_topic(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[2] not in VALID_MODES:
        await callback.answer("Некорректная тема", show_alert=True)
        return
    mode = parts[2]
    try:
        chat_id = int(parts[3])
        thread_id = int(parts[4])
    except ValueError:
        await callback.answer("Некорректная тема", show_alert=True)
        return
    topic = await repo.get_topic(session, chat_id, thread_id)
    if topic is None:
        await callback.answer("Тема не найдена", show_alert=True)
        return
    text, markup = await _peers_view(
        session, mode=mode, page=0, chat_id=chat_id, thread_id=thread_id
    )
    await _edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith("dialog:manual:"), IsAdmin())
async def cb_dialog_manual(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or "").split(":")
    mode = parts[2] if len(parts) == 3 else ""
    if mode not in VALID_MODES:
        await callback.answer("Некорректный режим", show_alert=True)
        return
    if mode == "ai" and not get_settings().openrouter_key:
        await callback.answer("OPENROUTER_API_KEY не задан — AI-режим недоступен.", show_alert=True)
        return
    await state.set_state(NewDialogFlow.target)
    await state.set_data({"mode": mode})
    await _edit(
        callback,
        "Отправьте ID или @username пользователя одним сообщением.\n\n"
        "Пользователь должен быть в базе бота. Если он ещё не нажимал /start, "
        "диалог всё равно назначится, но бот не сможет написать ему первым.",
        dialog_cancel(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dialog:peer:"), IsAdmin())
async def cb_dialog_peer(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in VALID_MODES:
        await callback.answer("Некорректный пользователь", show_alert=True)
        return
    mode = parts[2]
    try:
        peer_id = int(parts[3])
    except ValueError:
        await callback.answer("Некорректный пользователь", show_alert=True)
        return

    peer = await repo.get_user(session, peer_id)
    if peer is None or peer.is_bot:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    warning = None if peer.started_bot else peer_not_started_warning()

    if mode == "ai":
        if not get_settings().openrouter_key:
            await callback.answer(
                "OPENROUTER_API_KEY не задан — AI-режим недоступен.", show_alert=True
            )
            return
        await state.set_state(NewDialogFlow.instruction)
        await state.set_data({"mode": "ai", "peer_id": peer_id})
        await _edit(callback, _instruction_prompt(peer), dialog_ai_instruction(peer_id))
        if warning is not None:
            await callback.answer(f"⚠️ {warning}", show_alert=True)
        else:
            await callback.answer()
        return

    dialog, peer, error = await _launch_dialog(
        session,
        admin_id=callback.from_user.id,
        peer_id=peer_id,
        mode=mode,
        instruction=None,
    )
    if error or dialog is None or peer is None:
        await callback.answer(error or "Не удалось начать диалог", show_alert=True)
        return
    await _edit_created(callback, dialog, peer, warning=warning)


@router.callback_query(F.data.startswith("dialog:peerT:"), IsAdmin())
async def cb_dialog_peer_topic(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 6 or parts[2] not in VALID_MODES:
        await callback.answer("Некорректный пользователь", show_alert=True)
        return
    mode = parts[2]
    try:
        chat_id = int(parts[3])
        thread_id = int(parts[4])
        peer_id = int(parts[5])
    except ValueError:
        await callback.answer("Некорректный пользователь", show_alert=True)
        return

    topic = await repo.get_topic(session, chat_id, thread_id)
    if topic is None:
        await callback.answer("Тема не найдена", show_alert=True)
        return
    peer = await repo.get_user(session, peer_id)
    if peer is None or peer.is_bot:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    scope = await _scope_label_by_ids(session, chat_id, thread_id)
    if mode == "ai":
        if not get_settings().openrouter_key:
            await callback.answer(
                "OPENROUTER_API_KEY не задан — AI-режим недоступен.", show_alert=True
            )
            return
        await state.set_state(NewDialogFlow.instruction)
        await state.set_data(
            {
                "mode": "ai",
                "peer_id": peer_id,
                "chat_id": chat_id,
                "thread_id": thread_id,
            }
        )
        await _edit(
            callback,
            _instruction_prompt(peer, scope=scope),
            dialog_ai_instruction(peer_id),
        )
        await callback.answer()
        return

    dialog, peer, error = await _launch_dialog(
        session,
        admin_id=callback.from_user.id,
        peer_id=peer_id,
        mode=mode,
        instruction=None,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    if error or dialog is None or peer is None:
        await callback.answer(error or "Не удалось начать диалог", show_alert=True)
        return
    await _edit_created(callback, dialog, peer)


@router.callback_query(F.data.startswith("dialog:skipinstr:"), IsAdmin())
async def cb_dialog_skip_instruction(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    peer_id = _int_tail(callback.data)
    if peer_id is None:
        await callback.answer("Некорректный пользователь", show_alert=True)
        return
    data = await state.get_data()
    chat_id = data.get("chat_id")
    thread_id = data.get("thread_id")
    await state.clear()
    dialog, peer, error = await _launch_dialog(
        session,
        admin_id=callback.from_user.id,
        peer_id=peer_id,
        mode="ai",
        instruction=None,
        chat_id=int(chat_id) if chat_id is not None else None,
        thread_id=int(thread_id) if thread_id is not None else None,
    )
    if error or dialog is None or peer is None:
        await callback.answer(error or "Не удалось начать диалог", show_alert=True)
        return
    await _edit_created(callback, dialog, peer)


@router.callback_query(F.data.startswith("dialog:open:"), IsAdmin())
async def cb_dialog_open(callback: CallbackQuery, session: AsyncSession) -> None:
    dialog = await _owned_dialog(callback, session)
    if dialog is None:
        return
    text, markup = await _dialog_card_view(session, dialog)
    await _edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith("dialog:instrclear:"), IsAdmin())
async def cb_dialog_instruction_clear(callback: CallbackQuery, session: AsyncSession) -> None:
    dialog = await _owned_dialog(callback, session)
    if dialog is None:
        return
    if dialog.mode != "ai":
        await callback.answer("Кнопка доступна только для AI-диалога", show_alert=True)
        return
    await repo.set_dialog_instruction(session, dialog.id, None)
    await repo.add_audit(
        session,
        action="dialog_instruction_changed",
        actor_id=callback.from_user.id,
        details=f"dialog={dialog.id} instruction=default",
    )
    text, markup = await _dialog_card_view(session, dialog)
    await _edit(callback, text, markup)
    await callback.answer("Задание сброшено")


@router.callback_query(F.data.startswith("dialog:instr:"), IsAdmin())
async def cb_dialog_instruction(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    dialog = await _owned_dialog(callback, session)
    if dialog is None:
        return
    if dialog.mode != "ai":
        await callback.answer("Кнопка доступна только для AI-диалога", show_alert=True)
        return
    await state.set_state(EditInstructionFlow.instruction)
    await state.set_data({"dialog_id": dialog.id})
    current = (
        f"Текущее задание: {escape(_preview(dialog.instruction))}\n\n" if dialog.instruction else ""
    )
    await _edit(
        callback,
        f"<b>✏️ Задание для AI</b>\n\n{current}"
        "Отправьте новое задание одним сообщением. Оно заменит текущее.",
        dialog_edit_instruction(dialog.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dialog:stop:"), IsAdmin())
async def cb_dialog_stop(callback: CallbackQuery, session: AsyncSession) -> None:
    dialog = await _owned_dialog(callback, session)
    if dialog is None:
        return
    stopped = await repo.stop_dialog(session, dialog.id)
    if stopped is None:
        await callback.answer("Диалог уже завершён.", show_alert=True)
        return
    await repo.add_audit(
        session,
        action="dialog_stopped",
        actor_id=callback.from_user.id,
        details=f"dialog={dialog.id}",
    )
    await _notify_peer_finished(callback.bot, dialog)
    await _edit(
        callback,
        f"⛔ Диалог #{dialog.id} завершён.",
        dialogs_menu([]),
    )
    await callback.answer("Завершён")


@router.callback_query(F.data.startswith("dialog:pause:"), IsAdmin())
async def cb_dialog_pause(callback: CallbackQuery, session: AsyncSession) -> None:
    dialog = await _owned_dialog(callback, session)
    if dialog is None:
        return
    paused = await repo.pause_dialog(session, dialog.id)
    if paused is None:
        await callback.answer("Диалог уже выключен или завершён.", show_alert=True)
        return
    await repo.add_audit(
        session,
        action="dialog_paused",
        actor_id=callback.from_user.id,
        details=f"dialog={dialog.id}",
    )
    await _notify_peer_status(callback.bot, dialog, paused=True)
    text, markup = await _dialog_card_view(session, paused)
    await _edit(callback, text, markup)
    await callback.answer("Диалог выключен")


@router.callback_query(F.data.startswith("dialog:resume:"), IsAdmin())
async def cb_dialog_resume(callback: CallbackQuery, session: AsyncSession) -> None:
    dialog = await _owned_dialog(callback, session)
    if dialog is None:
        return
    resumed = await repo.resume_dialog(session, dialog.id)
    if resumed is None:
        await callback.answer("Диалог уже включён или завершён.", show_alert=True)
        return
    await repo.add_audit(
        session,
        action="dialog_resumed",
        actor_id=callback.from_user.id,
        details=f"dialog={dialog.id}",
    )
    await _notify_peer_status(callback.bot, dialog, paused=False)
    text, markup = await _dialog_card_view(session, resumed)
    await _edit(callback, text, markup)
    await callback.answer("Диалог включён")


@router.callback_query(F.data.startswith("dialog:switch:"), IsAdmin())
async def cb_dialog_switch(callback: CallbackQuery, session: AsyncSession) -> None:
    dialog = await _owned_dialog(callback, session)
    if dialog is None:
        return
    new_mode = "ai" if dialog.mode == "relay" else "relay"
    if new_mode == "ai" and not get_settings().openrouter_key:
        await callback.answer("OPENROUTER_API_KEY не задан — AI-режим недоступен.", show_alert=True)
        return
    updated = await repo.set_dialog_mode(session, dialog.id, new_mode)
    if updated is None:
        await callback.answer("Диалог не найден.", show_alert=True)
        return
    await repo.add_audit(
        session,
        action="dialog_mode_changed",
        actor_id=callback.from_user.id,
        details=f"dialog={dialog.id} mode={new_mode}",
    )
    text, markup = await _dialog_card_view(session, updated)
    await _edit(callback, text, markup)
    await callback.answer("Режим изменён")


@router.callback_query(F.data.startswith("dialog:history:"), IsAdmin())
async def cb_dialog_history(callback: CallbackQuery, session: AsyncSession) -> None:
    dialog = await _owned_dialog(callback, session)
    if dialog is None:
        return
    rows = await repo.dialog_messages(session, dialog.id, limit=40)
    lines = [f"<b>📜 История диалога #{dialog.id}</b>", ""]
    labels = {"from_peer": "👤 Пользователь", "from_ai": "🤖 AI", "from_admin": "🛡 Админ"}
    for row in rows:
        text = row.text if len(row.text) <= 500 else row.text[:500] + "…"
        lines.append(f"{labels.get(row.direction, row.direction)}: {escape(text)}")
    if not rows:
        lines.append("История пуста.")
    if isinstance(callback.message, Message):
        for chunk in chunk_text("\n".join(lines)):
            await callback.message.answer(chunk)
    await callback.answer()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_target(raw: str) -> tuple[str, str]:
    parts = raw.split(maxsplit=1)
    return parts[0], parts[1] if len(parts) > 1 else ""


async def _resolve_peer(session: AsyncSession, target: str) -> tuple[int | None, str | None]:
    if target.startswith("@"):
        user = await repo.find_user_by_username(session, target)
        if user is None:
            return None, "Пользователь с таким username не найден в базе."
        if user.is_bot:
            return None, "Нельзя начать диалог с ботом."
        return user.id, None
    try:
        user_id = int(target)
    except ValueError:
        return None, "Нужен числовой ID или @username."
    user = await repo.get_user(session, user_id)
    if user is None:
        return None, "Пользователь с таким ID не найден в базе."
    if user.is_bot:
        return None, "Нельзя начать диалог с ботом."
    return user.id, None


def _preview(text: str, limit: int = INSTRUCTION_PREVIEW) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _mode_and_page(data: str | None) -> tuple[str, int] | None:
    parts = (data or "").split(":")
    if len(parts) != 4 or parts[2] not in VALID_MODES:
        return None
    try:
        page = max(0, int(parts[3]))
    except ValueError:
        return None
    return parts[2], page


def _int_tail(data: str | None) -> int | None:
    parts = (data or "").split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[-1])
    except ValueError:
        return None


async def _owned_dialog(callback: CallbackQuery, session: AsyncSession) -> Dialog | None:
    dialog_id = _int_tail(callback.data)
    if dialog_id is None:
        await callback.answer("Некорректный диалог", show_alert=True)
        return None
    dialog = await repo.get_dialog(session, dialog_id)
    if dialog is None:
        await callback.answer("Диалог не найден", show_alert=True)
        return None
    if (
        dialog.admin_id != callback.from_user.id
        and callback.from_user.id not in get_settings().admin_id_set
    ):
        await callback.answer("Нет доступа", show_alert=True)
        return None
    return dialog


def _media_kind(message: Message) -> str:
    if message.photo:
        return "фото"
    if message.video:
        return "видео"
    if message.video_note:
        return "видеосообщение"
    if message.voice:
        return "голосовое сообщение"
    if message.audio:
        return "аудио"
    if message.document:
        return "файл"
    if message.sticker:
        return "стикер"
    if message.animation:
        return "GIF"
    return "вложение"


def _media_history_text(message: Message, kind: str) -> str:
    caption = (message.caption or "").strip()
    return f"[{kind}] {caption}" if caption else f"[{kind}]"


async def _forward_media_to_admin(
    message: Message,
    dialog: Dialog,
    *,
    scope: str | None = None,
    kind: str | None = None,
) -> None:
    """Deliver a peer's non-text message to the admin as a copy."""
    peer = message.from_user
    lines = [f"💬 Вложение в диалоге #{dialog.id}"]
    if scope:
        lines.append(f"Где: {escape(scope)}")
    if peer is not None:
        lines.append(
            f"От: {display_user(peer.id, peer.username, peer.first_name)} (<code>{peer.id}</code>)"
        )
    lines.append(f"Тип: {kind or _media_kind(message)}")
    header = "\n".join(lines)
    try:
        await message.bot.send_message(dialog.admin_id, header)
        await message.bot.copy_message(
            chat_id=dialog.admin_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Cannot copy media in dialog %s to admin: %s",
            dialog.id,
            type(exc).__name__,
        )
        try:
            await message.bot.send_message(
                dialog.admin_id,
                f"{header}\n\n⚠️ Не удалось переслать вложение.",
            )
        except Exception:  # noqa: BLE001
            pass


async def _notify_peer_finished(bot: object, dialog: Dialog) -> None:
    if dialog.chat_id is not None or dialog.peer_id == dialog.admin_id:
        # Topic dialogs are public; self-dialogs are tests.
        return
    try:
        await bot.send_message(dialog.peer_id, "ℹ️ Диалог с администратором завершён.")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


async def _notify_peer_status(bot: object, dialog: Dialog, *, paused: bool) -> None:
    if dialog.chat_id is not None or dialog.peer_id == dialog.admin_id:
        return
    text = (
        "ℹ️ Администратор приостановил диалог."
        if paused
        else "ℹ️ Диалог с администратором возобновлён."
    )
    try:
        await bot.send_message(dialog.peer_id, text)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


async def _notify_admin_ai_error(
    bot: object,
    dialog: Dialog,
    error: str,
    *,
    peer_text: str | None = None,
) -> None:
    lines = [f"⚠️ Ошибка AI в диалоге #{dialog.id}"]
    if dialog.chat_id is not None and dialog.thread_id is not None:
        lines.append(
            f"Где: чат <code>{dialog.chat_id}</code> → тема <code>{dialog.thread_id}</code>"
        )
    if peer_text:
        lines.append(f"👤 {escape(_preview(peer_text, 500))}")
    lines.append(f"Ошибка: {escape(error)}")
    lines.append("")
    lines.append("В чате бот ничего не написал.")
    try:
        await bot.send_message(dialog.admin_id, "\n".join(lines))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


async def _edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as exc:
            if "message is not modified" not in exc.message:
                await message.answer(text, reply_markup=markup)
