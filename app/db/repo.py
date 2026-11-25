from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram.types import Chat as TgChat
from aiogram.types import User as TgUser
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AiRule,
    AuditLog,
    Chat,
    ChatUser,
    Dialog,
    DialogMessage,
    ModerationRule,
    RuleException,
    Topic,
    User,
    WordFilter,
    utcnow,
)

_ModelT = TypeVar("_ModelT")


def _is_sqlite(session: AsyncSession) -> bool:
    bind = session.bind
    return bind is not None and bind.dialect.name == "sqlite"


# ---------------------------------------------------------------------------
# Users and chats
# ---------------------------------------------------------------------------


async def _get_or_insert(
    session: AsyncSession,
    instance: _ModelT,
    fetch: Callable[[], Awaitable[_ModelT | None]],
) -> tuple[_ModelT, bool]:
    """Return an existing row or insert ``instance`` (non-SQLite backends).

    SQLite uses atomic ``INSERT ... ON CONFLICT`` statements instead, which
    cannot lose the check-then-insert race at all. Here the insert runs inside
    a SAVEPOINT: if a concurrent handler wins the race, only that statement is
    rolled back and the existing row is fetched.
    """
    existing = await fetch()
    if existing is not None:
        return existing, False

    try:
        async with session.begin_nested():
            session.add(instance)
    except IntegrityError:
        if instance in session:
            session.expunge(instance)
        existing = await fetch()
        if existing is None:
            raise
        return existing, False
    return instance, True


async def upsert_user(
    session: AsyncSession,
    tg_user: TgUser,
    *,
    started_bot: bool | None = None,
) -> User:
    now = utcnow()
    values = {
        "id": tg_user.id,
        "username": tg_user.username,
        "first_name": tg_user.first_name,
        "last_name": tg_user.last_name,
        "is_bot": tg_user.is_bot,
        "started_bot": bool(started_bot),
        "first_seen_at": now,
        "last_seen_at": now,
    }
    updates: dict[str, object] = {
        "username": tg_user.username,
        "first_name": tg_user.first_name,
        "last_name": tg_user.last_name,
        "last_seen_at": now,
    }
    if started_bot:
        updates["started_bot"] = True

    if _is_sqlite(session):
        # One statement: no window between SELECT and INSERT for a concurrent
        # update to register the same user and cause a UNIQUE violation.
        await session.execute(
            sqlite_insert(User)
            .values(**values)
            .on_conflict_do_update(index_elements=[User.id], set_=updates)
        )
        user = await session.get(User, tg_user.id, populate_existing=True)
    else:
        user, created = await _get_or_insert(
            session,
            User(**values),
            lambda: session.get(User, tg_user.id),
        )
        if not created:
            for key, value in updates.items():
                setattr(user, key, value)
            await session.flush()
    if user is None:
        raise RuntimeError(f"User {tg_user.id} was not stored")
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def find_user_by_username(session: AsyncSession, username: str) -> User | None:
    username = username.lstrip("@").strip()
    if not username:
        return None
    return await session.scalar(select(User).where(func.lower(User.username) == username.lower()))


async def list_users(session: AsyncSession, *, offset: int = 0, limit: int = 20) -> list[User]:
    result = await session.scalars(
        select(User).order_by(User.last_seen_at.desc()).offset(offset).limit(limit)
    )
    return list(result)


async def count_users(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(User)) or 0)


async def list_users_for_dialog(
    session: AsyncSession, *, offset: int = 0, limit: int = 20
) -> list[User]:
    result = await session.scalars(
        select(User)
        .where(User.is_bot.is_(False))
        .order_by(User.last_seen_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result)


async def count_users_for_dialog(session: AsyncSession) -> int:
    return int(
        await session.scalar(select(func.count()).select_from(User).where(User.is_bot.is_(False)))
        or 0
    )


async def list_all_users(session: AsyncSession) -> list[User]:
    result = await session.scalars(select(User).order_by(User.id))
    return list(result)


async def upsert_chat(
    session: AsyncSession,
    tg_chat: TgChat,
    *,
    added_by: int | None = None,
) -> Chat:
    values = {
        "id": tg_chat.id,
        "title": tg_chat.title,
        "type": tg_chat.type,
        "is_forum": bool(getattr(tg_chat, "is_forum", False)),
        "added_by": added_by,
    }
    updates = {
        "title": tg_chat.title,
        "type": tg_chat.type,
        "is_forum": bool(getattr(tg_chat, "is_forum", False)),
    }

    if _is_sqlite(session):
        await session.execute(
            sqlite_insert(Chat)
            .values(**values)
            .on_conflict_do_update(index_elements=[Chat.id], set_=updates)
        )
        chat = await session.get(Chat, tg_chat.id, populate_existing=True)
    else:
        chat, created = await _get_or_insert(
            session,
            Chat(**values),
            lambda: session.get(Chat, tg_chat.id),
        )
        if not created:
            for key, value in updates.items():
                setattr(chat, key, value)
            await session.flush()
    if chat is None:
        raise RuntimeError(f"Chat {tg_chat.id} was not stored")
    return chat


async def get_chat(session: AsyncSession, chat_id: int) -> Chat | None:
    return await session.get(Chat, chat_id)


async def list_chats(session: AsyncSession) -> list[Chat]:
    result = await session.scalars(select(Chat).order_by(Chat.title, Chat.id))
    return list(result)


async def count_topics(session: AsyncSession, chat_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(Topic).where(Topic.chat_id == chat_id)
        )
        or 0
    )


