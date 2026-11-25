from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import Chat
from app.filters.admin import IsAdmin, IsPrivateChat
from app.keyboards import panel as kb
from app.keyboards.admin import main_menu
from app.services.matching import validate_pattern
from app.utils.links import chat_ref_matches, parse_topic_link
from app.utils.text import escape

router = Router(name="panel")

CHATS_PER_PAGE = 8


class AddFilterFlow(StatesGroup):
    pattern = State()


class AddRuleFlow(StatesGroup):
    target = State()


class AddAiRuleFlow(StatesGroup):
    instruction = State()


class AddExceptionFlow(StatesGroup):
    user = State()


class RenameTopicFlow(StatesGroup):
    title = State()


class RenameByLinkFlow(StatesGroup):
    link = State()
    title = State()


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def _chat_label(chat: Chat) -> str:
    return f"{escape(chat.title or 'Без названия')} (<code>{chat.id}</code>)"


async def _scope_label_named(
    session: AsyncSession,
    chat_id: int,
    thread_id: int | None,
) -> str:
    """Human-readable scope: topic title when known, otherwise its id."""
    if thread_id is None:
        return "весь чат"
    topic = await repo.get_topic(session, chat_id, thread_id)
    if topic is not None and topic.title:
        return f"тема «{escape(topic.title)}»"
    return f"тема {thread_id}"


