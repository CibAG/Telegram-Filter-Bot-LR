from __future__ import annotations

from aiogram.types import Chat as TgChat
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.services.dialog_service import dialog_created_text, start_dialog

ADMIN_ID = 1
PEER_ID = 42


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        bot_token="123456:TEST",
        admin_ids=str(ADMIN_ID),
        openrouter_api_key="test-key",
    )


async def test_dialog_allows_user_without_start(session: AsyncSession) -> None:
    await repo.upsert_user(session, TgUser(id=PEER_ID, is_bot=False, first_name="Peer"))
    dialog, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=PEER_ID,
        mode="relay",
        instruction=None,
    )
    assert error is None
    assert dialog is not None

    peer = await repo.get_user(session, PEER_ID)
    assert peer is not None
    text = dialog_created_text(dialog, peer)
    assert "не нажимал /start" in text


async def test_dialog_creation_replaces_active_dialog(session: AsyncSession) -> None:
    await repo.upsert_user(
        session,
        TgUser(id=PEER_ID, is_bot=False, first_name="Peer"),
        started_bot=True,
    )
    first, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=PEER_ID,
        mode="relay",
        instruction=None,
    )
    assert error is None and first is not None

    second, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=PEER_ID,
        mode="ai",
        instruction="Будь вежлив",
    )
    assert error is None and second is not None

    await session.refresh(first)
    assert first.status == "stopped"
    active = await repo.get_active_dialog_for_admin(session, ADMIN_ID)
    assert active is not None and active.id == second.id
    assert active.mode == "ai"


async def test_pause_and_resume_dialog(session: AsyncSession) -> None:
    await repo.upsert_user(
        session,
        TgUser(id=PEER_ID, is_bot=False, first_name="Peer"),
        started_bot=True,
    )
    dialog, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=PEER_ID,
        mode="relay",
        instruction=None,
    )
    assert error is None and dialog is not None

    paused = await repo.pause_dialog(session, dialog.id)
    assert paused is not None and paused.status == "paused"
    assert await repo.get_active_dialog_for_peer(session, PEER_ID) is None
    assert await repo.get_active_dialog_for_admin(session, ADMIN_ID) is None

    listed = await repo.list_active_dialogs(session, admin_id=ADMIN_ID, include_paused=True)
    assert [item.id for item in listed] == [dialog.id]
    assert await repo.list_active_dialogs(session, admin_id=ADMIN_ID) == []

    resumed = await repo.resume_dialog(session, dialog.id)
    assert resumed is not None and resumed.status == "active"
    assert await repo.get_active_dialog_for_peer(session, PEER_ID) is not None


async def test_paused_dialog_can_be_stopped(session: AsyncSession) -> None:
    await repo.upsert_user(
        session,
        TgUser(id=PEER_ID, is_bot=False, first_name="Peer"),
        started_bot=True,
    )
    dialog, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=PEER_ID,
        mode="relay",
        instruction=None,
    )
    assert error is None and dialog is not None

    await repo.pause_dialog(session, dialog.id)
    stopped = await repo.stop_dialog(session, dialog.id)

    assert stopped is not None
    assert stopped.status == "stopped"
    assert stopped.ended_at is not None
    assert await repo.resume_dialog(session, dialog.id) is None


async def test_ai_mode_requires_openrouter_key(session: AsyncSession) -> None:
    await repo.upsert_user(
        session,
        TgUser(id=PEER_ID, is_bot=False, first_name="Peer"),
        started_bot=True,
    )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        bot_token="123456:TEST",
        admin_ids=str(ADMIN_ID),
        openrouter_api_key="",
    )
    dialog, error = await start_dialog(
        session,
        settings,
        admin_id=ADMIN_ID,
        peer_id=PEER_ID,
        mode="ai",
        instruction="test",
    )
    assert dialog is None
    assert error is not None