async def get_topic(session: AsyncSession, chat_id: int, thread_id: int) -> Topic | None:
    return await session.scalar(
        select(Topic).where(Topic.chat_id == chat_id, Topic.thread_id == thread_id)
    )


async def count_word_filters(session: AsyncSession, chat_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(WordFilter).where(WordFilter.chat_id == chat_id)
        )
        or 0
    )


async def count_rules(session: AsyncSession, chat_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(ModerationRule)
            .where(ModerationRule.chat_id == chat_id)
        )
        or 0
    )


async def set_monitoring(session: AsyncSession, chat_id: int, enabled: bool) -> Chat | None:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        return None
    chat.is_monitored = enabled
    await session.flush()
    return chat


async def set_monitor_all_topics(
    session: AsyncSession,
    chat_id: int,
    enabled: bool,
) -> Chat | None:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        return None
    chat.monitor_all_topics = enabled
    if enabled:
        chat.is_monitored = True
        await session.execute(
            update(Topic)
            .where(Topic.chat_id == chat_id)
            .values(is_monitored=True, is_excluded=False)
        )
    else:
        await session.execute(
            update(Topic).where(Topic.chat_id == chat_id).values(is_monitored=False)
        )
    await session.flush()
    return chat


async def upsert_topic(
    session: AsyncSession,
    chat_id: int,
    thread_id: int,
    title: str | None = None,
) -> Topic:
    if _is_sqlite(session):
        statement = sqlite_insert(Topic).values(
            chat_id=chat_id,
            thread_id=thread_id,
            title=title,
        )
        if title:
            statement = statement.on_conflict_do_update(
                index_elements=["chat_id", "thread_id"],
                set_={"title": title},
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=["chat_id", "thread_id"])
        await session.execute(statement)
        topic = await session.scalar(
            select(Topic)
            .where(Topic.chat_id == chat_id, Topic.thread_id == thread_id)
            .execution_options(populate_existing=True)
        )
    else:
        topic, created = await _get_or_insert(
            session,
            Topic(chat_id=chat_id, thread_id=thread_id, title=title),
            lambda: get_topic(session, chat_id, thread_id),
        )
        if not created and title:
            topic.title = title
            await session.flush()
    if topic is None:
        raise RuntimeError(f"Topic {chat_id}/{thread_id} was not stored")
    return topic


async def touch_chat_user(session: AsyncSession, chat_id: int, user_id: int) -> None:
    now = utcnow()
    if _is_sqlite(session):
        await session.execute(
            sqlite_insert(ChatUser)
            .values(
                chat_id=chat_id,
                user_id=user_id,
                first_seen_at=now,
                last_seen_at=now,
                message_count=1,
            )
            .on_conflict_do_update(
                index_elements=["chat_id", "user_id"],
                set_={
                    "last_seen_at": now,
                    "message_count": ChatUser.message_count + 1,
                },
            )
        )
        return

    row, created = await _get_or_insert(
        session,
        ChatUser(
            chat_id=chat_id,
            user_id=user_id,
            first_seen_at=now,
            last_seen_at=now,
            message_count=1,
        ),
        lambda: session.get(ChatUser, (chat_id, user_id)),
    )
    if not created:
        row.last_seen_at = now
        row.message_count += 1
        await session.flush()


