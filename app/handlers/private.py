from __future__ import annotations

import asyncio
import csv
import io
import logging

from aiogram import Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import get_settings
from app.db import repo
from app.db.models import User
from app.filters.admin import IsAdmin, IsPrivateChat
from app.keyboards.admin import back_menu, main_menu, stop_confirm, users_pagination
from app.utils.text import display_user, escape, fmt_dt

logger = logging.getLogger(__name__)
router = Router(name="private")
_background_tasks: set[asyncio.Task[None]] = set()

HELP_TEXT = f"""
<b>Управление ботом</b>

Всё управление — здесь, в личке. В группах бот только удаляет сообщения
и ничего не пишет.

<b>Панель</b>
/start или /menu — панель администратора
🗂 Чаты и темы — включить чат, выбрать темы
🚫 Слова-фильтры — слово, подстрока или regex
🎯 Правила — удалять сообщения пользователя или всех
🤖 AI-модерация — удаление по текстовой инструкции
👥 Пользователи — база и карточки
💬 Диалоги — запуск AI и пересылки кнопками
⏹ Остановить бота — корректно завершить процесс

<b>Команды</b>
/users — список пользователей
/user &lt;id|@username&gt; — карточка пользователя
/export — CSV со всеми пользователями
/dialog &lt;id|@username&gt; [relay|ai] [инструкция] — начать диалог
/dialogs — активные диалоги
/stopdialog [id] — завершить диалог
/stats — статистика
/audit — журнал действий
/help — эта справка

<b>Как включить чат</b>
1. Добавьте бота в группу администратором с правом удалять сообщения.
2. В @BotFather отключите privacy mode: /setprivacy → Disable.
3. Напишите сообщение в группе или в нужной теме.
4. Откройте /menu → «🗂 Чаты и темы» и включите чат или тему.

<b>AI-диалог кнопками</b>
/menu → «💬 Диалоги» → «➕ Новый диалог» → «🤖 AI-диалог» →
выберите пользователя → отправьте задание для AI одним сообщением.
Задание можно изменить позже кнопкой «✏️ Задание для AI».

<b>AI-модерация</b>
/menu → «🤖 AI-модерация» → «➕ Добавить AI-правило» →
выберите чат и тему → отправьте инструкцию (что считать нарушением).
Модель отвечает DELETE или KEEP; каждое удаление приходит вам в личку.
Правило темы важнее правила всего чата.

<b>Режимы диалога</b>
relay — вы пишете боту, пользователь получает сообщение; ответы приходят вам.
ai — бот отвечает пользователю сам через OpenRouter по вашей инструкции.

<b>Управление диалогом</b>
В карточке диалога: «⏸ Выключить диалог» останавливает пересылку и AI,
«▶️ Включить диалог» возвращает его, «⛔ Завершить диалог» закрывает насовсем.
История сохраняется при выключении и включении.

Версия {__version__}
"""


@router.message(Command("start"), IsPrivateChat())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    if message.from_user is not None:
        await repo.upsert_user(session, message.from_user, started_bot=True)

    settings = get_settings()
    if not settings.admin_id_list:
        await message.answer(
            "⚙️ Бот ещё не настроен: список администраторов пуст.\n\n"
            f"Ваш Telegram ID: <code>{message.from_user.id if message.from_user else '—'}</code>\n"
            "Добавьте его в <code>ADMIN_IDS</code> в файле <code>.env</code> "
            "и перезапустите бота."
        )
        return

    if message.from_user is not None and message.from_user.id in settings.admin_id_set:
        await message.answer(
            "👋 <b>Панель администратора</b>\n\n"
            "Всё управление — здесь. В группах бот работает молча.",
            reply_markup=main_menu(),
        )
    else:
        await _welcome_peer(message, session)


