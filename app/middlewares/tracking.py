from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.db import repo

logger = logging.getLogger(__name__)

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


class TrackingMiddleware(BaseMiddleware):
    """Keep the user registry and chat/topic catalog up to date."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = data.get("session")
        if session is None:
            return await handler(event, data)

        tg_user: User | None = getattr(event, "from_user", None)
        if tg_user is not None and not tg_user.is_bot:
            started = False
            if isinstance(event, Message) and event.chat.type == ChatType.PRIVATE:
                text = (event.text or "").strip().lower()
                started = text.startswith("/start")
            await repo.upsert_user(session, tg_user, started_bot=started)

        if isinstance(event, Message) and event.chat.type in GROUP_TYPES:
            await repo.upsert_chat(session, event.chat, added_by=tg_user.id if tg_user else None)
            thread_id = event.message_thread_id
            if thread_id is not None:
                title = getattr(event, "forum_topic_created", None)
                title_text = getattr(title, "name", None) if title else None
                await repo.upsert_topic(session, event.chat.id, thread_id, title_text)
            if tg_user is not None and not tg_user.is_bot:
                await repo.touch_chat_user(session, event.chat.id, tg_user.id)

        if isinstance(event, CallbackQuery):
            callback_user = event.from_user
            if callback_user is not None and not callback_user.is_bot:
                await repo.upsert_user(session, callback_user)

        return await handler(event, data)