async def enable_chat(
    session: AsyncSession,
    chat_id: int,
    *,
    actor_id: int | None,
    all_topics: bool,
) -> Chat:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise ValueError("Chat is not registered")
    chat.is_monitored = True
    chat.monitor_all_topics = all_topics
    chat.added_by = actor_id
    if all_topics:
        await session.execute(
            update(Topic)
            .where(Topic.chat_id == chat_id)
            .values(is_excluded=False, is_monitored=True)
        )
    await session.flush()
    return chat


async def enable_topic(
    session: AsyncSession,
    chat_id: int,
    thread_id: int,
    *,
    actor_id: int | None,
) -> Topic:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise ValueError("Chat is not registered")
    chat.is_monitored = True
    topic = await upsert_topic(session, chat_id, thread_id)
    topic.is_monitored = True
    topic.is_excluded = False
    await session.flush()
    return topic


async def disable_topic(session: AsyncSession, chat_id: int, thread_id: int) -> bool:
    topic = await session.scalar(
        select(Topic).where(Topic.chat_id == chat_id, Topic.thread_id == thread_id)
    )
    if topic is None:
        return False
    topic.is_monitored = False
    topic.is_excluded = True
    await session.flush()
    return True


async def rename_topic(
    session: AsyncSession,
    chat_id: int,
    thread_id: int,
    title: str | None,
) -> Topic | None:
    """Set a human-friendly title for a topic (empty string clears it)."""
    topic = await get_topic(session, chat_id, thread_id)
    if topic is None:
        return None
    cleaned = (title or "").strip()
    topic.title = cleaned[:255] or None
    await session.flush()
    return topic


async def disable_chat(session: AsyncSession, chat_id: int) -> bool:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        return False
    chat.is_monitored = False
    await session.flush()
    return True


async def is_monitored(session: AsyncSession, chat_id: int, thread_id: int | None) -> bool:
    chat = await session.get(Chat, chat_id)
    if chat is None or not chat.is_monitored:
        return False
    if thread_id is None:
        return True
    topic = await session.scalar(
        select(Topic).where(Topic.chat_id == chat_id, Topic.thread_id == thread_id)
    )
    if topic is not None and topic.is_excluded:
        return False
    if chat.monitor_all_topics:
        return True
    return bool(topic and topic.is_monitored)


async def list_topics(session: AsyncSession, chat_id: int) -> list[Topic]:
    result = await session.scalars(
        select(Topic).where(Topic.chat_id == chat_id).order_by(Topic.thread_id)
    )
    return list(result)


# ---------------------------------------------------------------------------
# Word filters
# ---------------------------------------------------------------------------


async def add_word_filter(
    session: AsyncSession,
    *,
    chat_id: int,
    thread_id: int | None,
    pattern: str,
    match_type: str,
    case_sensitive: bool,
    created_by: int | None,
) -> WordFilter:
    item = WordFilter(
        chat_id=chat_id,
        thread_id=thread_id,
        pattern=pattern,
        match_type=match_type,
        case_sensitive=case_sensitive,
        created_by=created_by,
    )
    session.add(item)
    await session.flush()
    return item


async def list_word_filters(
    session: AsyncSession,
    *,
    chat_id: int | None = None,
    include_disabled: bool = True,
) -> list[WordFilter]:
    stmt = select(WordFilter).order_by(WordFilter.id)
    if chat_id is not None:
        stmt = stmt.where(WordFilter.chat_id == chat_id)
    if not include_disabled:
        stmt = stmt.where(WordFilter.enabled.is_(True))
    result = await session.scalars(stmt)
    return list(result)


async def get_word_filter(session: AsyncSession, filter_id: int) -> WordFilter | None:
    return await session.get(WordFilter, filter_id)


async def delete_word_filter(
    session: AsyncSession,
    filter_id: int,
    *,
    chat_id: int | None = None,
) -> bool:
    item = await session.get(WordFilter, filter_id)
    if item is None or (chat_id is not None and item.chat_id != chat_id):
        return False
    await session.delete(item)
    await session.flush()
    return True


async def active_word_filters(
    session: AsyncSession,
    chat_id: int,
    thread_id: int | None,
) -> list[WordFilter]:
    stmt = select(WordFilter).where(
        WordFilter.chat_id == chat_id,
        WordFilter.enabled.is_(True),
    )
    if thread_id is None:
        stmt = stmt.where(WordFilter.thread_id.is_(None))
    else:
        stmt = stmt.where(or_(WordFilter.thread_id.is_(None), WordFilter.thread_id == thread_id))
    result = await session.scalars(stmt.order_by(WordFilter.id))
    return list(result)