async def _welcome_peer(message: Message, session: AsyncSession) -> None:
    if message.from_user is None:
        return
    peer_id = message.from_user.id
    dialog = await repo.get_active_dialog_for_peer(session, peer_id, private_only=True)
    if dialog is None:
        await message.answer(
            "✅ Вы зарегистрированы.\n\n"
            "Теперь администратор может связаться с вами через этого бота."
        )
        return

    hint = (
        "Можете писать сюда — сообщения получает администратор."
        if dialog.mode == "relay"
        else "Можете писать сюда — бот ответит на ваше сообщение."
    )
    await message.answer(
        f"✅ Вы зарегистрированы.\n\n"
        f"ℹ️ С вами назначен диалог администратора (#{dialog.id}).\n{hint}"
    )
    try:
        await message.bot.send_message(
            dialog.admin_id,
            f"✅ Пользователь <code>{peer_id}</code> нажал /start — "
            f"диалог #{dialog.id} теперь может доставлять сообщения.",
        )
    except Exception:  # noqa: BLE001
        logger.warning("Cannot notify admin %s about started bot", dialog.admin_id)


@router.message(Command("help"), IsPrivateChat())
async def cmd_help(message: Message) -> None:
    is_admin = message.from_user is not None and message.from_user.id in get_settings().admin_id_set
    if not is_admin:
        await message.answer(
            "Этот бот используется администратором для связи. "
            "Ожидайте сообщения или нажмите /start."
        )
        return
    await message.answer(HELP_TEXT, reply_markup=main_menu())


@router.message(Command("menu"), IsAdmin(), IsPrivateChat())
async def cmd_menu(message: Message) -> None:
    await message.answer("Главное меню:", reply_markup=main_menu())


@router.message(Command("users"), IsAdmin(), IsPrivateChat())
async def cmd_users(message: Message, session: AsyncSession) -> None:
    await _send_users_page(message, session, page=0)


@router.message(Command("user"), IsAdmin(), IsPrivateChat())
async def cmd_user(message: Message, command: CommandObject, session: AsyncSession) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await message.answer(
            "Использование: <code>/user 123456789</code> или <code>/user @username</code>"
        )
        return
    if arg.startswith("@"):
        user = await repo.find_user_by_username(session, arg)
    else:
        try:
            user_id = int(arg)
        except ValueError:
            await message.answer("ID должен быть числом или @username.")
            return
        user = await repo.get_user(session, user_id)
    if user is None:
        await message.answer("Пользователь не найден в базе.")
        return
    await message.answer(_user_card(user), reply_markup=back_menu())


@router.message(Command("export"), IsAdmin(), IsPrivateChat())
async def cmd_export(message: Message, session: AsyncSession) -> None:
    users = await repo.list_all_users(session)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "user_id",
            "username",
            "first_name",
            "last_name",
            "started_bot",
            "is_blocked",
            "first_seen_at",
            "last_seen_at",
        ]
    )
    for user in users:
        writer.writerow(
            [
                user.id,
                user.username or "",
                user.first_name or "",
                user.last_name or "",
                int(user.started_bot),
                int(user.is_blocked),
                user.first_seen_at.isoformat() if user.first_seen_at else "",
                user.last_seen_at.isoformat() if user.last_seen_at else "",
            ]
        )
    data = buffer.getvalue().encode("utf-8-sig")
    document = BufferedInputFile(data, filename="users.csv")
    await message.answer_document(document, caption=f"Пользователи: {len(users)}")


@router.message(Command("stats"), IsAdmin(), IsPrivateChat())
async def cmd_stats(message: Message, session: AsyncSession) -> None:
    await message.answer(await _stats_text(session), reply_markup=back_menu())


