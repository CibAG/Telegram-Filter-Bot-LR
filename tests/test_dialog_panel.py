from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.handlers import dialog
from app.keyboards.admin import dialog_controls, dialog_peers

ADMIN_ID = 1
PEER_ID = 42
SECOND_ID = 43


async def _seed(session: AsyncSession) -> None:
    await repo.upsert_user(
        session,
        TgUser(id=PEER_ID, is_bot=False, first_name="Peer", username="peer"),
        started_bot=True,
    )
    await repo.upsert_user(
        session,
        TgUser(id=SECOND_ID, is_bot=False, first_name="NoStart"),
        started_bot=False,
    )
    await repo.create_dialog(
        session,
        admin_id=ADMIN_ID,
        peer_id=PEER_ID,
        mode="ai",
        instruction="Отвечай кратко",
        model=None,
    )


def _callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


async def test_dialogs_view_lists_active(session: AsyncSession) -> None:
    await _seed(session)
    text, markup = await dialog._dialogs_view(session, admin_id=ADMIN_ID)

    assert "Активных: 1" in text
    assert "@peer" in text
    callbacks = _callbacks(markup)
    assert "dialog:new" in callbacks
    assert any(callback.startswith("dialog:open:") for callback in callbacks)


async def test_dialogs_view_without_active(session: AsyncSession) -> None:
    text, markup = await dialog._dialogs_view(session, admin_id=ADMIN_ID)

    assert "Активных диалогов нет" in text
    assert "dialog:new" in _callbacks(markup)


async def test_peer_list_marks_started_and_has_manual_entry(
    session: AsyncSession,
) -> None:
    await _seed(session)
    users = await repo.list_users_for_dialog(session)
    markup = dialog_peers(users, mode="ai", page=0, has_prev=False, has_next=False)
    callbacks = _callbacks(markup)

    assert f"dialog:peer:ai:{PEER_ID}" in callbacks
    assert f"dialog:peer:ai:{SECOND_ID}" in callbacks
    assert "dialog:manual:ai" in callbacks
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert any(label.startswith("✅") for label in labels)
    assert any(label.startswith("⚠️") for label in labels)


async def test_dialog_controls_have_instruction_only_for_ai() -> None:
    ai_callbacks = _callbacks(dialog_controls(5, "ai"))
    relay_callbacks = _callbacks(dialog_controls(6, "relay"))

    assert "dialog:instr:5" in ai_callbacks
    assert "dialog:instr:6" not in relay_callbacks
    assert "dialog:stop:5" in ai_callbacks
    assert "menu:dialogs" in ai_callbacks


def test_dialog_controls_toggle_pause_and_resume() -> None:
    active = _callbacks(dialog_controls(5, "ai"))
    paused = _callbacks(dialog_controls(5, "ai", "paused"))

    assert "dialog:pause:5" in active
    assert "dialog:resume:5" not in active
    assert "dialog:resume:5" in paused
    assert "dialog:pause:5" not in paused


async def test_dialogs_view_lists_paused_dialog(session: AsyncSession) -> None:
    await _seed(session)
    active = await repo.get_active_dialog_for_admin(session, ADMIN_ID)
    assert active is not None
    await repo.pause_dialog(session, active.id)

    text, markup = await dialog._dialogs_view(session, admin_id=ADMIN_ID)

    assert "Активных: 0" in text
    assert "Выключено: 1" in text
    assert "выключен" in text
    assert any(callback.startswith("dialog:open:") for callback in _callbacks(markup))


async def test_set_dialog_instruction(session: AsyncSession) -> None:
    await _seed(session)
    active = await repo.get_active_dialog_for_admin(session, ADMIN_ID)
    assert active is not None

    updated = await repo.set_dialog_instruction(session, active.id, "Новое задание")
    assert updated is not None
    assert updated.instruction == "Новое задание"

    cleared = await repo.set_dialog_instruction(session, active.id, None)
    assert cleared is not None
    assert cleared.instruction is None


async def test_ai_card_shows_instruction_and_model(session: AsyncSession) -> None:
    await _seed(session)
    active = await repo.get_active_dialog_for_admin(session, ADMIN_ID)
    assert active is not None
    peer = await repo.get_user(session, PEER_ID)
    assert peer is not None

    text = dialog._dialog_card_text(active, peer)
    assert "Отвечай кратко" in text
    assert "Модель:" in text


async def test_topic_peer_picker_uses_topic_callbacks(session: AsyncSession) -> None:
    await _seed(session)
    users = await repo.list_users_for_dialog(session)
    markup = dialog_peers(
        users,
        mode="ai",
        page=0,
        has_prev=False,
        has_next=False,
        chat_id=-1002060145994,
        thread_id=29,
    )
    callbacks = _callbacks(markup)

    assert f"dialog:peerT:ai:-1002060145994:29:{PEER_ID}" in callbacks
    assert any(callback.startswith("dialog:tchat:ai:") for callback in callbacks)
    assert "dialog:manual:ai" not in callbacks


def test_media_kind_and_history_text() -> None:
    from aiogram.types import Message

    photo = Message.model_construct(photo=[object()], caption="Подпись")
    assert dialog._media_kind(photo) == "фото"
    assert dialog._media_history_text(photo, "фото") == "[фото] Подпись"

    document = Message.model_construct(document=object())
    assert dialog._media_kind(document) == "файл"
    assert dialog._media_history_text(document, "файл") == "[файл]"