# ---------------------------------------------------------------------------
# Moderation rules
# ---------------------------------------------------------------------------


async def add_rule(
    session: AsyncSession,
    *,
    chat_id: int,
    thread_id: int | None,
    target_user_id: int | None,
    created_by: int | None,
    action: str = "delete",
) -> ModerationRule:
    rule = ModerationRule(
        chat_id=chat_id,
        thread_id=thread_id,
        target_user_id=target_user_id,
        created_by=created_by,
        action=action,
    )
    session.add(rule)
    await session.flush()
    return rule


async def get_rule(session: AsyncSession, rule_id: int) -> ModerationRule | None:
    return await session.get(ModerationRule, rule_id)


async def list_rules(
    session: AsyncSession,
    *,
    chat_id: int | None = None,
    include_disabled: bool = True,
) -> list[ModerationRule]:
    stmt = select(ModerationRule).order_by(ModerationRule.id)
    if chat_id is not None:
        stmt = stmt.where(ModerationRule.chat_id == chat_id)
    if not include_disabled:
        stmt = stmt.where(ModerationRule.enabled.is_(True))
    result = await session.scalars(stmt)
    return list(result)


async def delete_rule(session: AsyncSession, rule_id: int, *, chat_id: int | None = None) -> bool:
    rule = await session.get(ModerationRule, rule_id)
    if rule is None or (chat_id is not None and rule.chat_id != chat_id):
        return False
    await session.delete(rule)
    await session.flush()
    return True


async def active_rules(
    session: AsyncSession,
    chat_id: int,
    thread_id: int | None,
) -> list[ModerationRule]:
    stmt = select(ModerationRule).where(
        ModerationRule.chat_id == chat_id,
        ModerationRule.enabled.is_(True),
    )
    if thread_id is None:
        stmt = stmt.where(ModerationRule.thread_id.is_(None))
    else:
        stmt = stmt.where(
            or_(ModerationRule.thread_id.is_(None), ModerationRule.thread_id == thread_id)
        )
    result = await session.scalars(stmt.order_by(ModerationRule.id))
    return list(result)


async def add_exception(session: AsyncSession, rule_id: int, user_id: int) -> bool:
    if _is_sqlite(session):
        result = await session.execute(
            sqlite_insert(RuleException)
            .values(rule_id=rule_id, user_id=user_id)
            .on_conflict_do_nothing(index_elements=["rule_id", "user_id"])
        )
        return bool(result.rowcount)

    existing = await session.scalar(
        select(RuleException).where(
            RuleException.rule_id == rule_id, RuleException.user_id == user_id
        )
    )
    if existing is not None:
        return False
    session.add(RuleException(rule_id=rule_id, user_id=user_id))
    await session.flush()
    return True


async def remove_exception(session: AsyncSession, rule_id: int, user_id: int) -> bool:
    item = await session.scalar(
        select(RuleException).where(
            RuleException.rule_id == rule_id, RuleException.user_id == user_id
        )
    )
    if item is None:
        return False
    await session.delete(item)
    await session.flush()
    return True


async def exception_user_ids(session: AsyncSession, rule_id: int) -> set[int]:
    result = await session.scalars(
        select(RuleException.user_id).where(RuleException.rule_id == rule_id)
    )
    return set(result)


async def is_exception(session: AsyncSession, rule_id: int, user_id: int) -> bool:
    item = await session.scalar(
        select(RuleException).where(
            RuleException.rule_id == rule_id, RuleException.user_id == user_id
        )
    )
    return item is not None


# ---------------------------------------------------------------------------
# AI moderation rules
# ---------------------------------------------------------------------------


def _ai_rule_scope_clause(chat_id: int, thread_id: int | None) -> tuple[object, ...]:
    clause = AiRule.thread_id.is_(None) if thread_id is None else AiRule.thread_id == thread_id
    return (AiRule.chat_id == chat_id, clause)


async def get_ai_rule_for_scope(
    session: AsyncSession,
    chat_id: int,
    thread_id: int | None,
) -> AiRule | None:
    stmt = select(AiRule).where(*_ai_rule_scope_clause(chat_id, thread_id))
    return await session.scalar(stmt.order_by(AiRule.id).limit(1))


