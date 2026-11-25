from __future__ import annotations

from aiogram.types import Chat as TgChat
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.handlers import panel

CHAT_ID = -1009876543210


async def _seed(session: AsyncSession) -> None:
    await repo.upsert_chat(
        session,
        TgChat(id=CHAT_ID, type="supergroup", title="Тестовый чат", is_forum=True),
    )
    await repo.enable_chat(session, CHAT_ID, actor_id=1, all_topics=True)
    await repo.upsert_topic(session, CHAT_ID, 1, "Общее")
    await repo.upsert_topic(session, CHAT_ID, 2, "Флуд")
    await repo.add_word_filter(
        session,
        chat_id=CHAT_ID,
        thread_id=2,
        pattern="спам",
        match_type="word",
        case_sensitive=False,
        created_by=1,
    )
    rule = await repo.add_rule(
        session,
        chat_id=CHAT_ID,
        thread_id=None,
        target_user_id=None,
        created_by=1,
    )
    await repo.upsert_user(session, TgUser(id=42, is_bot=False, first_name="Исключённый"))
    await repo.add_exception(session, rule.id, 42)
    await repo.add_ai_rule(
        session,
        chat_id=CHAT_ID,
        thread_id=2,
        instruction="Удаляй спам и рекламу",
    )


async def test_chats_view_renders(session: AsyncSession) -> None:
    await _seed(session)
    text, markup = await panel._chats_view(session, 0)
    assert "Тестовый чат" in text
    assert "все темы" in text
    assert markup.inline_keyboard


async def test_chat_view_counts(session: AsyncSession) -> None:
    await _seed(session)
    text, _ = await panel._chat_view(session, CHAT_ID)
    assert "Тем в базе: 2" in text
    assert "Фильтров: 1" in text
    assert "Правил: 1" in text
    assert "AI-правил: 1" in text


async def test_topics_view_marks_all_topics_enabled(session: AsyncSession) -> None:
    await _seed(session)
    text, markup = await panel._topics_view(session, CHAT_ID)
    assert text.count("включена") == 2
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert f"topicv:{CHAT_ID}:1" in callbacks
    assert f"topicv:{CHAT_ID}:2" in callbacks


async def test_topic_view_under_all_topics(session: AsyncSession) -> None:
    await _seed(session)
    text, _ = await panel._topic_view(session, CHAT_ID, 1)
    assert "Мониторинг: включён" in text


async def test_words_and_rules_views(session: AsyncSession) -> None:
    await _seed(session)
    words_text, words_markup = await panel._words_view(session)
    rules_text, rules_markup = await panel._rules_view(session)
    assert "спам" in words_text
    assert "все пользователи" in rules_text
    assert words_markup.inline_keyboard
    assert rules_markup.inline_keyboard


async def test_ai_rules_view_uses_topic_title(session: AsyncSession) -> None:
    await _seed(session)
    text, markup = await panel._ai_rules_view(session)

    assert "AI-модерация" in text
    assert "тема «Флуд»" in text
    assert "тема 2" not in text
    assert markup.inline_keyboard
