from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from app.db.models import AiRule
from app.keyboards import admin as admin_kb
from app.keyboards import panel as panel_kb

CHAT_ID = -1002060145994


def _callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]


def test_filter_type_encodes_whole_chat_as_minus_one() -> None:
    callbacks = _callbacks(panel_kb.add_filter_type(CHAT_ID, None))

    assert f"wordadd:type:{CHAT_ID}:-1:word" in callbacks
    assert f"wordadd:type:{CHAT_ID}:-1:substring" in callbacks
    assert f"wordadd:type:{CHAT_ID}:-1:regex" in callbacks
    assert all("None" not in item for item in callbacks)


def test_filter_type_keeps_topic_id() -> None:
    callbacks = _callbacks(panel_kb.add_filter_type(CHAT_ID, 7))

    assert f"wordadd:type:{CHAT_ID}:7:regex" in callbacks


def test_rule_target_encodes_whole_chat_as_minus_one() -> None:
    callbacks = _callbacks(panel_kb.add_rule_target(CHAT_ID, None))

    assert f"ruleadd:target:{CHAT_ID}:-1:all" in callbacks
    assert f"ruleadd:target:{CHAT_ID}:-1:user" in callbacks
    assert all("None" not in item for item in callbacks)


def test_rule_target_keeps_topic_id() -> None:
    callbacks = _callbacks(panel_kb.add_rule_target(CHAT_ID, 3))

    assert f"ruleadd:target:{CHAT_ID}:3:all" in callbacks


def test_filter_type_callback_is_parsable_by_handler() -> None:
    callbacks = _callbacks(panel_kb.add_filter_type(CHAT_ID, None))
    callback = next(item for item in callbacks if item.endswith(":word"))

    parts = callback.split(":")
    assert len(parts) == 5
    assert int(parts[2]) == CHAT_ID
    assert int(parts[3]) == -1
    assert parts[4] == "word"


def test_topics_list_has_link_rename_button() -> None:
    callbacks = _callbacks(panel_kb.topics_list(CHAT_ID, [], enabled=set()))

    assert f"topiclink:{CHAT_ID}" in callbacks


def test_topic_card_has_rename_button() -> None:
    callbacks = _callbacks(panel_kb.topic_card(CHAT_ID, 5, enabled=True))

    assert f"topicrename:{CHAT_ID}:5" in callbacks
    assert f"topictoggle:{CHAT_ID}:5" in callbacks


def test_topic_card_rename_callback_is_parsable() -> None:
    callbacks = _callbacks(panel_kb.topic_card(CHAT_ID, 5, enabled=False))
    callback = next(item for item in callbacks if item.startswith("topicrename:"))

    parts = callback.split(":")
    assert len(parts) == 3
    assert int(parts[1]) == CHAT_ID
    assert int(parts[2]) == 5


def test_main_menu_has_stop_button() -> None:
    assert "menu:stop" in _callbacks(admin_kb.main_menu())


def test_main_menu_has_ai_rules_button() -> None:
    assert "menu:airules" in _callbacks(admin_kb.main_menu())


def test_ai_rule_card_callbacks() -> None:
    rule = AiRule(
        id=7,
        chat_id=CHAT_ID,
        thread_id=None,
        instruction="Удаляй рекламу",
        enabled=True,
    )
    callbacks = _callbacks(panel_kb.ai_rule_card(rule))

    assert "airuletoggle:7" in callbacks
    assert "airuledel:7" in callbacks
    assert f"airulelist:{CHAT_ID}" in callbacks
    assert any(item.startswith(f"airuleadd:scope:{CHAT_ID}:-1") for item in callbacks)


def test_stop_confirm_keyboard() -> None:
    callbacks = _callbacks(admin_kb.stop_confirm())

    assert "stopbot:confirm" in callbacks
    assert "menu:main" in callbacks
