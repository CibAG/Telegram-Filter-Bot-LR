from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)
from aiogram.types.error_event import ErrorEvent

from app import __version__
from app.config import BASE_DIR, Settings, get_settings
from app.db.session import init_db
from app.handlers import setup_routers
from app.middlewares.db import DbSessionMiddleware
from app.middlewares.tracking import TrackingMiddleware
from app.utils.logging import configure_logging
from app.utils.single_instance import SingleInstance

logger = logging.getLogger(__name__)

PRIVATE_COMMANDS = [
    BotCommand(command="start", description="Меню"),
    BotCommand(command="menu", description="Панель администратора"),
    BotCommand(command="users", description="Пользователи"),
    BotCommand(command="user", description="Карточка пользователя"),
    BotCommand(command="export", description="Экспорт пользователей в CSV"),
    BotCommand(command="dialog", description="Начать диалог с пользователем"),
    BotCommand(command="dialogs", description="Активные диалоги"),
    BotCommand(command="stopdialog", description="Завершить диалог"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="audit", description="Журнал действий"),
    BotCommand(command="help", description="Помощь"),
]


async def on_error(event: ErrorEvent) -> None:
    exc = event.exception
    logger.error(
        "Unhandled update error: %s",
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    update = event.update
    message = update.message or update.edited_message
    if message is None:
        return
    try:
        await message.answer("⚠️ Внутренняя ошибка. Попробуйте позже.")
    except Exception:  # noqa: BLE001
        pass


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    # No bot controls are shown in groups: clear every group-related command scope.
    for scope in (
        BotCommandScopeDefault(),
        BotCommandScopeAllGroupChats(),
        BotCommandScopeAllChatAdministrators(),
    ):
        await bot.set_my_commands([], scope=scope)


async def _run_bot(settings: Settings) -> None:
    if not settings.admin_id_list:
        logger.warning("ADMIN_IDS is empty: admin commands will be unavailable")

    token = settings.bot_token.get_secret_value().strip()
    if not token or token == "your_bot_token_here" or ":" not in token:
        logger.error("BOT_TOKEN is not configured. Fill BOT_TOKEN in .env and restart the bot.")
        return

    await init_db(settings.database_url_resolved)

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    for observer in (dp.message, dp.edited_message, dp.callback_query):
        observer.outer_middleware(DbSessionMiddleware())
        observer.outer_middleware(TrackingMiddleware())

    setup_routers(dp)
    dp.errors.register(on_error)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await set_commands(bot)
        me = await bot.get_me()
        logger.info(
            "Starting bot @%s v%s (admins: %d)",
            me.username,
            __version__,
            len(settings.admin_id_list),
        )
        await dp.start_polling(bot)
    except TelegramUnauthorizedError:
        logger.error("Telegram rejected BOT_TOKEN. Check the token in .env.")
    finally:
        await bot.session.close()


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    lock = SingleInstance(BASE_DIR / "data" / "bot.lock")
    if not lock.acquire():
        pid = lock.existing_pid()
        suffix = f" (pid {pid})" if pid else ""
        logger.error(
            "Another bot instance is already running%s. "
            "Stop it before starting a new one: Telegram allows only one polling process.",
            suffix,
        )
        return

    try:
        await _run_bot(settings)
    finally:
        lock.release()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