async def add_ai_rule(
    session: AsyncSession,
    *,
    chat_id: int,
    thread_id: int | None,
    instruction: str,
    action: str = "delete_notify",
    created_by: int | None = None,
) -> AiRule:
    """Create a rule for the scope or replace the instruction of an existing one."""
    rule = await get_ai_rule_for_scope(session, chat_id, thread_id)
    if rule is None:
        rule = AiRule(
            chat_id=chat_id,
            thread_id=thread_id,
            instruction=instruction,
            action=action,
            created_by=created_by,
        )
        session.add(rule)
    else:
        rule.instruction = instruction
        rule.action = action
        rule.enabled = True
    await session.flush()
    return rule


async def get_ai_rule(session: AsyncSession, rule_id: int) -> AiRule | None:
    return await session.get(AiRule, rule_id)


async def list_ai_rules(
    session: AsyncSession,
    *,
    chat_id: int | None = None,
    include_disabled: bool = True,
) -> list[AiRule]:
    stmt = select(AiRule).order_by(AiRule.id)
    if chat_id is not None:
        stmt = stmt.where(AiRule.chat_id == chat_id)
    if not include_disabled:
        stmt = stmt.where(AiRule.enabled.is_(True))
    result = await session.scalars(stmt)
    return list(result)


async def count_ai_rules(session: AsyncSession, chat_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(AiRule).where(AiRule.chat_id == chat_id)
        )
        or 0
    )


async def set_ai_rule_enabled(
    session: AsyncSession,
    rule_id: int,
    enabled: bool,
) -> AiRule | None:
    rule = await session.get(AiRule, rule_id)
    if rule is None:
        return None
    rule.enabled = enabled
    await session.flush()
    return rule


async def delete_ai_rule(
    session: AsyncSession,
    rule_id: int,
    *,
    chat_id: int | None = None,
) -> bool:
    rule = await session.get(AiRule, rule_id)
    if rule is None or (chat_id is not None and rule.chat_id != chat_id):
        return False
    await session.delete(rule)
    await session.flush()
    return True


async def active_ai_rule(
    session: AsyncSession,
    chat_id: int,
    thread_id: int | None,
) -> AiRule | None:
    """Return the enabled rule for the message scope.

    A topic-specific rule takes precedence over the chat-wide rule.
    """
    if thread_id is not None:
        topic_rule = await session.scalar(
            select(AiRule)
            .where(
                AiRule.chat_id == chat_id,
                AiRule.thread_id == thread_id,
                AiRule.enabled.is_(True),
            )
            .order_by(AiRule.id)
            .limit(1)
        )
        if topic_rule is not None:
            return topic_rule
    return await session.scalar(
        select(AiRule)
        .where(
            AiRule.chat_id == chat_id,
            AiRule.thread_id.is_(None),
            AiRule.enabled.is_(True),
        )
        .order_by(AiRule.id)
        .limit(1)
    )


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


async def stop_active_dialogs(
    session: AsyncSession,
    *,
    admin_id: int | None = None,
    peer_id: int | None = None,
) -> int:
    stmt = select(Dialog).where(Dialog.status.in_(("active", "paused")))
    if admin_id is not None:
        stmt = stmt.where(Dialog.admin_id == admin_id)
    if peer_id is not None:
        stmt = stmt.where(Dialog.peer_id == peer_id)
    dialogs = list(await session.scalars(stmt))
    now = utcnow()
    for dialog in dialogs:
        dialog.status = "stopped"
        dialog.ended_at = now
    await session.flush()
    return len(dialogs)


async def create_dialog(
    session: AsyncSession,
    *,
    admin_id: int,
    peer_id: int,
    mode: str,
    instruction: str | None,
    model: str | None,
    chat_id: int | None = None,
    thread_id: int | None = None,
) -> Dialog:
    await stop_active_dialogs(session, admin_id=admin_id)
    await stop_active_dialogs(session, peer_id=peer_id)
    dialog = Dialog(
        admin_id=admin_id,
        peer_id=peer_id,
        chat_id=chat_id,
        thread_id=thread_id,
        mode=mode,
        instruction=instruction,
        model=model,
    )
    session.add(dialog)
    await session.flush()
    return dialog


async def get_dialog(session: AsyncSession, dialog_id: int) -> Dialog | None:
    return await session.get(Dialog, dialog_id)


async def get_active_dialog_for_admin(session: AsyncSession, admin_id: int) -> Dialog | None:
    return await session.scalar(
        select(Dialog)
        .where(Dialog.admin_id == admin_id, Dialog.status == "active")
        .order_by(Dialog.id.desc())
    )


