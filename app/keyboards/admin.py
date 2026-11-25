from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import Chat, Topic, User

BUTTON_TEXT_LIMIT = 48


def _short(text: str, limit: int = BUTTON_TEXT_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _peer_label(user: User) -> str:
    if user.username:
        return f"@{user.username}"
    name = " ".join(part for part in (user.first_name, user.last_name) if part)
    return name or str(user.id)


def _chat_title(chat: Chat) -> str:
    return chat.title or str(chat.id)


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="menu:stats"),
        InlineKeyboardButton(text="📋 Аудит", callback_data="menu:audit"),
    )
    builder.row(InlineKeyboardButton(text="🗂 Чаты и темы", callback_data="menu:chats:0"))
    builder.row(InlineKeyboardButton(text="🚫 Слова-фильтры", callback_data="menu:words"))
    builder.row(InlineKeyboardButton(text="🎯 Правила по темам", callback_data="menu:rules"))
    builder.row(InlineKeyboardButton(text="🤖 AI-модерация", callback_data="menu:airules"))
    builder.row(
        InlineKeyboardButton(text="👥 Пользователи", callback_data="menu:users:0"),
        InlineKeyboardButton(text="💬 Диалоги", callback_data="menu:dialogs"),
    )
    builder.row(InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help"))
    builder.row(InlineKeyboardButton(text="⏹ Остановить бота", callback_data="menu:stop"))
    return builder.as_markup()


def stop_confirm() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, остановить", callback_data="stopbot:confirm"),
        InlineKeyboardButton(text="⬅️ Отмена", callback_data="menu:main"),
    )
    return builder.as_markup()


def back_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main"))
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


def dialogs_menu(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Новый диалог", callback_data="dialog:new"))
    for dialog_id, label in items:
        builder.row(
            InlineKeyboardButton(
                text=_short(label),
                callback_data=f"dialog:open:{dialog_id}",
            )
        )
    builder.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main"))
    return builder.as_markup()


def dialog_mode_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🤖 AI-диалог", callback_data="dialog:new:ai:0"))
    builder.row(InlineKeyboardButton(text="💬 Пересылка", callback_data="dialog:new:relay:0"))
    builder.row(InlineKeyboardButton(text="⬅️ К диалогам", callback_data="menu:dialogs"))
    return builder.as_markup()


def dialog_scope_menu(mode: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="👤 В личке с пользователем",
            callback_data=f"dialog:scope:{mode}:private",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🗂 В теме чата",
            callback_data=f"dialog:scope:{mode}:topic",
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="dialog:new"))
    return builder.as_markup()


def dialog_topic_chats(chats: list[Chat], *, mode: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for chat in chats:
        builder.row(
            InlineKeyboardButton(
                text=_short(f"🗂 {_chat_title(chat)}"),
                callback_data=f"dialog:tchat:{mode}:{chat.id}",
            )
        )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"dialog:scope:{mode}:topic"))
    return builder.as_markup()


def dialog_topic_topics(topics: list[Topic], *, mode: str, chat_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for topic in topics[:30]:
        label = topic.title or f"Тема {topic.thread_id}"
        builder.row(
            InlineKeyboardButton(
                text=_short(label),
                callback_data=f"dialog:ttopic:{mode}:{chat_id}:{topic.thread_id}",
            )
        )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"dialog:scope:{mode}:topic"))
    return builder.as_markup()


def dialog_peers(
    users: list[User],
    *,
    mode: str,
    page: int,
    has_prev: bool,
    has_next: bool,
    chat_id: int | None = None,
    thread_id: int | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    in_topic = chat_id is not None and thread_id is not None
    for user in users:
        mark = "✅" if user.started_bot else "⚠️"
        callback = (
            f"dialog:peerT:{mode}:{chat_id}:{thread_id}:{user.id}"
            if in_topic
            else f"dialog:peer:{mode}:{user.id}"
        )
        builder.row(
            InlineKeyboardButton(
                text=_short(f"{mark} {_peer_label(user)} · {user.id}"),
                callback_data=callback,
            )
        )
    if in_topic:
        nav_topic: list[InlineKeyboardButton] = []
        if has_prev:
            nav_topic.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=(f"dialog:tpage:{mode}:{chat_id}:{thread_id}:{page - 1}"),
                )
            )
        if has_next:
            nav_topic.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=(f"dialog:tpage:{mode}:{chat_id}:{thread_id}:{page + 1}"),
                )
            )
        if nav_topic:
            builder.row(*nav_topic)
        builder.row(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"dialog:tchat:{mode}:{chat_id}")
        )
        return builder.as_markup()

    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"dialog:peers:{mode}:{page - 1}"))
    nav.append(InlineKeyboardButton(text="✍️ Ввести ID", callback_data=f"dialog:manual:{mode}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"dialog:peers:{mode}:{page + 1}"))
    builder.row(*nav)
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="dialog:new"),
    )
    return builder.as_markup()


def dialog_ai_instruction(peer_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Без задания (по умолчанию)",
            callback_data=f"dialog:skipinstr:{peer_id}",
        )
    )
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="flow:cancel"))
    return builder.as_markup()


def dialog_edit_instruction(dialog_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row = [
        InlineKeyboardButton(
            text="🗑 Без задания",
            callback_data=f"dialog:instrclear:{dialog_id}",
        )
    ]
    row.append(InlineKeyboardButton(text="Отмена", callback_data=f"dialog:open:{dialog_id}"))
    builder.row(*row)
    return builder.as_markup()


def dialog_cancel() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="flow:cancel"))
    return builder.as_markup()


def dialog_controls(dialog_id: int, mode: str, status: str = "active") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    switch_label = "🤖 Переключить на AI" if mode == "relay" else "💬 Переключить на пересылку"
    builder.row(
        InlineKeyboardButton(text="📜 История", callback_data=f"dialog:history:{dialog_id}"),
        InlineKeyboardButton(text=switch_label, callback_data=f"dialog:switch:{dialog_id}"),
    )
    if mode == "ai":
        builder.row(
            InlineKeyboardButton(text="✏️ Задание для AI", callback_data=f"dialog:instr:{dialog_id}")
        )
    if status == "paused":
        builder.row(
            InlineKeyboardButton(
                text="▶️ Включить диалог", callback_data=f"dialog:resume:{dialog_id}"
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text="⏸ Выключить диалог", callback_data=f"dialog:pause:{dialog_id}"
            )
        )
    builder.row(
        InlineKeyboardButton(text="⛔ Завершить диалог", callback_data=f"dialog:stop:{dialog_id}")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ К диалогам", callback_data="menu:dialogs"),
        InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main"),
    )
    return builder.as_markup()


def users_pagination(page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_prev:
        builder.button(text="⬅️", callback_data=f"menu:users:{page - 1}")
    builder.button(text="⬅️ В меню", callback_data="menu:main")
    if has_next:
        builder.button(text="➡️", callback_data=f"menu:users:{page + 1}")
    return builder.as_markup()
