from __future__ import annotations

from app.handlers import panel


def test_parse_scope_callback() -> None:
    assert panel._three_parts_ints("wordadd:scope:-1001234567890:-1", "wordadd:scope") == (
        -1001234567890,
        None,
    )
    assert panel._three_parts_ints("ruleadd:scope:-1001234567890:42", "ruleadd:scope") == (
        -1001234567890,
        42,
    )


def test_parse_two_ids_callback() -> None:
    assert panel._two_ints("topicv:-1001234567890:42") == (-1001234567890, 42)
    assert panel._two_ints("ruleexcdel:7:123456789") == (7, 123456789)
    assert panel._two_ints("broken") is None


def test_parse_tail_id_callback() -> None:
    assert panel._int_tail("chatv:-1001234567890") == -1001234567890
    assert panel._int_tail("worddel:12") == 12
    assert panel._int_tail("worddel:oops") is None
