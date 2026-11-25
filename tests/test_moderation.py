from __future__ import annotations

from aiogram.types import Chat as TgChat
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.services.moderation import evaluate_message

CHAT_ID = -1001234567890
USER_ID = 111
OTHER_USER_ID = 222


async def _prepare(session: AsyncSession) -> None:
    await repo.upsert_chat(
        session,
        TgChat(id=CHAT_ID, type="supergroup", title="Test", is_forum=True),
    )
    await repo.upsert_user(session, TgUser(id=USER_ID, is_bot=False, first_name="Alice"))
    await repo.upsert_user(session, TgUser(id=OTHER_USER_ID, is_bot=False, first_name="Bob"))


async def test_word_filter_deletes_in_matching_topic(session: AsyncSession) -> None:
    await _prepare(session)
    await repo.add_word_filter(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        pattern="спам",
        match_type="word",
        case_sensitive=False,
        created_by=1,
    )

    outcome = await evaluate_message(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        user_id=USER_ID,
        text="это спам",
        caption=None,
    )
    assert outcome.delete
    assert outcome.filter_id is not None


async def test_word_filter_does_not_apply_to_other_topic(session: AsyncSession) -> None:
    await _prepare(session)
    await repo.add_word_filter(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        pattern="спам",
        match_type="word",
        case_sensitive=False,
        created_by=1,
    )

    outcome = await evaluate_message(
        session,
        chat_id=CHAT_ID,
        thread_id=6,
        user_id=USER_ID,
        text="это спам",
        caption=None,
    )
    assert not outcome.delete


async def test_rule_targets_single_user(session: AsyncSession) -> None:
    await _prepare(session)
    rule = await repo.add_rule(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        target_user_id=USER_ID,
        created_by=1,
    )

    hit = await evaluate_message(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        user_id=USER_ID,
        text="привет",
        caption=None,
    )
    miss = await evaluate_message(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        user_id=OTHER_USER_ID,
        text="привет",
        caption=None,
    )
    assert hit.delete and hit.rule_id == rule.id
    assert not miss.delete


async def test_rule_exception_allows_user(session: AsyncSession) -> None:
    await _prepare(session)
    rule = await repo.add_rule(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        target_user_id=None,
        created_by=1,
    )
    await repo.add_exception(session, rule.id, USER_ID)

    allowed = await evaluate_message(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        user_id=USER_ID,
        text="привет",
        caption=None,
    )
    deleted = await evaluate_message(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        user_id=OTHER_USER_ID,
        text="привет",
        caption=None,
    )
    assert not allowed.delete
    assert deleted.delete


async def test_exception_on_first_rule_does_not_disable_second(session: AsyncSession) -> None:
    await _prepare(session)
    first = await repo.add_rule(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        target_user_id=USER_ID,
        created_by=1,
    )
    second = await repo.add_rule(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        target_user_id=None,
        created_by=1,
    )
    await repo.add_exception(session, first.id, USER_ID)

    outcome = await evaluate_message(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        user_id=USER_ID,
        text="привет",
        caption=None,
    )
    assert outcome.delete
    assert outcome.rule_id == second.id


async def test_monitoring_scope(session: AsyncSession) -> None:
    await _prepare(session)
    await repo.enable_topic(session, CHAT_ID, 5, actor_id=1)

    assert await repo.is_monitored(session, CHAT_ID, 5)
    assert not await repo.is_monitored(session, CHAT_ID, 6)

    await repo.enable_chat(session, CHAT_ID, actor_id=1, all_topics=True)
    assert await repo.is_monitored(session, CHAT_ID, 6)

    await repo.upsert_topic(session, CHAT_ID, 6)
    await repo.disable_topic(session, CHAT_ID, 6)
    assert not await repo.is_monitored(session, CHAT_ID, 6)
    assert await repo.is_monitored(session, CHAT_ID, 5)

    await repo.enable_topic(session, CHAT_ID, 6, actor_id=1)
    assert await repo.is_monitored(session, CHAT_ID, 6)


async def test_switching_off_all_topics_stops_new_topics(session: AsyncSession) -> None:
    await _prepare(session)
    await repo.enable_chat(session, CHAT_ID, actor_id=1, all_topics=True)
    assert await repo.is_monitored(session, CHAT_ID, 9)

    await repo.set_monitor_all_topics(session, CHAT_ID, False)
    assert not await repo.is_monitored(session, CHAT_ID, 9)

    await repo.enable_topic(session, CHAT_ID, 5, actor_id=1)
    assert await repo.is_monitored(session, CHAT_ID, 5)
    assert not await repo.is_monitored(session, CHAT_ID, 9)

    await repo.set_monitor_all_topics(session, CHAT_ID, True)
    assert await repo.is_monitored(session, CHAT_ID, 9)


async def test_deleting_rule_cascades_exceptions(session: AsyncSession) -> None:
    await _prepare(session)
    rule = await repo.add_rule(
        session,
        chat_id=CHAT_ID,
        thread_id=5,
        target_user_id=None,
        created_by=1,
    )
    await repo.add_exception(session, rule.id, USER_ID)
    assert await repo.exception_user_ids(session, rule.id) == {USER_ID}

    await repo.delete_rule(session, rule.id)
    assert await repo.exception_user_ids(session, rule.id) == set()