async def get_active_dialog_for_peer(
    session: AsyncSession,
    peer_id: int,
    *,
    private_only: bool = False,
) -> Dialog | None:
    stmt = select(Dialog).where(Dialog.peer_id == peer_id, Dialog.status == "active")
    if private_only:
        stmt = stmt.where(Dialog.chat_id.is_(None))
    return await session.scalar(stmt.order_by(Dialog.id.desc()))


async def get_active_topic_dialog(
    session: AsyncSession,
    chat_id: int,
    thread_id: int,
) -> Dialog | None:
    """Active dialog that runs in a forum topic of ``chat_id``."""
    return await session.scalar(
        select(Dialog)
        .where(
            Dialog.chat_id == chat_id,
            Dialog.thread_id == thread_id,
            Dialog.status == "active",
        )
        .order_by(Dialog.id.desc())
    )


async def list_active_dialogs(
    session: AsyncSession,
    *,
    admin_id: int | None = None,
    include_paused: bool = False,
) -> list[Dialog]:
    statuses = ("active", "paused") if include_paused else ("active",)
    stmt = select(Dialog).where(Dialog.status.in_(statuses)).order_by(Dialog.id.desc())
    if admin_id is not None:
        stmt = stmt.where(Dialog.admin_id == admin_id)
    return list(await session.scalars(stmt))


async def add_dialog_message(
    session: AsyncSession,
    *,
    dialog_id: int,
    direction: str,
    text: str,
    tg_message_id: int | None = None,
) -> DialogMessage:
    item = DialogMessage(
        dialog_id=dialog_id,
        direction=direction,
        text=text,
        tg_message_id=tg_message_id,
    )
    session.add(item)
    await session.flush()
    return item


async def dialog_messages(
    session: AsyncSession,
    dialog_id: int,
    *,
    limit: int = 50,
) -> list[DialogMessage]:
    result = await session.scalars(
        select(DialogMessage)
        .where(DialogMessage.dialog_id == dialog_id)
        .order_by(DialogMessage.id.desc())
        .limit(limit)
    )
    return list(reversed(list(result)))


async def set_dialog_mode(session: AsyncSession, dialog_id: int, mode: str) -> Dialog | None:
    dialog = await session.get(Dialog, dialog_id)
    if dialog is None:
        return None
    dialog.mode = mode
    await session.flush()
    return dialog


async def set_dialog_instruction(
    session: AsyncSession, dialog_id: int, instruction: str | None
) -> Dialog | None:
    dialog = await session.get(Dialog, dialog_id)
    if dialog is None:
        return None
    dialog.instruction = instruction
    await session.flush()
    return dialog


async def stop_dialog(session: AsyncSession, dialog_id: int) -> Dialog | None:
    dialog = await session.get(Dialog, dialog_id)
    if dialog is None or dialog.status not in {"active", "paused"}:
        return None
    dialog.status = "stopped"
    dialog.ended_at = utcnow()
    await session.flush()
    return dialog


async def pause_dialog(session: AsyncSession, dialog_id: int) -> Dialog | None:
    """Temporarily stop relaying/AI for a dialog without ending it."""
    dialog = await session.get(Dialog, dialog_id)
    if dialog is None or dialog.status != "active":
        return None
    dialog.status = "paused"
    await session.flush()
    return dialog


async def resume_dialog(session: AsyncSession, dialog_id: int) -> Dialog | None:
    dialog = await session.get(Dialog, dialog_id)
    if dialog is None or dialog.status != "paused":
        return None
    dialog.status = "active"
    await session.flush()
    return dialog


# ---------------------------------------------------------------------------
# Audit and statistics
# ---------------------------------------------------------------------------


async def add_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_id: int | None = None,
    chat_id: int | None = None,
    details: str | None = None,
) -> AuditLog:
    item = AuditLog(actor_id=actor_id, chat_id=chat_id, action=action, details=details)
    session.add(item)
    await session.flush()
    return item


async def recent_audit(session: AsyncSession, *, limit: int = 10) -> list[AuditLog]:
    result = await session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    return list(result)


async def count_rows(session: AsyncSession, model: object) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def count_deletions(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "message_deleted")
        )
        or 0
    )


async def count_active_dialogs(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(Dialog).where(Dialog.status == "active")
        )
        or 0
    )


async def stats(session: AsyncSession) -> dict[str, int]:
    return {
        "users": await count_rows(session, User),
        "chats": await count_rows(session, Chat),
        "topics": await count_rows(session, Topic),
        "filters": await count_rows(session, WordFilter),
        "rules": await count_rows(session, ModerationRule),
        "dialogs": await count_active_dialogs(session),
        "deletions": await count_deletions(session),
    }
