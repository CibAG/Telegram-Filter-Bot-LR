from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

PUBLIC_PREFIX = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/",
    re.IGNORECASE,
)
PRIVATE_PATH = re.compile(r"^c/(?P<chat>\d+)/(?P<rest>\d+(?:/\d+)?)$")
PUBLIC_PATH = re.compile(r"^(?P<chat>[A-Za-z0-9_]{4,})/(?P<rest>\d+(?:/\d+)?)$")
WEB_FRAGMENT = re.compile(r"^(?P<chat>-?\d+)_(?P<thread>\d+)(?:_(?P<message>\d+))?$")


@dataclass(frozen=True)
class TopicLink:
    """Parsed Telegram link pointing to a forum topic.

    ``chat_ref`` is the numeric part of ``t.me/c/<chat>/...`` or a public
    username. ``thread_id`` is the forum topic id, ``message_id`` the message.
    """

    chat_ref: str
    thread_id: int | None
    message_id: int | None


def _to_int(value: str | None) -> int | None:
    if not value or not value.isdigit():
        return None
    return int(value)


def _split_rest(rest: str) -> tuple[int | None, int | None]:
    parts = rest.split("/")
    if len(parts) == 1:
        # ``t.me/c/<chat>/<x>``: for topics this is the topic link, otherwise
        # it is a link to a single message. Caller decides based on the query.
        return int(parts[0]), None
    return int(parts[0]), int(parts[1])


def parse_topic_link(text: str | None) -> TopicLink | None:
    """Parse a message or topic link into chat reference and thread id."""
    raw = (text or "").strip()
    if not raw:
        return None

    bare = raw[1:] if raw.startswith("#") else raw
    bare_match = WEB_FRAGMENT.fullmatch(bare)
    if "/" not in bare and bare_match is not None:
        # Only the fragment was pasted: ``-1002060145994_29``.
        return TopicLink(
            chat_ref=bare_match.group("chat"),
            thread_id=_to_int(bare_match.group("thread")),
            message_id=_to_int(bare_match.group("message")),
        )

    if raw.lower().startswith("tg://"):
        parsed = urlparse(raw)
        if parsed.netloc not in {"privatepost", "resolve"}:
            return None
        query = parse_qs(parsed.query)
        if parsed.netloc == "resolve":
            username = (query.get("domain") or [""])[0]
            thread = _to_int((query.get("thread") or [""])[0])
            post = _to_int((query.get("post") or [""])[0])
            if not username:
                return None
            return TopicLink(chat_ref=username, thread_id=thread, message_id=post)

        chat_ref = (query.get("channel") or [""])[0]
        thread = _to_int((query.get("thread") or [""])[0])
        post = _to_int((query.get("post") or [""])[0])
        if not chat_ref.isdigit():
            return None
        return TopicLink(chat_ref=chat_ref, thread_id=thread, message_id=post)

    query = parse_qs(urlparse(raw).query)
    query_thread = _to_int((query.get("thread") or [""])[0])

    parsed = urlparse(raw)
    fragment = (parsed.fragment or "").strip("/")
    web = WEB_FRAGMENT.match(fragment)
    if web is not None:
        # Telegram Web: https://web.telegram.org/a/#-1002060145994_29
        return TopicLink(
            chat_ref=web.group("chat"),
            thread_id=_to_int(web.group("thread")),
            message_id=_to_int(web.group("message")),
        )

    cleaned = PUBLIC_PREFIX.sub("", raw).split("?")[0].split("#")[0].strip("/")
    for pattern in (PRIVATE_PATH, PUBLIC_PATH):
        match = pattern.match(cleaned)
        if match is None:
            continue
        thread, message = _split_rest(match.group("rest"))
        if query_thread is not None:
            # ``t.me/c/<chat>/<message>?thread=<topic>``
            thread, message = query_thread, thread
        return TopicLink(chat_ref=match.group("chat"), thread_id=thread, message_id=message)

    return None


def _internal_chat_id(chat_id: int) -> str:
    digits = str(abs(chat_id))
    return digits[3:] if digits.startswith("100") else digits


def chat_ref_matches(chat_id: int, chat_ref: str) -> bool:
    """Check that a chat reference points to ``chat_id``.

    Accepts bare internal ids (``2060145994``), full ids
    (``-1002060145994``) and basic group ids.
    """
    digits = chat_ref.lstrip("-")
    return digits in {str(abs(chat_id)), _internal_chat_id(chat_id)}
