from __future__ import annotations

from aiogram import Dispatcher

from app.handlers import dialog, group, panel, private


def setup_routers(dp: Dispatcher) -> None:
    # Commands and callbacks first; topic dialog handlers before group moderation
    # so that assigned dialog messages are not swallowed by the catch-all.
    dp.include_router(private.router)
    dp.include_router(panel.router)
    dp.include_router(dialog.router)
    dp.include_router(group.router)
