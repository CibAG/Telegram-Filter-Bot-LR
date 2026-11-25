from __future__ import annotations

import html
from datetime import datetime

from aiogram.types import Message, User


def escape(value: object | None) -> str:
    return html.escape(str(value)) if value is not None else ""


def user_mention(user: User | None) -> str:
    if user is None:
        return "неизвестный"
    name = escape(user.full_name)
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def display_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> str:
    if username:
        return f"@{escape(username)}"
    if first_name:
        return escape(first_name)
    return str(user_id)


def message_preview(message: Message, limit: int = 120) -> str:
    text = message.text or message.caption or ""
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return escape(text)


def chunk_text(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        if size + len(line) > limit and current:
            chunks.append("".join(current))
            current = []
            size = 0
        while len(line) > limit:
            if current:
                chunks.append("".join(current))
                current = []
                size = 0
            chunks.append(line[:limit])
            line = line[limit:]
        current.append(line)
        size += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M")
