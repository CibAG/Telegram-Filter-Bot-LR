from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import AiRule, Chat, ModerationRule, Topic, WordFilter

BUTTON_TEXT_LIMIT = 48


def _short(text: str, limit: int = BUTTON_TEXT_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _chat_title(chat: Chat) -> str:
    return chat.title or str(chat.id)


def chats_list(
    chats: list[Chat],
    *,
    page: int,
    has_prev: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for chat in chats:
        mark = "🟢" if chat.is_monitored else "⚪"
        builder.row(
            InlineKeyboardButton(
                text=_short(f"{mark} {_chat_title(chat)}"),
                callback_data=f"chatv:{chat.id}",
            )
        )
    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"menu:chats:{page - 1}"))
    nav.append(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"menu:chats:{page + 1}"))
    builder.row(*nav)
    return builder.as_markup()


def chat_card(
    chat: Chat,
    *,
    topics: int,
    filters: int,
    rules: int,
    ai_rules: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⏹ Выключить чат" if chat.is_monitored else "▶️ Включить чат",
            callback_data=f"chatmon:{chat.id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🎯 Только выбранные темы"
            if chat.monitor_all_topics
            else "🌐 Следить за всеми темами",
            callback_data=f"chatall:{chat.id}",
        )
    )
    builder.row(
        InlineKeyboardButton(text=f"💬 Темы ({topics})", callback_data=f"chattopics:{chat.id}"),
        InlineKeyboardButton(text=f"🚫 Фильтры ({filters})", callback_data=f"wordlist:{chat.id}"),
    )
    builder.row(
        InlineKeyboardButton(text=f"🎯 Правила ({rules})", callback_data=f"rulelist:{chat.id}"),
        InlineKeyboardButton(text="➕ Фильтр", callback_data=f"wordadd:chat:{chat.id}"),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"🤖 AI-правила ({ai_rules})",
            callback_data=f"airulelist:{chat.id}",
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ К чатам", callback_data="menu:chats:0"))
    return builder.as_markup()


def topics_list(chat_id: int, topics: list[Topic], *, enabled: set[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for topic in topics:
        mark = "🟢" if topic.thread_id in enabled else "⚪"
        label = topic.title or f"Тема {topic.thread_id}"
        builder.row(
            InlineKeyboardButton(
                text=_short(f"{mark} {label}"),
                callback_data=f"topicv:{chat_id}:{topic.thread_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="🔗 Назвать тему по ссылке", callback_data=f"topiclink:{chat_id}")
    )
    builder.row(InlineKeyboardButton(text="⬅️ К чату", callback_data=f"chatv:{chat_id}"))
    return builder.as_markup()


def topic_card(chat_id: int, thread_id: int, *, enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⏹ Выключить тему" if enabled else "▶️ Включить тему",
            callback_data=f"topictoggle:{chat_id}:{thread_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="✏️ Название темы",
            callback_data=f"topicrename:{chat_id}:{thread_id}",
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ К темам", callback_data=f"chattopics:{chat_id}"))
    return builder.as_markup()


def words_menu(chats_with_filters: list[Chat]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for chat in chats_with_filters:
        builder.row(
            InlineKeyboardButton(
                text=_short(f"🚫 {_chat_title(chat)}"),
                callback_data=f"wordlist:{chat.id}",
            )
        )
    builder.row(InlineKeyboardButton(text="➕ Добавить фильтр", callback_data="wordadd:chats:0"))
    builder.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main"))
    return builder.as_markup()


def chat_words(chat_id: int, filters: list[WordFilter]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in filters[:20]:
        builder.row(
            InlineKeyboardButton(
                text=_short(f"🗑 #{item.id} {item.pattern}"),
                callback_data=f"worddel:{item.id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить", callback_data=f"wordadd:chat:{chat_id}"),
        InlineKeyboardButton(text="⬅️ К чату", callback_data=f"chatv:{chat_id}"),
    )
    return builder.as_markup()


def rules_menu(chats_with_rules: list[Chat]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for chat in chats_with_rules:
        builder.row(
            InlineKeyboardButton(
                text=_short(f"🎯 {_chat_title(chat)}"),
                callback_data=f"rulelist:{chat.id}",
            )
        )
    builder.row(InlineKeyboardButton(text="➕ Добавить правило", callback_data="ruleadd:chats:0"))
    builder.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main"))
    return builder.as_markup()


def chat_rules(
    chat_id: int,
    rules: list[ModerationRule],
    *,
    topic_titles: dict[int, str] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    titles = topic_titles or {}
    for rule in rules[:20]:
        if rule.thread_id is None:
            scope = "чат"
        else:
            scope = f"тема {titles.get(rule.thread_id) or rule.thread_id}"
        target = "все" if rule.target_user_id is None else str(rule.target_user_id)
        builder.row(
            InlineKeyboardButton(
                text=_short(f"🎯 #{rule.id} · {scope} · {target}"),
                callback_data=f"ruleview:{rule.id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить", callback_data=f"ruleadd:chat:{chat_id}"),
        InlineKeyboardButton(text="⬅️ К чату", callback_data=f"chatv:{chat_id}"),
    )
    return builder.as_markup()


def rule_card(
    rule: ModerationRule,
    *,
    exceptions: set[int],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Исключение", callback_data=f"ruleexcadd:{rule.id}"))
    for user_id in sorted(exceptions)[:15]:
        builder.row(
            InlineKeyboardButton(
                text=f"➖ {user_id}",
                callback_data=f"ruleexcdel:{rule.id}:{user_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="🗑 Удалить правило", callback_data=f"ruledel:{rule.id}"),
        InlineKeyboardButton(text="⬅️ К правилам", callback_data=f"rulelist:{rule.chat_id}"),
    )
    return builder.as_markup()


def ai_rules_menu(chats_with_rules: list[Chat]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for chat in chats_with_rules:
        builder.row(
            InlineKeyboardButton(
                text=_short(f"🤖 {_chat_title(chat)}"),
                callback_data=f"airulelist:{chat.id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить AI-правило", callback_data="airuleadd:chats:0")
    )
    builder.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main"))
    return builder.as_markup()


def chat_ai_rules(
    chat_id: int,
    rules: list[AiRule],
    *,
    topic_titles: dict[int, str] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    titles = topic_titles or {}
    for rule in rules[:20]:
        if rule.thread_id is None:
            scope = "чат"
        else:
            scope = f"тема {titles.get(rule.thread_id) or rule.thread_id}"
        state = "вкл" if rule.enabled else "выкл"
        builder.row(
            InlineKeyboardButton(
                text=_short(f"🤖 #{rule.id} · {scope} · {state}"),
                callback_data=f"airuleview:{rule.id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить", callback_data=f"airuleadd:chat:{chat_id}"),
        InlineKeyboardButton(text="⬅️ К чату", callback_data=f"chatv:{chat_id}"),
    )
    return builder.as_markup()


def ai_rule_card(rule: AiRule) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⏹ Выключить" if rule.enabled else "▶️ Включить",
            callback_data=f"airuletoggle:{rule.id}",
        )
    )
    thread = _thread_slug(rule.thread_id)
    builder.row(
        InlineKeyboardButton(
            text="✏️ Изменить инструкцию",
            callback_data=f"airuleadd:scope:{rule.chat_id}:{thread}",
        )
    )
    builder.row(
        InlineKeyboardButton(text="🗑 Удалить правило", callback_data=f"airuledel:{rule.id}"),
        InlineKeyboardButton(text="⬅️ К правилам", callback_data=f"airulelist:{rule.chat_id}"),
    )
    return builder.as_markup()


def add_chats(
    chats: list[Chat],
    *,
    kind: str,
    page: int,
    has_prev: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for chat in chats:
        builder.row(
            InlineKeyboardButton(
                text=_short(_chat_title(chat)),
                callback_data=f"{kind}add:chat:{chat.id}",
            )
        )
    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{kind}add:chats:{page - 1}"))
    nav.append(InlineKeyboardButton(text="Отмена", callback_data="flow:cancel"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{kind}add:chats:{page + 1}"))
    builder.row(*nav)
    return builder.as_markup()


def add_scope(chat_id: int, topics: list[Topic], *, kind: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Весь чат", callback_data=f"{kind}add:scope:{chat_id}:-1")
    )
    for topic in topics[:20]:
        label = topic.title or f"Тема {topic.thread_id}"
        builder.row(
            InlineKeyboardButton(
                text=_short(label),
                callback_data=f"{kind}add:scope:{chat_id}:{topic.thread_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{kind}add:chats:0"),
        InlineKeyboardButton(text="Отмена", callback_data="flow:cancel"),
    )
    return builder.as_markup()


def _thread_slug(thread_id: int | None) -> int:
    """Encode "whole chat" scope for callback data (``-1`` means no topic)."""
    return -1 if thread_id is None else thread_id


def add_filter_type(chat_id: int, thread_id: int | None) -> InlineKeyboardMarkup:
    thread = _thread_slug(thread_id)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Слово", callback_data=f"wordadd:type:{chat_id}:{thread}:word"),
        InlineKeyboardButton(
            text="Подстрока",
            callback_data=f"wordadd:type:{chat_id}:{thread}:substring",
        ),
    )
    builder.row(
        InlineKeyboardButton(text="Regex", callback_data=f"wordadd:type:{chat_id}:{thread}:regex")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"wordadd:chat:{chat_id}"),
        InlineKeyboardButton(text="Отмена", callback_data="flow:cancel"),
    )
    return builder.as_markup()


def add_rule_target(chat_id: int, thread_id: int | None) -> InlineKeyboardMarkup:
    thread = _thread_slug(thread_id)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Все пользователи",
            callback_data=f"ruleadd:target:{chat_id}:{thread}:all",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Конкретный пользователь",
            callback_data=f"ruleadd:target:{chat_id}:{thread}:user",
        )
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ruleadd:chat:{chat_id}"),
        InlineKeyboardButton(text="Отмена", callback_data="flow:cancel"),
    )
    return builder.as_markup()


def cancel_flow() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="flow:cancel"))
    return builder.as_markup()
