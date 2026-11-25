from __future__ import annotations

from aiogram.types import Chat as TgChat
from aiogram.types import User as TgUser
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import Chat, ChatUser, ModerationRule, User


async def _insert_raw(session: AsyncSession, table: Table, **values: object) -> None:
    connection = await session.connection()
    await connection.execute(table.insert().values(**values))


def _tg_user(user_id: int = 1, username: str | None = "ivan") -> TgUser:
    return TgUser(id=user_id, is_bot=False, first_name="Ivan", username=username)


async def test_get_or_insert_recovers_when_row_appears_after_lookup(
    session: AsyncSession,
) -> None:
    await _insert_raw(session, Chat.__table__, id=1, title="from other handler")

    first_lookup = True

    async def fetch() -> Chat | None:
        nonlocal first_lookup
        if first_lookup:
            first_lookup = False
            return None
        return await session.get(Chat, 1)

    chat, created = await repo._get_or_insert(
        session,
        Chat(id=1, title="mine", type="supergroup"),
        fetch,
    )

    assert created is False
    assert chat.title == "from other handler"


async def test_session_still_usable_after_insert_conflict(session: AsyncSession) -> None:
    await _insert_raw(session, User.__table__, id=42, username="winner")

    first_lookup = True

    async def fetch() -> User | None:
        nonlocal first_lookup
        if first_lookup:
            first_lookup = False
            return None
        return await session.get(User, 42)

    user, created = await repo._get_or_insert(session, User(id=42), fetch)
    assert created is False
    assert user.username == "winner"

    other = await repo.upsert_user(session, _tg_user(user_id=7))
    await session.commit()

    assert other.id == 7
    assert await repo.get_user(session, 7) is not None


async def test_upsert_user_creates_and_updates(session: AsyncSession) -> None:
    created = await repo.upsert_user(session, _tg_user(username="first"))
    first_seen = created.first_seen_at

    updated = await repo.upsert_user(session, _tg_user(username="second"), started_bot=True)

    assert updated.id == created.id
    assert updated.username == "second"
    assert updated.started_bot is True
    assert updated.first_seen_at == first_seen


async def test_upsert_user_does_not_reset_started_bot(session: AsyncSession) -> None:
    await repo.upsert_user(session, _tg_user(), started_bot=True)

    updated = await repo.upsert_user(session, _tg_user())

    assert updated.started_bot is True


async def test_touch_chat_user_counts_messages(session: AsyncSession) -> None:
    await repo.upsert_user(session, _tg_user())
    await repo.upsert_chat(session, TgChat(id=-100, type="supergroup", title="Chat"))

    await repo.touch_chat_user(session, -100, 1)
    await repo.touch_chat_user(session, -100, 1)

    row = await session.get(ChatUser, (-100, 1))
    assert row is not None
    assert row.message_count == 2


async def test_upsert_user_updates_row_created_outside_session(
    session: AsyncSession,
) -> None:
    await _insert_raw(session, User.__table__, id=99, username="other")

    user = await repo.upsert_user(session, _tg_user(user_id=99, username="mine"))

    assert user.username == "mine"
    assert await repo.count_users(session) == 1


async def test_upsert_topic_updates_title_on_conflict(session: AsyncSession) -> None:
    await repo.upsert_chat(session, TgChat(id=-200, type="supergroup", title="Chat"))

    first = await repo.upsert_topic(session, -200, 7, "old")
    second = await repo.upsert_topic(session, -200, 7, "new")

    assert second.id == first.id
    assert second.title == "new"


async def test_rename_topic_sets_and_clears_title(session: AsyncSession) -> None:
    await repo.upsert_chat(session, TgChat(id=-400, type="supergroup", title="Chat"))
    await repo.upsert_topic(session, -400, 11)

    renamed = await repo.rename_topic(session, -400, 11, "  Важное  ")

    assert renamed is not None
    assert renamed.title == "Важное"

    cleared = await repo.rename_topic(session, -400, 11, None)
    assert cleared is not None
    assert cleared.title is None

    empty = await repo.rename_topic(session, -400, 11, "   ")
    assert empty is not None
    assert empty.title is None


async def test_rename_topic_limits_length(session: AsyncSession) -> None:
    await repo.upsert_chat(session, TgChat(id=-401, type="supergroup", title="Chat"))
    await repo.upsert_topic(session, -401, 12)

    renamed = await repo.rename_topic(session, -401, 12, "x" * 400)

    assert renamed is not None
    assert renamed.title is not None
    assert len(renamed.title) == 255


async def test_rename_topic_returns_none_for_unknown_topic(session: AsyncSession) -> None:
    assert await repo.rename_topic(session, -402, 13, "нет темы") is None


async def test_add_exception_is_idempotent(session: AsyncSession) -> None:
    await repo.upsert_user(session, _tg_user(user_id=5))
    await repo.upsert_chat(session, TgChat(id=-300, type="supergroup", title="Chat"))
    rule = ModerationRule(chat_id=-300, action="delete")
    session.add(rule)
    await session.flush()

    assert await repo.add_exception(session, rule.id, 5) is True
    assert await repo.add_exception(session, rule.id, 5) is False
