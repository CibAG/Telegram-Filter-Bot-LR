from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.config import get_settings


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, **_: Any) -> bool:
        user = event.from_user
        return user is not None and user.id in get_settings().admin_id_set


class IsPrivateChat(BaseFilter):
    async def __call__(self, event: Message, **_: Any) -> bool:
        return event.chat is not None and event.chat.type == "private"
