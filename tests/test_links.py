from __future__ import annotations

import pytest

from app.utils.links import chat_ref_matches, parse_topic_link

CHAT_ID = -1002060145994


@pytest.mark.parametrize(
    ("text", "thread_id", "message_id"),
    [
        ("https://t.me/c/2060145994/47/1234", 47, 1234),
        ("t.me/c/2060145994/47/1234?single", 47, 1234),
        ("https://t.me/c/2060145994/47", 47, None),
        ("https://t.me/c/2060145994/1234?thread=47", 47, 1234),
        ("https://t.me/LRClub/47/1234", 47, 1234),
        ("tg://privatepost?channel=2060145994&post=1234&thread=47", 47, 1234),
        ("https://web.telegram.org/a/#-1002060145994_1", 1, None),
        ("https://web.telegram.org/a/#-1002060145994_29", 29, None),
        ("https://web.telegram.org/a/#-1002060145994_81245", 81245, None),
        ("https://web.telegram.org/k/#-1002060145994_468", 468, None),
        ("https://web.telegram.org/a/#-1002060145994_29_1234", 29, 1234),
    ],
)
def test_parse_topic_link(text: str, thread_id: int, message_id: int | None) -> None:
    link = parse_topic_link(text)

    assert link is not None
    assert link.thread_id == thread_id
    assert link.message_id == message_id


def test_parse_topic_link_keeps_chat_reference() -> None:
    assert parse_topic_link("https://t.me/c/2060145994/47/1234").chat_ref == "2060145994"
    assert parse_topic_link("https://t.me/LRClub/47/1234").chat_ref == "LRClub"


@pytest.mark.parametrize(
    "text", ["", "просто текст", "https://t.me/c/2060145994", "https://example.com/1/2"]
)
def test_parse_topic_link_rejects_garbage(text: str) -> None:
    assert parse_topic_link(text) is None


def test_chat_ref_matches_strips_supergroup_prefix() -> None:
    assert chat_ref_matches(CHAT_ID, "2060145994") is True
    assert chat_ref_matches(-1001234567890, "1234567890") is True
    assert chat_ref_matches(CHAT_ID, "123") is False


def test_chat_ref_matches_handles_basic_group_ids() -> None:
    assert chat_ref_matches(-1234567890, "1234567890") is True


def test_chat_ref_matches_accepts_full_web_ids() -> None:
    assert chat_ref_matches(CHAT_ID, "-1002060145994") is True
    assert chat_ref_matches(CHAT_ID, "1002060145994") is True


def test_web_topic_link_from_user() -> None:
    link = parse_topic_link("https://web.telegram.org/a/#-1002060145994_29")

    assert link is not None
    assert link.thread_id == 29
    assert chat_ref_matches(CHAT_ID, link.chat_ref) is True


@pytest.mark.parametrize(
    "text",
    [
        "-1002060145994_29",
        "#-1002060145994_29",
    ],
)
def test_bare_web_fragment_is_accepted(text: str) -> None:
    link = parse_topic_link(text)

    assert link is not None
    assert link.thread_id == 29
    assert link.chat_ref == "-1002060145994"