async def _seed_topic(session: AsyncSession, *, title: str = "Флудилка") -> tuple[int, int]:
    chat_id = -1002060145994
    thread_id = 29
    await repo.upsert_chat(
        session,
        TgChat(id=chat_id, type="supergroup", title="LR Club", is_forum=True),
    )
    await repo.upsert_topic(session, chat_id, thread_id, title)
    await repo.upsert_user(
        session,
        TgUser(id=PEER_ID, is_bot=False, first_name="Peer"),
        started_bot=True,
    )
    return chat_id, thread_id


async def test_topic_dialog_requires_chat_and_thread(session: AsyncSession) -> None:
    await _seed_topic(session)
    dialog, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=PEER_ID,
        mode="relay",
        instruction=None,
        chat_id=-1002060145994,
    )
    assert dialog is None
    assert error is not None and "чат" in error


async def test_topic_dialog_requires_known_topic(session: AsyncSession) -> None:
    await _seed_topic(session)
    dialog, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=PEER_ID,
        mode="relay",
        instruction=None,
        chat_id=-1002060145994,
        thread_id=999,
    )
    assert dialog is None
    assert error is not None and "Тема не найдена" in error


async def test_topic_dialog_is_lookupable(session: AsyncSession) -> None:
    chat_id, thread_id = await _seed_topic(session)
    dialog, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=PEER_ID,
        mode="ai",
        instruction="Отвечай кратко",
        chat_id=chat_id,
        thread_id=thread_id,
    )
    assert error is None and dialog is not None
    assert dialog.chat_id == chat_id
    assert dialog.thread_id == thread_id

    found = await repo.get_active_topic_dialog(session, chat_id, thread_id)
    assert found is not None and found.id == dialog.id
    assert await repo.get_active_topic_dialog(session, chat_id, thread_id + 1) is None
    # A topic dialog must not hijack private messages.
    assert await repo.get_active_dialog_for_peer(session, PEER_ID, private_only=True) is None

    text = dialog_created_text(dialog, await repo.get_user(session, PEER_ID))
    assert "чат" in text and "тема" in text


async def test_topic_dialog_pause_and_stop(session: AsyncSession) -> None:
    chat_id, thread_id = await _seed_topic(session)
    dialog, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=PEER_ID,
        mode="relay",
        instruction=None,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    assert error is None and dialog is not None

    paused = await repo.pause_dialog(session, dialog.id)
    assert paused is not None and paused.status == "paused"
    assert await repo.get_active_topic_dialog(session, chat_id, thread_id) is None

    resumed = await repo.resume_dialog(session, dialog.id)
    assert resumed is not None
    assert await repo.get_active_topic_dialog(session, chat_id, thread_id) is not None

    stopped = await repo.stop_dialog(session, dialog.id)
    assert stopped is not None and stopped.status == "stopped"
    assert await repo.get_active_topic_dialog(session, chat_id, thread_id) is None


async def _seed_admin(session: AsyncSession) -> None:
    await repo.upsert_user(
        session,
        TgUser(id=ADMIN_ID, is_bot=False, first_name="Admin"),
        started_bot=True,
    )


async def test_self_dialog_private_relay_is_blocked(session: AsyncSession) -> None:
    await _seed_admin(session)
    dialog, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=ADMIN_ID,
        mode="relay",
        instruction=None,
    )
    assert dialog is None
    assert error is not None and "самим собой" in error


async def test_self_dialog_private_ai_is_allowed(session: AsyncSession) -> None:
    await _seed_admin(session)
    dialog, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=ADMIN_ID,
        mode="ai",
        instruction="Отвечай кратко",
    )
    assert error is None and dialog is not None

    admin = await repo.get_user(session, ADMIN_ID)
    assert admin is not None
    assert "Тестовый диалог" in dialog_created_text(dialog, admin)


async def test_self_dialog_in_topic_is_allowed(session: AsyncSession) -> None:
    chat_id, thread_id = await _seed_topic(session)
    await _seed_admin(session)
    dialog, error = await start_dialog(
        session,
        _settings(),
        admin_id=ADMIN_ID,
        peer_id=ADMIN_ID,
        mode="relay",
        instruction=None,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    assert error is None and dialog is not None
    assert dialog.chat_id == chat_id
    assert dialog.thread_id == thread_id