def _preview(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


async def _chats_view(session: AsyncSession, page: int) -> tuple[str, InlineKeyboardMarkup]:
    page = max(page, 0)
    chats = await repo.list_chats(session)
    total = len(chats)
    start = page * CHATS_PER_PAGE
    items = chats[start : start + CHATS_PER_PAGE]
    has_next = start + CHATS_PER_PAGE < total

    lines = [f"<b>🗂 Чаты и темы</b> (всего {total})", ""]
    if not chats:
        lines.append(
            "Пока пусто. Напишите любое сообщение в группе, где есть бот, "
            "и чат появится здесь. Темы появляются после сообщений в них."
        )
    for chat in items:
        topics = await repo.count_topics(session, chat.id)
        status = "включён" if chat.is_monitored else "выключен"
        mode = "все темы" if chat.monitor_all_topics else "выбранные темы"
        lines.append(f"• {_chat_label(chat)} — {status}, {mode}, тем: {topics}")
    if chats:
        lines.append("")
        lines.append("Выберите чат для управления.")
    return "\n".join(lines), kb.chats_list(items, page=page, has_prev=page > 0, has_next=has_next)


async def _chat_view(session: AsyncSession, chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        return "Чат не найден в базе.", main_menu()

    topics = await repo.count_topics(session, chat_id)
    filters = await repo.count_word_filters(session, chat_id)
    rules = await repo.count_rules(session, chat_id)
    ai_rules = await repo.count_ai_rules(session, chat_id)
    lines = [
        f"<b>🏷 {escape(chat.title or 'Без названия')}</b>",
        f"ID: <code>{chat.id}</code>",
        f"Тип: {escape(chat.type)}",
        f"Форум: {'да' if chat.is_forum else 'нет'}",
        f"Мониторинг: {'включён' if chat.is_monitored else 'выключен'}",
        ("Режим тем: все темы" if chat.monitor_all_topics else "Режим тем: только выбранные"),
        f"Тем в базе: {topics}",
        f"Фильтров: {filters}",
        f"Правил: {rules}",
        f"AI-правил: {ai_rules}",
        "",
        "Темы появляются в списке после сообщений в них.",
    ]
    return "\n".join(lines), kb.chat_card(
        chat, topics=topics, filters=filters, rules=rules, ai_rules=ai_rules
    )


async def _topics_view(session: AsyncSession, chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        return "Чат не найден в базе.", main_menu()

    topics = await repo.list_topics(session, chat_id)
    enabled = {
        topic.thread_id
        for topic in topics
        if not topic.is_excluded and (chat.monitor_all_topics or topic.is_monitored)
    }
    lines = [f"<b>💬 Темы</b> — {_chat_label(chat)}", ""]
    if not topics:
        lines.append("Тем пока нет. Напишите сообщение в нужной теме, чтобы бот её увидел.")
    for topic in topics:
        title = escape(topic.title) if topic.title else "Без названия"
        is_enabled = topic.thread_id in enabled
        state = "включена" if is_enabled else "выключена"
        if topic.is_excluded:
            state += ", исключена из «всех тем»"
        lines.append(f"• {title} — thread <code>{topic.thread_id}</code>, {state}")
    lines += [
        "",
        "Номер темы — среднее число в ссылке "
        "<code>t.me/c/…/47/1234</code>. Кнопка «🔗 Назвать тему по ссылке» "
        "подпишет тему, если номер неизвестен.",
    ]
    return "\n".join(lines), kb.topics_list(chat_id, topics, enabled=enabled)


async def _topic_view(
    session: AsyncSession, chat_id: int, thread_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    topic = await repo.get_topic(session, chat_id, thread_id)
    if topic is None:
        return "Тема не найдена в базе.", main_menu()

    chat = await repo.get_chat(session, chat_id)
    title = escape(topic.title) if topic.title else "Без названия"
    enabled = not topic.is_excluded and (
        (chat.monitor_all_topics if chat else False) or topic.is_monitored
    )
    lines = [
        f"<b>💬 Тема «{title}»</b>",
        f"Чат: {_chat_label(chat) if chat else chat_id}",
        f"ID темы (thread_id): <code>{topic.thread_id}</code>",
        f"Мониторинг: {'включён' if enabled else 'выключен'}",
        f"Исключена из «всех тем»: {'да' if topic.is_excluded else 'нет'}",
    ]
    if not topic.title:
        lines += [
            "",
            "Название не сохранено: бот узнаёт его только у тем, созданных при нём. "
            "Нажмите «✏️ Название темы», чтобы подписать её.",
        ]
    return "\n".join(lines), kb.topic_card(chat_id, thread_id, enabled=enabled)


async def _words_view(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    filters = await repo.list_word_filters(session)
    chats = {chat.id: chat for chat in await repo.list_chats(session)}
    lines = [f"<b>🚫 Слова-фильтры</b> (всего {len(filters)})", ""]
    if not filters:
        lines.append("Фильтров пока нет.")
    for item in filters[:30]:
        chat = chats.get(item.chat_id)
        chat_title = escape(chat.title) if chat and chat.title else str(item.chat_id)
        state = "вкл" if item.enabled else "выкл"
        scope = await _scope_label_named(session, item.chat_id, item.thread_id)
        lines.append(
            f"<code>#{item.id}</code> [{item.match_type}, {state}, "
            f"{scope}] <code>{escape(_preview(item.pattern))}</code> "
            f"— {chat_title}"
        )
    if len(filters) > 30:
        lines.append(f"… и ещё {len(filters) - 30}")
    lines.append("")
    lines.append("Выберите чат ниже или добавьте новый фильтр.")
    chats_with_filters = [
        chat for chat in chats.values() if any(item.chat_id == chat.id for item in filters)
    ]
    return "\n".join(lines), kb.words_menu(chats_with_filters)


async def _chat_words_view(session: AsyncSession, chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        return "Чат не найден в базе.", main_menu()

    filters = await repo.list_word_filters(session, chat_id=chat_id)
    lines = [f"<b>🚫 Фильтры</b> — {_chat_label(chat)}", ""]
    if not filters:
        lines.append("Фильтров пока нет.")
    for item in filters[:20]:
        scope = await _scope_label_named(session, chat_id, item.thread_id)
        lines.append(
            f"<code>#{item.id}</code> [{item.match_type}, "
            f"{scope}] <code>{escape(_preview(item.pattern))}</code>"
        )
    if len(filters) > 20:
        lines.append(f"… и ещё {len(filters) - 20}")
    lines.append("")
    lines.append("Нажмите 🗑 под сообщением, чтобы удалить фильтр.")
    return "\n".join(lines), kb.chat_words(chat_id, filters)


async def _rules_view(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    rules = await repo.list_rules(session)
    chats = {chat.id: chat for chat in await repo.list_chats(session)}
    lines = [f"<b>🎯 Правила</b> (всего {len(rules)})", ""]
    if not rules:
        lines.append("Правил пока нет.")
    for rule in rules[:30]:
        chat = chats.get(rule.chat_id)
        chat_title = escape(chat.title) if chat and chat.title else str(rule.chat_id)
        target = "все пользователи" if rule.target_user_id is None else str(rule.target_user_id)
        exceptions = await repo.exception_user_ids(session, rule.id)
        scope = await _scope_label_named(session, rule.chat_id, rule.thread_id)
        lines.append(
            f"<code>#{rule.id}</code> [{scope}] "
            f"цель: {target}, исключений: {len(exceptions)} — {chat_title}"
        )
    if len(rules) > 30:
        lines.append(f"… и ещё {len(rules) - 30}")
    lines.append("")
    lines.append("Выберите чат ниже или добавьте новое правило.")
    chats_with_rules = [
        chat for chat in chats.values() if any(rule.chat_id == chat.id for rule in rules)
    ]
    return "\n".join(lines), kb.rules_menu(chats_with_rules)


async def _chat_rules_view(session: AsyncSession, chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        return "Чат не найден в базе.", main_menu()

    rules = await repo.list_rules(session, chat_id=chat_id)
    lines = [f"<b>🎯 Правила</b> — {_chat_label(chat)}", ""]
    if not rules:
        lines.append("Правил пока нет.")
    for rule in rules[:20]:
        target = "все пользователи" if rule.target_user_id is None else str(rule.target_user_id)
        exceptions = await repo.exception_user_ids(session, rule.id)
        scope = await _scope_label_named(session, chat_id, rule.thread_id)
        lines.append(
            f"<code>#{rule.id}</code> [{scope}] цель: {target}, исключений: {len(exceptions)}"
        )
    if len(rules) > 20:
        lines.append(f"… и ещё {len(rules) - 20}")
    lines.append("")
    lines.append("Нажмите на правило, чтобы настроить исключения.")
    topics = await repo.list_topics(session, chat_id)
    titles = {topic.thread_id: topic.title for topic in topics if topic.title}
    return "\n".join(lines), kb.chat_rules(chat_id, rules, topic_titles=titles)


async def _rule_view(session: AsyncSession, rule_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rule = await repo.get_rule(session, rule_id)
    if rule is None:
        return "Правило не найдено.", main_menu()

    chat = await repo.get_chat(session, rule.chat_id)
    exceptions = await repo.exception_user_ids(session, rule.id)
    target = "все пользователи" if rule.target_user_id is None else str(rule.target_user_id)
    scope = await _scope_label_named(session, rule.chat_id, rule.thread_id)
    lines = [
        f"<b>🎯 Правило #{rule.id}</b>",
        f"Чат: {_chat_label(chat) if chat else rule.chat_id}",
        f"Область: {scope}",
        f"Цель: {target}",
        f"Исключений: {len(exceptions)}",
        f"Статус: {'включено' if rule.enabled else 'выключено'}",
        "",
    ]
    if exceptions:
        lines.append("Нажмите ➖ под сообщением, чтобы снять исключение.")
    else:
        lines.append("Исключений нет.")
    return "\n".join(lines), kb.rule_card(rule, exceptions=exceptions)


# ---------------------------------------------------------------------------
# AI moderation rules
# ---------------------------------------------------------------------------


async def _ai_rules_view(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    rules = await repo.list_ai_rules(session)
    chats = {chat.id: chat for chat in await repo.list_chats(session)}
    lines = [f"<b>🤖 AI-модерация</b> (всего {len(rules)})", ""]
    if not rules:
        lines.append("AI-правил пока нет.")
        lines.append("")
        lines.append(
            "AI-правило — это текстовая инструкция. Модель читает каждое новое "
            "сообщение и решает: удалить (DELETE) или оставить (KEEP)."
        )
    for rule in rules[:30]:
        chat = chats.get(rule.chat_id)
        chat_title = escape(chat.title) if chat and chat.title else str(rule.chat_id)
        scope = await _scope_label_named(session, rule.chat_id, rule.thread_id)
        state = "вкл" if rule.enabled else "выкл"
        lines.append(
            f"<code>#{rule.id}</code> [{scope}, {state}] "
            f"{escape(_preview(rule.instruction, 70))} — {chat_title}"
        )
    if len(rules) > 30:
        lines.append(f"… и ещё {len(rules) - 30}")
    lines.append("")
    lines.append("Выберите чат ниже или добавьте новое AI-правило.")
    chats_with_rules = [
        chat for chat in chats.values() if any(rule.chat_id == chat.id for rule in rules)
    ]
    return "\n".join(lines), kb.ai_rules_menu(chats_with_rules)


async def _chat_ai_rules_view(
    session: AsyncSession,
    chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        return "Чат не найден в базе.", main_menu()

    rules = await repo.list_ai_rules(session, chat_id=chat_id)
    topics = await repo.list_topics(session, chat_id)
    titles = {topic.thread_id: topic.title for topic in topics if topic.title}
    lines = [f"<b>🤖 AI-правила</b> — {_chat_label(chat)}", ""]
    if not rules:
        lines.append("AI-правил пока нет.")
    for rule in rules[:20]:
        scope = await _scope_label_named(session, chat_id, rule.thread_id)
        state = "вкл" if rule.enabled else "выкл"
        lines.append(
            f"<code>#{rule.id}</code> [{scope}, {state}] {escape(_preview(rule.instruction, 70))}"
        )
    if len(rules) > 20:
        lines.append(f"… и ещё {len(rules) - 20}")
    lines.append("")
    lines.append(
        "Правило темы важнее правила всего чата. Нажмите на правило, чтобы открыть карточку."
    )
    return "\n".join(lines), kb.chat_ai_rules(chat_id, rules, topic_titles=titles)


async def _ai_rule_view(session: AsyncSession, rule_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rule = await repo.get_ai_rule(session, rule_id)
    if rule is None:
        return "AI-правило не найдено.", main_menu()

    chat = await repo.get_chat(session, rule.chat_id)
    scope = await _scope_label_named(session, rule.chat_id, rule.thread_id)
    lines = [
        f"<b>🤖 AI-правило #{rule.id}</b>",
        f"Чат: {_chat_label(chat) if chat else rule.chat_id}",
        f"Область: {scope}",
        "Действие: удалить и уведомить администратора",
        f"Статус: {'включено' if rule.enabled else 'выключено'}",
        "",
        "<b>Инструкция:</b>",
        f"<blockquote>{escape(_preview(rule.instruction, 3000))}</blockquote>",
    ]
    return "\n".join(lines), kb.ai_rule_card(rule)


# ---------------------------------------------------------------------------
# Chat and topic navigation
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("menu:chats:"), IsAdmin())
async def cb_chats(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        page = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        page = 0
    text, markup = await _chats_view(session, page)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("chatv:"), IsAdmin())
async def cb_chat(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = _int_tail(callback.data)
    if chat_id is None:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    text, markup = await _chat_view(session, chat_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("chatmon:"), IsAdmin())
async def cb_chat_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = _int_tail(callback.data)
    if chat_id is None:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return
    enabled = not chat.is_monitored
    await repo.set_monitoring(session, chat_id, enabled)
    await repo.add_audit(
        session,
        action="chat_enabled" if enabled else "chat_disabled",
        actor_id=callback.from_user.id,
        chat_id=chat_id,
    )
    text, markup = await _chat_view(session, chat_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("chatall:"), IsAdmin())
async def cb_chat_all_topics(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = _int_tail(callback.data)
    if chat_id is None:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return
    enabled = not chat.monitor_all_topics
    await repo.set_monitor_all_topics(session, chat_id, enabled)
    await repo.add_audit(
        session,
        action="chat_monitor_all_topics" if enabled else "chat_selected_topics",
        actor_id=callback.from_user.id,
        chat_id=chat_id,
    )
    text, markup = await _chat_view(session, chat_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("chattopics:"), IsAdmin())
async def cb_topics(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = _int_tail(callback.data)
    if chat_id is None:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    text, markup = await _topics_view(session, chat_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("topicv:"), IsAdmin())
async def cb_topic(callback: CallbackQuery, session: AsyncSession) -> None:
    parsed = _two_ints(callback.data)
    if parsed is None:
        await callback.answer("Некорректная тема", show_alert=True)
        return
    chat_id, thread_id = parsed
    text, markup = await _topic_view(session, chat_id, thread_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("topictoggle:"), IsAdmin())
async def cb_topic_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    parsed = _two_ints(callback.data)
    if parsed is None:
        await callback.answer("Некорректная тема", show_alert=True)
        return
    chat_id, thread_id = parsed
    topic = await repo.get_topic(session, chat_id, thread_id)
    if topic is None:
        await callback.answer("Тема не найдена", show_alert=True)
        return
    chat = await repo.get_chat(session, chat_id)
    enabled = not topic.is_excluded and (
        (chat.monitor_all_topics if chat else False) or topic.is_monitored
    )
    if not enabled:
        await repo.enable_topic(session, chat_id, thread_id, actor_id=callback.from_user.id)
    else:
        await repo.disable_topic(session, chat_id, thread_id)
    await repo.add_audit(
        session,
        action="topic_enabled" if enabled else "topic_disabled",
        actor_id=callback.from_user.id,
        chat_id=chat_id,
        details=f"thread={thread_id}",
    )
    text, markup = await _topic_view(session, chat_id, thread_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("topicrename:"), IsAdmin())
async def cb_topic_rename(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    parsed = _two_ints(callback.data)
    if parsed is None:
        await callback.answer("Некорректная тема", show_alert=True)
        return
    chat_id, thread_id = parsed
    topic = await repo.get_topic(session, chat_id, thread_id)
    if topic is None:
        await callback.answer("Тема не найдена", show_alert=True)
        return

    await state.set_state(RenameTopicFlow.title)
    await state.set_data({"chat_id": chat_id, "thread_id": thread_id})
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Введите название для темы <code>{thread_id}</code> одним сообщением.\n\n"
            "Отправьте <code>-</code>, чтобы убрать название.",
            reply_markup=kb.cancel_flow(),
        )
    await callback.answer()


@router.message(RenameTopicFlow.title, IsAdmin(), IsPrivateChat(), F.text, ~F.text.startswith("/"))
async def save_topic_title(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await _save_title_from_state(message, state, session)


@router.callback_query(F.data.startswith("topiclink:"), IsAdmin())
async def cb_topic_link(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    chat_id = _int_tail(callback.data)
    if chat_id is None:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return

    await state.set_state(RenameByLinkFlow.link)
    await state.set_data({"chat_id": chat_id})
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"🔗 <b>Название темы по ссылке</b>\n\n"
            f"Чат: {_chat_label(chat)}\n\n"
            "Откройте нужную тему в Telegram, зажмите любое сообщение и выберите "
            "«Копировать ссылку». Пришлите её сюда.\n\n"
            "Подходит любой формат:\n"
            "• <code>https://t.me/c/2060145994/47/1234</code>\n"
            "• <code>https://web.telegram.org/a/#-1002060145994_47</code>\n\n"
            "Число после чата — номер темы (здесь <code>47</code>).",
            reply_markup=kb.cancel_flow(),
        )
    await callback.answer()


@router.message(RenameByLinkFlow.link, IsAdmin(), IsPrivateChat(), F.text, ~F.text.startswith("/"))
async def save_topic_link(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    try:
        chat_id = int(data["chat_id"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer("Состояние сброшено. Начните заново.")
        return

    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await state.clear()
        await message.answer("Чат не найден в базе. Откройте панель заново.")
        return

    link = parse_topic_link(message.text)
    if link is None or link.thread_id is None:
        await message.answer(
            "Не понял ссылку. Нужна ссылка на сообщение или тему, например:\n"
            "<code>https://t.me/c/2060145994/47/1234</code>\n"
            "<code>https://web.telegram.org/a/#-1002060145994_47</code>\n\n"
            "Попробуйте ещё раз или нажмите «Отмена».",
            reply_markup=kb.cancel_flow(),
        )
        return

    if not await _link_belongs_to_chat(message, chat, link.chat_ref, link.thread_id):
        await message.answer(
            "Эта ссылка от другого чата. Вернитесь в панель и выберите нужный чат.",
            reply_markup=kb.cancel_flow(),
        )
        return

    topic = await repo.get_topic(session, chat_id, link.thread_id)
    if topic is None:
        await state.clear()
        await message.answer(
            f"Тема <code>{link.thread_id}</code> пока не известна боту.\n\n"
            "Напишите в неё любое сообщение, затем повторите: "
            "«🗂 Чаты и темы» → чат → «💬 Темы» → «🔗 Назвать тему по ссылке»."
        )
        return

    await state.set_state(RenameByLinkFlow.title)
    await state.set_data({"chat_id": chat_id, "thread_id": link.thread_id})
    current = escape(topic.title) if topic.title else "без названия"
    await message.answer(
        f"✅ Тема <code>{link.thread_id}</code> найдена, сейчас: {current}.\n\n"
        "Отправьте новое название одним сообщением. "
        "Символ <code>-</code> уберёт название.",
        reply_markup=kb.cancel_flow(),
    )


@router.message(RenameByLinkFlow.title, IsAdmin(), IsPrivateChat(), F.text, ~F.text.startswith("/"))
async def save_link_title(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await _save_title_from_state(message, state, session)


async def _link_belongs_to_chat(
    message: Message,
    chat: Chat,
    chat_ref: str,
    thread_id: int,
) -> bool:
    if chat_ref.isdigit():
        return chat_ref_matches(chat.id, chat_ref)
    # Public link: ask Telegram which chat the username points to.
    try:
        resolved = await message.bot.get_chat(chat_ref)
    except TelegramAPIError:
        return True
    return resolved.id == chat.id


async def _save_title_from_state(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    try:
        chat_id = int(data["chat_id"])
        thread_id = int(data["thread_id"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer("Состояние сброшено. Начните заново.")
        return

    raw = (message.text or "").strip()
    if len(raw) > 255:
        await message.answer(
            "Название слишком длинное, максимум 255 символов. Попробуйте снова.",
            reply_markup=kb.cancel_flow(),
        )
        return

    title = None if raw in {"-", "—"} else raw
    topic = await repo.rename_topic(session, chat_id, thread_id, title)
    if topic is None:
        await state.clear()
        await message.answer("Тема не найдена в базе.")
        return

    await repo.add_audit(
        session,
        action="topic_renamed",
        actor_id=message.from_user.id if message.from_user else None,
        chat_id=chat_id,
        details=f"thread={thread_id} title={title or '-'}",
    )
    await state.clear()
    text, markup = await _topic_view(session, chat_id, thread_id)
    await message.answer("✅ Название сохранено.")
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "menu:words", IsAdmin())
async def cb_words(callback: CallbackQuery, session: AsyncSession) -> None:
    text, markup = await _words_view(session)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("wordlist:"), IsAdmin())
async def cb_chat_words(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = _int_tail(callback.data)
    if chat_id is None:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    text, markup = await _chat_words_view(session, chat_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("worddel:"), IsAdmin())
async def cb_word_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    filter_id = _int_tail(callback.data)
    if filter_id is None:
        await callback.answer("Некорректный фильтр", show_alert=True)
        return
    item = await repo.get_word_filter(session, filter_id)
    if item is None:
        await callback.answer("Фильтр не найден", show_alert=True)
        return
    chat_id = item.chat_id
    await repo.delete_word_filter(session, filter_id)
    await repo.add_audit(
        session,
        action="word_filter_deleted",
        actor_id=callback.from_user.id,
        chat_id=chat_id,
        details=f"filter={filter_id}",
    )
    text, markup = await _chat_words_view(session, chat_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("wordadd:chats:"), IsAdmin())
async def cb_word_add_chats(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        page = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        page = 0
    chats = await repo.list_chats(session)
    text, markup = _add_chats_view(chats, kind="word", page=page)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("wordadd:chat:"), IsAdmin())
async def cb_word_add_chat(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = _int_tail(callback.data)
    if chat_id is None:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return
    topics = await repo.list_topics(session, chat_id)
    text = f"<b>➕ Новый фильтр</b>\n\nЧат: {_chat_label(chat)}\n\nВыберите область действия:"
    await _edit(callback, text, kb.add_scope(chat_id, topics, kind="word"))


@router.callback_query(F.data.startswith("wordadd:scope:"), IsAdmin())
async def cb_word_add_scope(callback: CallbackQuery, session: AsyncSession) -> None:
    parsed = _three_parts_ints(callback.data, "wordadd:scope")
    if parsed is None:
        await callback.answer("Некорректная область", show_alert=True)
        return
    chat_id, thread_id = parsed
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return
    text = (
        f"<b>➕ Новый фильтр</b>\n\n"
        f"Чат: {_chat_label(chat)}\n"
        f"Область: {await _scope_label_named(session, chat_id, thread_id)}\n\n"
        "Выберите тип совпадения:"
    )
    await _edit(callback, text, kb.add_filter_type(chat_id, thread_id))


@router.callback_query(F.data.startswith("wordadd:type:"), IsAdmin())
async def cb_word_add_type(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer("Некорректный тип", show_alert=True)
        return
    try:
        chat_id = int(parts[2])
        thread_raw = int(parts[3])
    except ValueError:
        await callback.answer("Некорректный тип", show_alert=True)
        return
    match_type = parts[4]
    thread_id = None if thread_raw < 0 else thread_raw
    if match_type not in {"word", "substring", "regex"}:
        await callback.answer("Неизвестный тип", show_alert=True)
        return

    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return

    await state.set_state(AddFilterFlow.pattern)
    await state.set_data({"chat_id": chat_id, "thread_id": thread_raw, "match_type": match_type})
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите шаблон фильтра одним сообщением.\n\n"
            f"Чат: {escape(chat.title or str(chat_id))}\n"
            f"Область: {await _scope_label_named(session, chat_id, thread_id)}\n"
            f"Тип: {match_type}",
            reply_markup=kb.cancel_flow(),
        )
    await callback.answer()


@router.message(AddFilterFlow.pattern, IsAdmin(), IsPrivateChat(), F.text, ~F.text.startswith("/"))
async def add_filter_pattern(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    try:
        chat_id = int(data["chat_id"])
        thread_raw = int(data["thread_id"])
        match_type = str(data["match_type"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer("Состояние добавления сброшено. Начните заново.")
        return

    pattern = (message.text or "").strip()
    if pattern.startswith("/"):
        await message.answer("Введите шаблон текстом или нажмите «Отмена».")
        return

    error = validate_pattern(pattern, match_type)
    if error:
        await message.answer(
            f"❌ {error}\nПопробуйте снова или нажмите «Отмена».",
            reply_markup=kb.cancel_flow(),
        )
        return

    thread_id = None if thread_raw < 0 else thread_raw
    item = await repo.add_word_filter(
        session,
        chat_id=chat_id,
        thread_id=thread_id,
        pattern=pattern,
        match_type=match_type,
        case_sensitive=False,
        created_by=message.from_user.id if message.from_user else None,
    )
    await repo.add_audit(
        session,
        action="word_filter_added",
        actor_id=message.from_user.id if message.from_user else None,
        chat_id=chat_id,
        details=f"filter={item.id} type={match_type} thread={thread_id}",
    )
    await state.clear()
    text, markup = await _chat_words_view(session, chat_id)
    await message.answer(
        f"✅ Фильтр <code>#{item.id}</code> добавлен "
        f"({await _scope_label_named(session, chat_id, thread_id)}).",
    )
    await message.answer(text, reply_markup=markup)


# ---------------------------------------------------------------------------
# Moderation rules
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "menu:rules", IsAdmin())
async def cb_rules(callback: CallbackQuery, session: AsyncSession) -> None:
    text, markup = await _rules_view(session)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("rulelist:"), IsAdmin())
async def cb_chat_rules(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = _int_tail(callback.data)
    if chat_id is None:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    text, markup = await _chat_rules_view(session, chat_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("ruleview:"), IsAdmin())
async def cb_rule(callback: CallbackQuery, session: AsyncSession) -> None:
    rule_id = _int_tail(callback.data)
    if rule_id is None:
        await callback.answer("Некорректное правило", show_alert=True)
        return
    text, markup = await _rule_view(session, rule_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("ruledel:"), IsAdmin())
async def cb_rule_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    rule_id = _int_tail(callback.data)
    if rule_id is None:
        await callback.answer("Некорректное правило", show_alert=True)
        return
    rule = await repo.get_rule(session, rule_id)
    if rule is None:
        await callback.answer("Правило не найдено", show_alert=True)
        return
    chat_id = rule.chat_id
    await repo.delete_rule(session, rule_id)
    await repo.add_audit(
        session,
        action="rule_deleted",
        actor_id=callback.from_user.id,
        chat_id=chat_id,
        details=f"rule={rule_id}",
    )
    text, markup = await _chat_rules_view(session, chat_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("ruleadd:chats:"), IsAdmin())
async def cb_rule_add_chats(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        page = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        page = 0
    chats = await repo.list_chats(session)
    text, markup = _add_chats_view(chats, kind="rule", page=page)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("ruleadd:chat:"), IsAdmin())
async def cb_rule_add_chat(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = _int_tail(callback.data)
    if chat_id is None:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return
    topics = await repo.list_topics(session, chat_id)
    text = f"<b>➕ Новое правило</b>\n\nЧат: {_chat_label(chat)}\n\nВыберите область действия:"
    await _edit(callback, text, kb.add_scope(chat_id, topics, kind="rule"))


@router.callback_query(F.data.startswith("ruleadd:scope:"), IsAdmin())
async def cb_rule_add_scope(callback: CallbackQuery, session: AsyncSession) -> None:
    parsed = _three_parts_ints(callback.data, "ruleadd:scope")
    if parsed is None:
        await callback.answer("Некорректная область", show_alert=True)
        return
    chat_id, thread_id = parsed
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return
    text = (
        f"<b>➕ Новое правило</b>\n\n"
        f"Чат: {_chat_label(chat)}\n"
        f"Область: {await _scope_label_named(session, chat_id, thread_id)}\n\n"
        "Сообщения кого удалять?"
    )
    await _edit(callback, text, kb.add_rule_target(chat_id, thread_id))


@router.callback_query(F.data.startswith("ruleadd:target:"), IsAdmin())
async def cb_rule_add_target(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer("Некорректная цель", show_alert=True)
        return
    try:
        chat_id = int(parts[2])
        thread_raw = int(parts[3])
    except ValueError:
        await callback.answer("Некорректная цель", show_alert=True)
        return
    target = parts[4]
    thread_id = None if thread_raw < 0 else thread_raw

    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return

    if target == "all":
        rule = await repo.add_rule(
            session,
            chat_id=chat_id,
            thread_id=thread_id,
            target_user_id=None,
            created_by=callback.from_user.id,
        )
        await repo.add_audit(
            session,
            action="rule_added",
            actor_id=callback.from_user.id,
            chat_id=chat_id,
            details=f"rule={rule.id} target=all thread={thread_id}",
        )
        text, markup = await _rule_view(session, rule.id)
        await _edit(callback, text, markup)
        return

    if target != "user":
        await callback.answer("Неизвестная цель", show_alert=True)
        return

    await state.set_state(AddRuleFlow.target)
    await state.set_data(
        {"chat_id": chat_id, "thread_id": thread_raw},
    )
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Отправьте ID или @username пользователя, чьи сообщения удалять.\n\n"
            f"Чат: {escape(chat.title or str(chat_id))}\n"
            f"Область: {await _scope_label_named(session, chat_id, thread_id)}",
            reply_markup=kb.cancel_flow(),
        )
    await callback.answer()


@router.message(AddRuleFlow.target, IsAdmin(), IsPrivateChat(), F.text, ~F.text.startswith("/"))
async def add_rule_target(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    try:
        chat_id = int(data["chat_id"])
        thread_raw = int(data["thread_id"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer("Состояние добавления сброшено. Начните заново.")
        return

    raw = (message.text or "").strip()
    if raw.startswith("/"):
        await message.answer("Отправьте ID или @username или нажмите «Отмена».")
        return

    user_id, error = await _resolve_user(session, raw)
    if error or user_id is None:
        await message.answer(
            f"❌ {error or 'Пользователь не найден.'}",
            reply_markup=kb.cancel_flow(),
        )
        return

    thread_id = None if thread_raw < 0 else thread_raw
    rule = await repo.add_rule(
        session,
        chat_id=chat_id,
        thread_id=thread_id,
        target_user_id=user_id,
        created_by=message.from_user.id if message.from_user else None,
    )
    await repo.add_audit(
        session,
        action="rule_added",
        actor_id=message.from_user.id if message.from_user else None,
        chat_id=chat_id,
        details=f"rule={rule.id} target={user_id} thread={thread_id}",
    )
    await state.clear()
    text, markup = await _rule_view(session, rule.id)
    await message.answer(
        f"✅ Правило <code>#{rule.id}</code> добавлено.",
    )
    await message.answer(text, reply_markup=markup)


# ---------------------------------------------------------------------------
# Rule exceptions
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("ruleexcadd:"), IsAdmin())
async def cb_exception_add(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    rule_id = _int_tail(callback.data)
    if rule_id is None:
        await callback.answer("Некорректное правило", show_alert=True)
        return
    rule = await repo.get_rule(session, rule_id)
    if rule is None:
        await callback.answer("Правило не найдено", show_alert=True)
        return

    await state.set_state(AddExceptionFlow.user)
    await state.set_data({"rule_id": rule_id})
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Отправьте ID или @username пользователя для исключения из правила #{rule_id}.",
            reply_markup=kb.cancel_flow(),
        )
    await callback.answer()


@router.message(AddExceptionFlow.user, IsAdmin(), IsPrivateChat(), F.text, ~F.text.startswith("/"))
async def add_exception_user(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    try:
        rule_id = int(data["rule_id"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer("Состояние сброшено. Начните заново.")
        return

    raw = (message.text or "").strip()
    if raw.startswith("/"):
        await message.answer("Отправьте ID или @username или нажмите «Отмена».")
        return

    user_id, error = await _resolve_user(session, raw)
    if error or user_id is None:
        await message.answer(
            f"❌ {error or 'Пользователь не найден.'}",
            reply_markup=kb.cancel_flow(),
        )
        return

    created = await repo.add_exception(session, rule_id, user_id)
    if created:
        await repo.add_audit(
            session,
            action="exception_added",
            actor_id=message.from_user.id if message.from_user else None,
            details=f"rule={rule_id} user={user_id}",
        )
    await state.clear()
    text, markup = await _rule_view(session, rule_id)
    if created:
        await message.answer(f"✅ Пользователь <code>{user_id}</code> добавлен в исключения.")
    else:
        await message.answer("Этот пользователь уже в исключениях.")
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ruleexcdel:"), IsAdmin())
async def cb_exception_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    parsed = _two_ints(callback.data)
    if parsed is None:
        await callback.answer("Некорректное исключение", show_alert=True)
        return
    rule_id, user_id = parsed
    removed = await repo.remove_exception(session, rule_id, user_id)
    if removed:
        await repo.add_audit(
            session,
            action="exception_removed",
            actor_id=callback.from_user.id,
            details=f"rule={rule_id} user={user_id}",
        )
    text, markup = await _rule_view(session, rule_id)
    await _edit(callback, text, markup)


# ---------------------------------------------------------------------------
# AI moderation rules
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "menu:airules", IsAdmin())
async def cb_ai_rules(callback: CallbackQuery, session: AsyncSession) -> None:
    text, markup = await _ai_rules_view(session)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("airulelist:"), IsAdmin())
async def cb_chat_ai_rules(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = _int_tail(callback.data)
    if chat_id is None:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    text, markup = await _chat_ai_rules_view(session, chat_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("airuleview:"), IsAdmin())
async def cb_ai_rule(callback: CallbackQuery, session: AsyncSession) -> None:
    rule_id = _int_tail(callback.data)
    if rule_id is None:
        await callback.answer("Некорректное AI-правило", show_alert=True)
        return
    text, markup = await _ai_rule_view(session, rule_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("airuletoggle:"), IsAdmin())
async def cb_ai_rule_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    rule_id = _int_tail(callback.data)
    if rule_id is None:
        await callback.answer("Некорректное AI-правило", show_alert=True)
        return
    rule = await repo.get_ai_rule(session, rule_id)
    if rule is None:
        await callback.answer("AI-правило не найдено", show_alert=True)
        return
    updated = await repo.set_ai_rule_enabled(session, rule_id, not rule.enabled)
    if updated is not None:
        await repo.add_audit(
            session,
            action="ai_rule_enabled" if updated.enabled else "ai_rule_disabled",
            actor_id=callback.from_user.id,
            chat_id=updated.chat_id,
            details=f"ai_rule={rule_id}",
        )
    text, markup = await _ai_rule_view(session, rule_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("airuledel:"), IsAdmin())
async def cb_ai_rule_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    rule_id = _int_tail(callback.data)
    if rule_id is None:
        await callback.answer("Некорректное AI-правило", show_alert=True)
        return
    rule = await repo.get_ai_rule(session, rule_id)
    if rule is None:
        await callback.answer("AI-правило не найдено", show_alert=True)
        return
    chat_id = rule.chat_id
    await repo.delete_ai_rule(session, rule_id)
    await repo.add_audit(
        session,
        action="ai_rule_deleted",
        actor_id=callback.from_user.id,
        chat_id=chat_id,
        details=f"ai_rule={rule_id}",
    )
    text, markup = await _chat_ai_rules_view(session, chat_id)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("airuleadd:chats:"), IsAdmin())
async def cb_ai_rule_add_chats(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        page = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        page = 0
    chats = await repo.list_chats(session)
    text, markup = _add_chats_view(chats, kind="airule", page=page)
    await _edit(callback, text, markup)


@router.callback_query(F.data.startswith("airuleadd:chat:"), IsAdmin())
async def cb_ai_rule_add_chat(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = _int_tail(callback.data)
    if chat_id is None:
        await callback.answer("Некорректный чат", show_alert=True)
        return
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return
    topics = await repo.list_topics(session, chat_id)
    text = f"<b>➕ Новое AI-правило</b>\n\nЧат: {_chat_label(chat)}\n\nВыберите область действия:"
    await _edit(callback, text, kb.add_scope(chat_id, topics, kind="airule"))


@router.callback_query(F.data.startswith("airuleadd:scope:"), IsAdmin())
async def cb_ai_rule_add_scope(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    parsed = _three_parts_ints(callback.data, "airuleadd:scope")
    if parsed is None:
        await callback.answer("Некорректная область", show_alert=True)
        return
    chat_id, thread_id = parsed
    chat = await repo.get_chat(session, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return

    existing = await repo.get_ai_rule_for_scope(session, chat_id, thread_id)
    scope = await _scope_label_named(session, chat_id, thread_id)
    await state.set_state(AddAiRuleFlow.instruction)
    await state.set_data({"chat_id": chat_id, "thread_id": -1 if thread_id is None else thread_id})
    if isinstance(callback.message, Message):
        if existing is not None:
            intro = "Новая инструкция заменит текущую для этой области."
        else:
            intro = "Опишите, что AI должен считать нарушением."
        await callback.message.answer(
            f"{intro}\n\n"
            "Например: «Удаляй рекламу, спам, приглашения в другие чаты "
            "и продажу услуг».\n\n"
            "AI будет отвечать DELETE или KEEP и удалять только нарушения. "
            "Каждое удаление придёт вам в личку.\n\n"
            f"Чат: {escape(chat.title or str(chat_id))}\n"
            f"Область: {scope}\n\n"
            "Отправьте инструкцию одним сообщением (до 2000 символов).",
            reply_markup=kb.cancel_flow(),
        )
    await callback.answer()


@router.message(
    AddAiRuleFlow.instruction,
    IsAdmin(),
    IsPrivateChat(),
    F.text,
    ~F.text.startswith("/"),
)
async def add_ai_rule_instruction(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    try:
        chat_id = int(data["chat_id"])
        thread_raw = int(data["thread_id"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await message.answer("Состояние добавления сброшено. Начните заново.")
        return

    instruction = (message.text or "").strip()
    if len(instruction) < 10:
        await message.answer(
            "❌ Инструкция слишком короткая. Опишите правило подробнее (минимум 10 символов).",
            reply_markup=kb.cancel_flow(),
        )
        return
    if len(instruction) > 2000:
        await message.answer(
            "❌ Инструкция длиннее 2000 символов. Сократите её.",
            reply_markup=kb.cancel_flow(),
        )
        return

    thread_id = None if thread_raw < 0 else thread_raw
    existing = await repo.get_ai_rule_for_scope(session, chat_id, thread_id)
    rule = await repo.add_ai_rule(
        session,
        chat_id=chat_id,
        thread_id=thread_id,
        instruction=instruction,
        created_by=message.from_user.id if message.from_user else None,
    )
    await repo.add_audit(
        session,
        action="ai_rule_updated" if existing is not None else "ai_rule_added",
        actor_id=message.from_user.id if message.from_user else None,
        chat_id=chat_id,
        details=f"ai_rule={rule.id} thread={thread_id}",
    )
    await state.clear()
    text, markup = await _ai_rule_view(session, rule.id)
    await message.answer(f"✅ AI-правило <code>#{rule.id}</code> сохранено.")
    await message.answer(text, reply_markup=markup)


# ---------------------------------------------------------------------------
# Flow controls
# ---------------------------------------------------------------------------


@router.message(Command("cancel"), IsAdmin(), IsPrivateChat(), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())


@router.callback_query(F.data == "flow:cancel", IsAdmin())
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit(callback, "Действие отменено.", main_menu())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_chats_view(chats: list[Chat], *, kind: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    page = max(page, 0)
    total = len(chats)
    start = page * CHATS_PER_PAGE
    items = chats[start : start + CHATS_PER_PAGE]
    has_next = start + CHATS_PER_PAGE < total
    title = "фильтра" if kind == "word" else "правила"
    if kind == "airule":
        title = "AI-правила"
    lines = [f"<b>➕ Выбор чата для {title}</b>", ""]
    if not chats:
        lines.append(
            "Чатов пока нет. Напишите любое сообщение в группе, где есть бот, и чат появится здесь."
        )
    for chat in items:
        lines.append(f"• {_chat_label(chat)}")
    return "\n".join(lines), kb.add_chats(
        items, kind=kind, page=page, has_prev=page > 0, has_next=has_next
    )


async def _resolve_user(session: AsyncSession, raw: str) -> tuple[int | None, str | None]:
    value = raw.strip()
    if not value:
        return None, "Укажите ID или @username."
    if value.startswith("@"):
        user = await repo.find_user_by_username(session, value)
    else:
        try:
            user_id = int(value)
        except ValueError:
            return None, "Нужен числовой ID или @username."
        user = await repo.get_user(session, user_id)
    if user is None:
        return None, "Пользователь не найден в базе."
    if user.is_bot:
        return None, "Нельзя выбрать бота."
    return user.id, None


def _int_tail(data: str | None) -> int | None:
    parts = (data or "").split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[-1])
    except ValueError:
        return None


def _two_ints(data: str | None) -> tuple[int, int] | None:
    parts = (data or "").split(":")
    if len(parts) < 3:
        return None
    try:
        return int(parts[-2]), int(parts[-1])
    except ValueError:
        return None


def _three_parts_ints(data: str | None, prefix: str) -> tuple[int, int | None] | None:
    parts = (data or "").split(":")
    expected = prefix.split(":")
    if parts[: len(expected)] != expected or len(parts) != len(expected) + 2:
        return None
    try:
        chat_id = int(parts[-2])
        thread_raw = int(parts[-1])
    except ValueError:
        return None
    return chat_id, None if thread_raw < 0 else thread_raw


async def _edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as exc:
            if "message is not modified" not in exc.message:
                await message.answer(text, reply_markup=markup)
    await callback.answer()