@router.message(Command("audit"), IsAdmin(), IsPrivateChat())
async def cmd_audit(message: Message, session: AsyncSession) -> None:
    await message.answer(await _audit_text(session), reply_markup=back_menu())


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "menu:main", IsAdmin())
async def cb_main(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "👋 <b>Панель администратора</b>\n\nВыберите раздел:",
            reply_markup=main_menu(),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:help", IsAdmin())
async def cb_help(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(HELP_TEXT, reply_markup=back_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:stats", IsAdmin())
async def cb_stats(callback: CallbackQuery, session: AsyncSession) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(await _stats_text(session), reply_markup=back_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:audit", IsAdmin())
async def cb_audit(callback: CallbackQuery, session: AsyncSession) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(await _audit_text(session), reply_markup=back_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:stop", IsAdmin())
async def cb_stop_menu(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "⏹ <b>Остановить бота?</b>\n\n"
            "Бот перестанет получать сообщения и модерировать чаты, диалоги тоже "
            "остановятся. База, фильтры и настройки сохранятся.\n\n"
            "Запустить снова можно через <code>start_bot.bat</code> или командой "
            "<code>python -m app.main</code>.",
            reply_markup=stop_confirm(),
        )
    await callback.answer()


@router.callback_query(F.data == "stopbot:confirm", IsAdmin())
async def cb_stop_confirm(
    callback: CallbackQuery,
    dispatcher: Dispatcher,
    session: AsyncSession,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "⏹ <b>Бот остановлен</b>\n\n"
            "Процесс завершён. Чтобы запустить снова, откройте "
            "<code>start_bot.bat</code> или выполните <code>python -m app.main</code>."
        )
    await callback.answer("Останавливаю бота")
    await repo.add_audit(
        session,
        action="bot_stopped",
        actor_id=callback.from_user.id if callback.from_user else None,
    )
    # Commit before shutting down: the middleware would normally commit after
    # the handler returns, but polling may already be stopping by then.
    await session.commit()

    task = asyncio.create_task(_stop_polling(dispatcher))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _stop_polling(dispatcher: Dispatcher) -> None:
    try:
        await dispatcher.stop_polling()
    except RuntimeError:
        logger.warning("Polling is already stopped")


@router.callback_query(F.data.startswith("menu:users:"), IsAdmin())
async def cb_users(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        page = int((callback.data or "menu:users:0").rsplit(":", 1)[1])
    except ValueError:
        page = 0
    if isinstance(callback.message, Message):
        await _send_users_page(callback.message, session, page=page, edit=True)
    await callback.answer()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_card(user: User) -> str:
    name = escape(user.first_name or "")
    if user.last_name:
        name = f"{name} {escape(user.last_name)}"
    return (
        f"<b>Пользователь</b>\n"
        f"ID: <code>{user.id}</code>\n"
        f"Имя: {name or '—'}\n"
        f"Username: {display_user(user.id, user.username, user.first_name)}\n"
        f"Запускал бота: {'да' if user.started_bot else 'нет'}\n"
        f"Заблокирован: {'да' if user.is_blocked else 'нет'}\n"
        f"Первый раз: {fmt_dt(user.first_seen_at)}\n"
        f"Последний раз: {fmt_dt(user.last_seen_at)}"
    )


async def _send_users_page(
    message: Message,
    session: AsyncSession,
    *,
    page: int,
    edit: bool = False,
) -> None:
    per_page = 10
    page = max(page, 0)
    total = await repo.count_users(session)
    users = await repo.list_users(session, offset=page * per_page, limit=per_page + 1)
    has_next = len(users) > per_page
    users = users[:per_page]

    lines = [f"<b>👥 Пользователи</b> (всего {total})", ""]
    if not users:
        lines.append("Пока никого нет.")
    for user in users:
        started = "✅" if user.started_bot else "—"
        lines.append(
            f"{started} {display_user(user.id, user.username, user.first_name)} — "
            f"<code>{user.id}</code> "
            f"{'@' + escape(user.username) if user.username else ''}"
        )
    text = "\n".join(lines)
    markup = users_pagination(page, page > 0, has_next)

    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def _stats_text(session: AsyncSession) -> str:
    data = await repo.stats(session)
    return (
        "<b>📊 Статистика</b>\n\n"
        f"Пользователей: {data['users']}\n"
        f"Чатов: {data['chats']}\n"
        f"Тем: {data['topics']}\n"
        f"Слов-фильтров: {data['filters']}\n"
        f"Правил: {data['rules']}\n"
        f"Активных диалогов: {data['dialogs']}\n"
        f"Удалённых сообщений: {data['deletions']}"
    )


async def _audit_text(session: AsyncSession) -> str:
    entries = await repo.recent_audit(session, limit=15)
    lines = ["<b>📋 Последние действия</b>", ""]
    if not entries:
        lines.append("Журнал пуст.")
    for entry in entries:
        actor = f"админ {entry.actor_id}" if entry.actor_id else "бот"
        details = f" — {escape(entry.details)}" if entry.details else ""
        lines.append(f"{fmt_dt(entry.created_at)} | {escape(entry.action)} | {actor}{details}")
    return "\n".join(lines)
