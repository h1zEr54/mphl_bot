import asyncio
import html
import logging
import os
import re
import sqlite3

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# =====================================================
# НАСТРОЙКИ
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

if not OWNER_ID or not OWNER_ID.isdigit():
    raise RuntimeError("Не задан корректный OWNER_ID")

OWNER_ID = int(OWNER_ID)

# ВАЖНО: база данных хранится в /app/data, чтобы не удаляться при перезапуске
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "mphl_bot.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

_user_locks = {}


# =====================================================
# БАЗА ДАННЫХ
# =====================================================

@contextmanager
def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position TEXT NOT NULL,
                name TEXT NOT NULL,
                telegram TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'Администрация'
            )
        """)
        
        columns = [row["name"] for row in db.execute("PRAGMA table_info(admins)").fetchall()]
        if "level" not in columns:
            db.execute("ALTER TABLE admins ADD COLUMN level TEXT NOT NULL DEFAULT 'Администрация'")

        db.execute("""
            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                text TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                nickname TEXT NOT NULL,
                text TEXT NOT NULL,
                media_type TEXT,
                media_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS unban_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                steam_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                ban_date TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS admin_user_ids (
                user_id INTEGER PRIMARY KEY
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS captains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                team TEXT NOT NULL UNIQUE,
                telegram TEXT NOT NULL DEFAULT '',
                added_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS suggestion_media_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id INTEGER NOT NULL,
                source_chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_name TEXT NOT NULL,
                date_time TEXT NOT NULL,
                stream_link TEXT,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_name TEXT NOT NULL,
                date_time TEXT NOT NULL,
                score TEXT NOT NULL,
                winner TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        count = db.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
        if count == 0:
            db.execute("""
                INSERT INTO admins (position, name, telegram, level)
                VALUES (?, ?, ?, ?)
            """, ("Владелец лиги", "h1zEr #99", "https://t.me/h1zEr54", "Высшая администрация"))


# =====================================================
# ДОЛЖНОСТИ И УРОВНИ
# =====================================================

LEVELS = ["Высшая администрация", "Старшая администрация", "Администрация"]

POSITIONS = [
    "Владелец лиги",
    "Главный администратор",
    "Главный судья",
    "Зам.главного администратора",
    "Зам.главного судьи",
    "Технический специалист",
    "Специалист по соц сетям",
    "Судья 1",
    "Судья 2",
    "Судья 3",
]


# =====================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================

def escape(value):
    return html.escape(str(value or ""))


def is_owner(user_id: int):
    return user_id == OWNER_ID


def is_admin(user_id: int, username: str | None = None):
    if is_owner(user_id):
        return True
    if not username:
        return False
    username = username.lower().lstrip("@")
    with get_db() as db:
        admins = db.execute("SELECT telegram FROM admins").fetchall()
    for admin in admins:
        link = admin["telegram"].strip().rstrip("/")
        admin_username = link.split("/")[-1].lstrip("@").lower()
        if admin_username == username:
            return True
    return False


def register_admin_user(user_id: int, username: str | None = None):
    if is_admin(user_id, username):
        with get_db() as db:
            db.execute(
                "INSERT OR IGNORE INTO admin_user_ids (user_id) VALUES (?)",
                (user_id,)
            )


def get_registered_admin_ids():
    with get_db() as db:
        rows = db.execute("SELECT user_id FROM admin_user_ids").fetchall()
    return [row["user_id"] for row in rows]


def valid_telegram_link(link: str):
    return bool(re.fullmatch(r"https?://(t\.me|www\.twitch\.tv|twitch\.tv|youtube\.com|youtu\.be)/[A-Za-z0-9_/]+", link.strip()))


def main_menu_keyboard(user_id: int, username: str | None = None):
    builder = InlineKeyboardBuilder()
    builder.button(text="🏆 Капитаны и их команды", callback_data="captains")
    builder.button(text="👥 Состав администрации", callback_data="admins")
    builder.button(text="🔓 Разбан", callback_data="unban")
    builder.button(text="⚠️ Жалоба на администрацию", callback_data="complaint")
    builder.button(text="💡 Предложение по улучшению", callback_data="suggestion")
    builder.button(text="🎮 Игры", callback_data="games")
    builder.button(text="📊 Итоги игр", callback_data="results")
    if is_admin(user_id, username):
        builder.button(text="⚙️ Админ-панель", callback_data="admin_panel")
    builder.adjust(1)
    return builder.as_markup()


def back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="home")]
        ]
    )


async def show_main_menu(message: Message):
    await message.answer(
        "🏒 <b>Добро пожаловать в MPHL!</b>\n\n"
        "Выберите нужный раздел:",
        reply_markup=main_menu_keyboard(message.from_user.id, message.from_user.username)
    )


# =====================================================
# СОСТОЯНИЯ FSM
# =====================================================

class ComplaintForm(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_admin_nickname = State()
    waiting_for_violation = State()
    waiting_for_proof = State()

class SuggestionForm(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_text = State()
    waiting_for_media = State()

class SuggestionReplyForm(StatesGroup):
    waiting_for_reply = State()

class UnbanForm(StatesGroup):
    waiting_for_steamid = State()
    waiting_for_reason = State()
    waiting_for_date = State()
    waiting_for_media = State()

class AddCaptainForm(StatesGroup):
    waiting_for_name = State()
    waiting_for_team = State()
    waiting_for_telegram = State()

class AddAdminForm(StatesGroup):
    waiting_for_level = State()
    waiting_for_position = State()
    waiting_for_name = State()
    waiting_for_link = State()

class AddGameForm(StatesGroup):
    waiting_for_match = State()
    waiting_for_datetime = State()
    waiting_for_stream = State()

class DeleteGameForm(StatesGroup):
    waiting_for_match = State()

class AddResultForm(StatesGroup):
    waiting_for_datetime = State()
    waiting_for_match = State()
    waiting_for_score = State()
    waiting_for_winner = State()

class DeleteResultForm(StatesGroup):
    waiting_for_match = State()


# =====================================================
# СТАРТ
# =====================================================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    register_admin_user(message.from_user.id, message.from_user.username)
    await show_main_menu(message)


@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено.")
    await show_main_menu(message)


# =====================================================
# ГЛАВНОЕ МЕНЮ
# =====================================================

@dp.callback_query(F.data == "home")
async def home_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        "🏒 <b>Главное меню MPHL</b>\n\nВыберите нужный раздел:",
        reply_markup=main_menu_keyboard(call.from_user.id, call.from_user.username)
    )


# =====================================================
# ИГРЫ (ПРОСМОТР)
# =====================================================

@dp.callback_query(F.data == "games")
async def games_callback(call: CallbackQuery):
    await call.answer()
    with get_db() as db:
        games = db.execute("SELECT * FROM games ORDER BY id DESC").fetchall()
    
    if not games:
        text = "🎮 <b>Игры MPHL</b>\n\nПока нет запланированных игр."
    else:
        text = "🎮 <b>Игры MPHL</b>\n\n"
        for game in games:
            stream = game['stream_link'] if game['stream_link'] else "Игра будет без стрима"
            text += f"📅 {escape(game['date_time'])} | {escape(game['match_name'])} | {escape(stream)}\n\n"
    
    await call.message.edit_text(text, reply_markup=back_keyboard(), disable_web_page_preview=True)


# =====================================================
# ИТОГИ (ПРОСМОТР)
# =====================================================

@dp.callback_query(F.data == "results")
async def results_callback(call: CallbackQuery):
    await call.answer()
    with get_db() as db:
        results = db.execute("SELECT * FROM results ORDER BY id DESC").fetchall()
    
    if not results:
        text = "📊 <b>Итоги игр MPHL</b>\n\nПока нет итогов."
    else:
        text = "📊 <b>Итоги игр MPHL</b>\n\n"
        for res in results:
            text += f"📅 {escape(res['date_time'])} | {escape(res['match_name'])} | {escape(res['score'])} - победа команды - {escape(res['winner'])}\n\n"
    
    await call.message.edit_text(text, reply_markup=back_keyboard())


# =====================================================
# КАПИТАНЫ
# =====================================================

@dp.callback_query(F.data == "captains")
async def captains_callback(call: CallbackQuery):
    await call.answer()
    with get_db() as db:
        captains = db.execute("SELECT name, team, telegram FROM captains ORDER BY id LIMIT 5").fetchall()
    text = "🏆 <b>Капитаны и их команды MPHL</b>\n\n"
    for number in range(1, 6):
        if number <= len(captains):
            captain = captains[number - 1]
            text += f"<b>{number}. Команда — {escape(captain['name'])} | {escape(captain['team'])}</b>\n"
            if captain["telegram"]:
                text += f"👤 <a href=\"{escape(captain['telegram'])}\">Telegram капитана</a>\n"
            text += "\n"
        else:
            text += f"<b>{number}. Команда — Свободно</b>\n\n"
    text += "📝 <a href=\"https://t.me/infomphl/7\">Подать заявку</a>"
    await call.message.edit_text(text, reply_markup=back_keyboard())


# =====================================================
# АДМИНИСТРАЦИЯ
# =====================================================

@dp.callback_query(F.data == "admins")
async def admins_callback(call: CallbackQuery):
    await call.answer()
    with get_db() as db:
        admins = db.execute("SELECT * FROM admins ORDER BY id").fetchall()

    admin_dict = {}
    for admin in admins:
        pos = admin['position']
        if pos not in admin_dict:
            admin_dict[pos] = []
        admin_dict[pos].append((admin['name'], admin['telegram']))

    def fmt(pos_name):
        if pos_name in admin_dict and admin_dict[pos_name]:
            name, link = admin_dict[pos_name][0]
            return f"{escape(name)} | {escape(link)}"
        return "Свободно"

    text = (
        "<b>Высшая администрация</b>\n\n"
        f"Владелец лиги - {fmt('Владелец лиги')}\n"
        f"Главный администратор - {fmt('Главный администратор')}\n"
        f"Главный судья - {fmt('Главный судья')}\n\n"
        "<b>Старшая администрация</b>\n\n"
        f"Зам.главного администратора - {fmt('Зам.главного администратора')}\n"
        f"Зам.главного судьи - {fmt('Зам.главного судьи')}\n"
        f"Технический специалист - {fmt('Технический специалист')}\n"
        f"Специалист по соц сетям - {fmt('Специалист по соц сетям')}\n\n"
        "<b>Администрация</b>\n\n"
        f"Судья - {fmt('Судья 1')}\n"
        f"Судья - {fmt('Судья 2')}\n"
        f"Судья - {fmt('Судья 3')}\n\n"
        "Подать заявку - https://t.me/infomphl/5"
    )

    await call.message.edit_text(text, reply_markup=back_keyboard(), disable_web_page_preview=True)


# =====================================================
# АДМИН-ПАНЕЛЬ
# =====================================================

@dp.callback_query(F.data == "admin_panel")
async def admin_panel(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("У вас нет доступа.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить капитана", callback_data="add_captain")
    builder.button(text="➖ Снять капитана", callback_data="remove_captain")
    builder.button(text="🎮 Добавить игру", callback_data="add_game")
    builder.button(text="🗑 Удалить игру", callback_data="delete_game")
    builder.button(text="📊 Добавить итоги", callback_data="add_result")
    builder.button(text="🗑 Удалить итоги", callback_data="delete_result")
    if is_owner(call.from_user.id):
        builder.button(text="👥 Управление администрацией", callback_data="manage_admins")
        builder.button(text="📋 История жалоб", callback_data="complaint_history")
    builder.button(text="⬅️ В главное меню", callback_data="home")
    builder.adjust(1)
    await call.answer()
    await call.message.edit_text(
        "⚙️ <b>Панель администрации MPHL</b>\n\nВыберите действие:",
        reply_markup=builder.as_markup()
    )


# =====================================================
# УПРАВЛЕНИЕ ИГРАМИ
# =====================================================

@dp.callback_query(F.data == "add_game")
async def add_game_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AddGameForm.waiting_for_match)
    await call.answer()
    await call.message.answer(
        "🎮 <b>Добавление игры</b>\n\n"
        "Шаг 1. Введите, кто против кого играет (например: Барыс vs Адмирал):\n\n"
        "Для отмены напишите /cancel."
    )

@dp.message(AddGameForm.waiting_for_match)
async def add_game_match(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    match = (message.text or "").strip()
    if not match or len(match) > 100:
        await message.answer("❌ Введите корректное название матча (до 100 символов).")
        return
    await state.update_data(game_match=match)
    await state.set_state(AddGameForm.waiting_for_datetime)
    await message.answer("Шаг 2. Введите дату и время игры (например: 12.11.2026 16:00):")

@dp.message(AddGameForm.waiting_for_datetime)
async def add_game_datetime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    dt = (message.text or "").strip()
    if not dt or len(dt) > 50:
        await message.answer("❌ Введите корректную дату и время.")
        return
    await state.update_data(game_datetime=dt)
    await state.set_state(AddGameForm.waiting_for_stream)
    await message.answer("Шаг 3. Отправьте ссылку на стрим (если есть). Если стрима не будет, напишите «Нет»:")

@dp.message(AddGameForm.waiting_for_stream)
async def add_game_stream(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    stream = (message.text or "").strip()
    if stream.lower() in {"нет", "no", "без стрима"}:
        stream = None
    elif not stream.startswith("http"):
        await message.answer("❌ Некорректная ссылка. Отправьте ссылку (начинается с http) или напишите «Нет».")
        return

    data = await state.get_data()
    with get_db() as db:
        db.execute("""
            INSERT INTO games (match_name, date_time, stream_link, created_at)
            VALUES (?, ?, ?, ?)
        """, (data["game_match"], data["game_datetime"], stream, datetime.now().strftime("%d.%m.%Y %H:%M")))

    await state.clear()
    await message.answer("✅ Игра добавлена.")
    await message.answer("⚙️ Админ-панель:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]]))


@dp.callback_query(F.data == "delete_game")
async def delete_game_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await state.set_state(DeleteGameForm.waiting_for_match)
    await call.answer()
    await call.message.answer(
        "🗑 <b>Удаление игры</b>\n\n"
        "Введите название игры, которую хотите удалить (например: Барыс vs Адмирал):\n\n"
        "Для отмены напишите /cancel."
    )

@dp.message(DeleteGameForm.waiting_for_match)
async def delete_game_match(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    match = (message.text or "").strip()
    with get_db() as db:
        cursor = db.execute("DELETE FROM games WHERE match_name = ?", (match,))
    
    if cursor.rowcount == 0:
        await message.answer("❌ Игра с таким названием не найдена.")
    else:
        await message.answer("✅ Игра удалена.")
    
    await state.clear()
    await message.answer("⚙️ Админ-панель:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]]))


# =====================================================
# УПРАВЛЕНИЕ ИТОГАМИ
# =====================================================

@dp.callback_query(F.data == "add_result")
async def add_result_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AddResultForm.waiting_for_datetime)
    await call.answer()
    await call.message.answer(
        "📊 <b>Добавление итогов</b>\n\n"
        "Шаг 1. Введите дату и время игры (например: 12.11.2026 16:00):\n\n"
        "Для отмены напишите /cancel."
    )

@dp.message(AddResultForm.waiting_for_datetime)
async def add_result_datetime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    dt = (message.text or "").strip()
    if not dt or len(dt) > 50:
        await message.answer("❌ Введите корректную дату и время.")
        return
    await state.update_data(result_datetime=dt)
    await state.set_state(AddResultForm.waiting_for_match)
    await message.answer("Шаг 2. Введите название матча (например: Барыс vs Адмирал):")

@dp.message(AddResultForm.waiting_for_match)
async def add_result_match(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    match = (message.text or "").strip()
    if not match or len(match) > 100:
        await message.answer("❌ Введите корректное название матча.")
        return
    await state.update_data(result_match=match)
    await state.set_state(AddResultForm.waiting_for_score)
    await message.answer("Шаг 3. Введите счет (например: 3:0):")

@dp.message(AddResultForm.waiting_for_score)
async def add_result_score(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    score = (message.text or "").strip()
    if not score or len(score) > 20:
        await message.answer("❌ Введите корректный счет.")
        return
    await state.update_data(result_score=score)
    await state.set_state(AddResultForm.waiting_for_winner)
    await message.answer("Шаг 4. Введите название победившей команды (например: Барыс):")

@dp.message(AddResultForm.waiting_for_winner)
async def add_result_winner(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    winner = (message.text or "").strip()
    if not winner or len(winner) > 100:
        await message.answer("❌ Введите корректное название команды.")
        return

    data = await state.get_data()
    with get_db() as db:
        db.execute("""
            INSERT INTO results (match_name, date_time, score, winner, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (data["result_match"], data["result_datetime"], data["result_score"], winner, datetime.now().strftime("%d.%m.%Y %H:%M")))

    await state.clear()
    await message.answer("✅ Итоги добавлены.")
    await message.answer("⚙️ Админ-панель:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]]))


@dp.callback_query(F.data == "delete_result")
async def delete_result_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await state.set_state(DeleteResultForm.waiting_for_match)
    await call.answer()
    await call.message.answer(
        "🗑 <b>Удаление итогов</b>\n\n"
        "Введите название матча, итоги которого хотите удалить (например: Барыс vs Адмирал):\n\n"
        "Для отмены напишите /cancel."
    )

@dp.message(DeleteResultForm.waiting_for_match)
async def delete_result_match(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    match = (message.text or "").strip()
    with get_db() as db:
        cursor = db.execute("DELETE FROM results WHERE match_name = ?", (match,))
    
    if cursor.rowcount == 0:
        await message.answer("❌ Итоги с таким названием не найдены.")
    else:
        await message.answer("✅ Итоги удалены.")
    
    await state.clear()
    await message.answer("⚙️ Админ-панель:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]]))


# =====================================================
# ДОБАВЛЕНИЕ КАПИТАНА
# =====================================================

@dp.callback_query(F.data == "add_captain")
async def add_captain_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) FROM captains").fetchone()[0]
    if count >= 5:
        await call.answer("Все пять мест капитанов уже заняты.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AddCaptainForm.waiting_for_name)
    await call.answer()
    await call.message.answer(
        "➕ <b>Добавление капитана</b>\n\n"
        "1. Введите никнейм и номер капитана.\n\n"
        "Пример: <code>h1zEr #99</code>\n\nДля отмены напишите /cancel."
    )

@dp.message(AddCaptainForm.waiting_for_name)
async def add_captain_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name or len(name) > 100:
        await message.answer("❌ Введите корректный никнейм (до 100 символов).")
        return
    await state.update_data(captain_name=name)
    await state.set_state(AddCaptainForm.waiting_for_team)
    await message.answer("2. Введите название команды.\n\nПример: <code>Адмирал</code>")

@dp.message(AddCaptainForm.waiting_for_team)
async def add_captain_team(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    team = (message.text or "").strip()
    if not team or len(team) > 100:
        await message.answer("❌ Введите корректное название команды.")
        return
    await state.update_data(captain_team=team)
    await state.set_state(AddCaptainForm.waiting_for_telegram)
    await message.answer("3. Отправьте ссылку на Telegram капитана.\n\nПример: <code>https://t.me/h1zEr54</code>")

@dp.message(AddCaptainForm.waiting_for_telegram)
async def add_captain_telegram(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    telegram = (message.text or "").strip()
    if not valid_telegram_link(telegram):
        await message.answer("❌ Некорректная ссылка.\nИспользуйте формат https://t.me/username")
        return
    data = await state.get_data()
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) FROM captains").fetchone()[0]
        if count >= 5:
            await message.answer("❌ Все пять мест капитанов уже заняты.")
            await state.clear()
            return
        exists = db.execute("SELECT id FROM captains WHERE team = ?", (data["captain_team"],)).fetchone()
        if exists:
            await message.answer("❌ Эта команда уже добавлена. Введите другое название команды.")
            return
        db.execute("""
            INSERT INTO captains (name, team, telegram, added_by, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            data["captain_name"], data["captain_team"], telegram,
            message.from_user.id, datetime.now().strftime("%d.%m.%Y %H:%M")
        ))
    await state.clear()
    await message.answer(
        "✅ <b>Капитан успешно добавлен!</b>\n\n"
        f"👤 Никнейм: {escape(data['captain_name'])}\n"
        f"🏆 Команда: {escape(data['captain_team'])}\n"
        f"🔗 Telegram: <a href=\"{escape(telegram)}\">Открыть профиль</a>"
    )
    await message.answer(
        "⚙️ <b>Панель администрации</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить капитана", callback_data="add_captain")],
            [InlineKeyboardButton(text="➖ Снять капитана", callback_data="remove_captain")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="home")]
        ])
    )


# =====================================================
# СНЯТИЕ КАПИТАНА
# =====================================================

@dp.callback_query(F.data == "remove_captain")
async def remove_captain_start(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await call.answer()
    with get_db() as db:
        captains = db.execute("SELECT id, name, team FROM captains ORDER BY id").fetchall()
    if not captains:
        await call.message.edit_text(
            "➖ <b>Снятие капитана</b>\n\nСейчас нет добавленных капитанов.",
            reply_markup=back_keyboard()
        )
        return
    builder = InlineKeyboardBuilder()
    for captain in captains:
        builder.button(
            text=f"❌ {captain['name']} — {captain['team']}",
            callback_data=f"remove_captain_confirm:{captain['id']}"
        )
    builder.button(text="⬅️ Назад в админ-панель", callback_data="admin_panel")
    builder.adjust(1)
    await call.message.edit_text(
        "➖ <b>Снятие капитана</b>\n\nВыберите капитана, которого нужно снять:",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("remove_captain_confirm:"))
async def remove_captain_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    try:
        captain_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer("Некорректный запрос.", show_alert=True)
        return
    with get_db() as db:
        captain = db.execute("SELECT name, team FROM captains WHERE id = ?", (captain_id,)).fetchone()
    if not captain:
        await call.answer("Капитан уже удалён или не найден.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, снять", callback_data=f"remove_captain_final:{captain_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="remove_captain")
        ]
    ])
    await call.answer()
    await call.message.edit_text(
        "⚠️ <b>Подтверждение снятия капитана</b>\n\n"
        f"👤 Никнейм: {escape(captain['name'])}\n"
        f"🏆 Команда: {escape(captain['team'])}\n\n"
        "Вы действительно хотите снять этого капитана?",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("remove_captain_final:"))
async def remove_captain_final(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    try:
        captain_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer("Некорректный запрос.", show_alert=True)
        return
    with get_db() as db:
        cursor = db.execute("DELETE FROM captains WHERE id = ?", (captain_id,))
    if cursor.rowcount == 0:
        await call.answer("Капитан уже удалён.", show_alert=True)
        return
    await call.answer("Капитан снят.")
    await call.message.edit_text(
        "✅ <b>Капитан успешно снят!</b>\n\nЕго место теперь отображается как «Свободно».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➖ Снять ещё капитана", callback_data="remove_captain")],
            [InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]
        ])
    )


# =====================================================
# РАЗБАН
# =====================================================

@dp.callback_query(F.data == "unban")
async def unban_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await state.set_state(UnbanForm.waiting_for_steamid)
    await call.message.answer(
        "🔓 <b>Заявка на разбан</b>\n\n"
        "Шаг 1. Введите ваш SteamID64 (17 цифр, начинается на 765...):\n\n"
        "Для отмены напишите /cancel."
    )

@dp.message(UnbanForm.waiting_for_steamid)
async def unban_steamid(message: Message, state: FSMContext):
    steam_id = (message.text or "").strip()
    if not re.fullmatch(r"765\d{14}", steam_id):
        await message.answer("❌ Неверный формат SteamID64. Он должен начинаться с 765 и содержать 17 цифр.")
        return
    await state.update_data(unban_steamid=steam_id)
    await state.set_state(UnbanForm.waiting_for_reason)
    await message.answer("Шаг 2. Опишите суть бана (за что забанили):")

@dp.message(UnbanForm.waiting_for_reason)
async def unban_reason(message: Message, state: FSMContext):
    reason = (message.text or "").strip()
    if not reason or len(reason) > 1000:
        await message.answer("❌ Опишите причину (до 1000 символов).")
        return
    await state.update_data(unban_reason=reason)
    await state.set_state(UnbanForm.waiting_for_date)
    await message.answer("Шаг 3. Введите дату выдачи бана (например, 12.11.2026):")

@dp.message(UnbanForm.waiting_for_date)
async def unban_date(message: Message, state: FSMContext):
    ban_date = (message.text or "").strip()
    if not ban_date or len(ban_date) > 50:
        await message.answer("❌ Введите корректную дату.")
        return
    await state.update_data(unban_date=ban_date)
    await state.set_state(UnbanForm.waiting_for_media)
    await message.answer(
        "Шаг 4. Прикрепите доказательства (фото/видео бана), если есть.\n"
        "Когда закончите, напишите «Готово». Если доказательств нет — «Пропустить»."
    )

@dp.message(UnbanForm.waiting_for_media)
async def unban_media(message: Message, state: FSMContext):
    data = await state.get_data()
    media_items = list(data.get("unban_media_items", []))
    
    command = (message.text or "").strip().lower()
    if command in {"готово", "гатово", "done", "завершить"}:
        if not media_items:
            await message.answer("Вы не прикрепили файлы. Напишите «Пропустить», если их нет.")
            return
        await _send_unban_request(message, state, media_items=media_items)
        return

    if command in {"пропустить", "нет", "без фото", "без видео"}:
        await _send_unban_request(message, state)
        return

    media_type = None
    media_file_id = None
    if message.photo:
        media_type, media_file_id = "photo", message.photo[-1].file_id
    elif message.video:
        media_type, media_file_id = "video", message.video.file_id
    elif message.document:
        media_type, media_file_id = "document", message.document.file_id

    if media_type:
        lock = _user_locks.setdefault(message.from_user.id, asyncio.Lock())
        async with lock:
            latest = await state.get_data()
            media_items = list(latest.get("unban_media_items", []))
            media_items.append((media_type, media_file_id))
            await state.update_data(unban_media_items=media_items)
            count = len(media_items)
        await message.answer(f"📎 Файл добавлен ({count}). Можете отправить ещё или написать «Готово».")
        return

    await message.answer("Прикрепите фото/видео/документ. Когда закончите, напишите «Готово».")

async def _send_unban_request(message: Message, state: FSMContext, media_items: list | None = None):
    data = await state.get_data()
    user = message.from_user
    username = f"@{user.username}" if user.username else user.full_name

    with get_db() as db:
        cursor = db.execute("""
            INSERT INTO unban_requests (user_id, username, steam_id, reason, ban_date, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """, (
            user.id, username, data["unban_steamid"], data["unban_reason"],
            data["unban_date"], datetime.now().strftime("%d.%m.%Y %H:%M")
        ))
        request_id = cursor.lastrowid

    admin_text = (
        "🔓 <b>Новая заявка на разбан</b>\n\n"
        f"🆔 Номер: <code>{request_id}</code>\n"
        f"👤 Отправитель: {escape(username)}\n"
        f"🆔 Telegram ID: <code>{user.id}</code>\n"
        f"🎮 SteamID64: <code>{escape(data['unban_steamid'])}</code>\n"
        f"📅 Дата бана: {escape(data['unban_date'])}\n\n"
        f"📝 <b>Суть бана:</b>\n{escape(data['unban_reason'])}"
    )

    try:
        await bot.send_message(OWNER_ID, admin_text)
        if media_items:
            album = []
            for kind, file_id in media_items:
                if kind == "photo":
                    album.append(InputMediaPhoto(media=file_id))
                elif kind == "video":
                    album.append(InputMediaVideo(media=file_id))
                elif kind == "document":
                    album.append(InputMediaDocument(media=file_id))
            
            if len(album) >= 2:
                await bot.send_media_group(OWNER_ID, album)
            elif len(album) == 1:
                kind, file_id = media_items[0]
                if kind == "photo":
                    await bot.send_photo(OWNER_ID, file_id)
                elif kind == "video":
                    await bot.send_video(OWNER_ID, file_id)
                else:
                    await bot.send_document(OWNER_ID, file_id)
    except TelegramAPIError:
        logger.exception("Ошибка отправки заявки на разбан владельцу")

    await state.clear()
    await message.answer(
        f"✅ <b>Заявка на разбан №{request_id} отправлена!</b>\n\n"
        "Владелец рассмотрит её в ближайшее время."
    )
    await show_main_menu(message)


# =====================================================
# ЖАЛОБЫ
# =====================================================

@dp.callback_query(F.data == "complaint")
async def complaint_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await state.set_state(ComplaintForm.waiting_for_nickname)
    await call.message.answer(
        "⚠️ <b>Жалоба на администрацию</b>\n\n"
        "Шаг 1. Введите ваш игровой никнейм:\n\n"
        "Для отмены напишите /cancel."
    )

@dp.message(ComplaintForm.waiting_for_nickname)
async def complaint_nickname(message: Message, state: FSMContext):
    nickname = (message.text or "").strip()
    if not nickname or len(nickname) > 100:
        await message.answer("❌ Введите корректный никнейм (до 100 символов).")
        return
    await state.update_data(complaint_nickname=nickname)
    await state.set_state(ComplaintForm.waiting_for_admin_nickname)
    await message.answer("Шаг 2. Введите никнейм администратора, на которого жалуетесь:")

@dp.message(ComplaintForm.waiting_for_admin_nickname)
async def complaint_admin_nickname(message: Message, state: FSMContext):
    admin_nickname = (message.text or "").strip()
    if not admin_nickname or len(admin_nickname) > 100:
        await message.answer("❌ Введите корректный никнейм администратора.")
        return
    await state.update_data(complaint_admin_nickname=admin_nickname)
    await state.set_state(ComplaintForm.waiting_for_violation)
    await message.answer("Шаг 3. Опишите, что нарушил администратор (суть проблемы):")

@dp.message(ComplaintForm.waiting_for_violation)
async def complaint_violation(message: Message, state: FSMContext):
    violation = (message.text or "").strip()
    if not violation or len(violation) > 3500:
        await message.answer("❌ Опишите нарушение (до 3500 символов).")
        return
    await state.update_data(complaint_violation=violation)
    await state.set_state(ComplaintForm.waiting_for_proof)
    await message.answer(
        "Шаг 4. Прикрепите доказательства (фото/видео/документы).\n"
        "Можно прикрепить до 4-х штук. Когда закончите, напишите «Готово». Если доказательств нет — «Пропустить»."
    )

@dp.message(ComplaintForm.waiting_for_proof)
async def complaint_proof(message: Message, state: FSMContext):
    data = await state.get_data()
    media_items = list(data.get("complaint_media_items", []))
    media_refs = list(data.get("complaint_media_refs", []))

    command = (message.text or "").strip().lower()
    if command in {"готово", "гатово", "done", "завершить"}:
        if not media_items:
            await message.answer("Вы ещё не прикрепили доказательства. Отправьте фото или напишите «Пропустить».")
            return
        await _send_complaint_to_owner(message, state, media_items=media_items, media_refs=media_refs)
        return

    if command in {"пропустить", "нет", "без фото", "без видео"}:
        await _send_complaint_to_owner(message, state)
        return

    media_type = None
    media_file_id = None
    if message.photo:
        media_type, media_file_id = "photo", message.photo[-1].file_id
    elif message.video:
        media_type, media_file_id = "video", message.video.file_id
    elif message.document:
        media_type, media_file_id = "document", message.document.file_id

    if media_type:
        lock = _user_locks.setdefault(message.from_user.id, asyncio.Lock())
        async with lock:
            latest = await state.get_data()
            media_items = list(latest.get("complaint_media_items", []))
            media_refs = list(latest.get("complaint_media_refs", []))
            
            if len(media_items) >= 4:
                await message.answer("❌ К сожалению, я не принимаю больше 4-х фотографий за раз.")
                return

            media_items.append((media_type, media_file_id))
            media_refs.append((message.chat.id, message.message_id))
            await state.update_data(complaint_media_items=media_items, complaint_media_refs=media_refs)
            count = len(media_items)
        await message.answer(f"📎 Доказательство добавлено ({count}/4). Можете отправить ещё или написать «Готово».")
        return

    await message.answer("Прикрепите фото/видео/документ. Когда закончите, напишите «Готово».")


async def _send_complaint_to_owner(message: Message, state: FSMContext, media_items: list | None = None, media_refs: list | None = None):
    data = await state.get_data()
    user = message.from_user
    username = f"@{user.username}" if user.username else user.full_name

    text = (
        f"1. Никнейм заявителя: {data['complaint_nickname']}\n"
        f"2. Никнейм администратора: {data['complaint_admin_nickname']}\n"
        f"3. Что нарушил: {data['complaint_violation']}"
    )

    with get_db() as db:
        cursor = db.execute("""
            INSERT INTO complaints (user_id, username, text, status, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user.id, username, text, "pending", datetime.now().strftime("%d.%m.%Y %H:%M")))
        complaint_id = cursor.lastrowid

    admin_text = (
        f"⚠️ <b>Новая жалоба №{complaint_id}</b>\n\n"
        f"👤 Отправитель: {escape(username)}\n"
        f"🆔 Telegram ID: <code>{user.id}</code>\n\n"
        f"📝 <b>Детали жалобы:</b>\n{escape(text)}"
    )

    try:
        await bot.send_message(
            OWNER_ID,
            admin_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"complaint:approve:{complaint_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"complaint:reject:{complaint_id}")
                ]
            ])
        )
        
        if media_items:
            album = []
            for kind, file_id in media_items:
                if kind == "photo":
                    album.append(InputMediaPhoto(media=file_id))
                elif kind == "video":
                    album.append(InputMediaVideo(media=file_id))
                elif kind == "document":
                    album.append(InputMediaDocument(media=file_id))
            
            if len(album) >= 2:
                await bot.send_media_group(OWNER_ID, album)
            elif len(album) == 1:
                kind, file_id = media_items[0]
                if kind == "photo":
                    await bot.send_photo(OWNER_ID, file_id)
                elif kind == "video":
                    await bot.send_video(OWNER_ID, file_id)
                else:
                    await bot.send_document(OWNER_ID, file_id)
                    
    except TelegramAPIError:
        logger.exception("Ошибка отправки жалобы владельцу")

    await state.clear()
    await message.answer(
        f"✅ <b>Жалоба №{complaint_id} успешно отправлена!</b>\n\n"
        "Администрация рассмотрит её в ближайшее время."
    )
    await show_main_menu(message)


@dp.callback_query(F.data.startswith("complaint:"))
async def complaint_decision(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    try:
        _, action, complaint_id = call.data.split(":")
        complaint_id = int(complaint_id)
    except (ValueError, AttributeError):
        await call.answer("Некорректный запрос.")
        return
    if action == "approve":
        status = "approved"
        result = "одобрена"
    elif action == "reject":
        status = "rejected"
        result = "отклонена"
    else:
        await call.answer("Неизвестное действие.")
        return
    with get_db() as db:
        cursor = db.execute("""
            UPDATE complaints SET status = ? WHERE id = ? AND status = 'pending'
        """, (status, complaint_id))
        complaint = db.execute("SELECT user_id FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
    if cursor.rowcount == 0:
        await call.answer("Жалоба уже обработана или не найдена.", show_alert=True)
        return
    await call.answer("Статус обновлён.")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer(f"✅ Жалоба №{complaint_id} {result}.")
    except TelegramAPIError:
        logger.exception("Не удалось обновить сообщение жалобы")
    if complaint:
        try:
            await bot.send_message(complaint["user_id"], f"📋 Ваша жалоба №{complaint_id} {result}.")
        except TelegramAPIError:
            logger.exception("Не удалось уведомить автора жалобы")


# =====================================================
# ПРЕДЛОЖЕНИЯ
# =====================================================

@dp.callback_query(F.data == "suggestion")
async def suggestion_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await state.set_state(SuggestionForm.waiting_for_nickname)
    await call.message.answer(
        "💡 <b>Форма подачи заявок по улучшению</b>\n\n"
        "Шаг 1. Введите ваш игровой никнейм:\n\n"
        "Для отмены напишите /cancel."
    )

@dp.message(SuggestionForm.waiting_for_nickname)
async def suggestion_nickname(message: Message, state: FSMContext):
    nickname = (message.text or "").strip()
    if not nickname or len(nickname) > 100:
        await message.answer("❌ Введите корректный никнейм (до 100 символов).")
        return
    await state.update_data(suggestion_nickname=nickname)
    await state.set_state(SuggestionForm.waiting_for_text)
    await message.answer("Шаг 2. Опишите суть вашего предложения (до 3500 символов):")

@dp.message(SuggestionForm.waiting_for_text)
async def suggestion_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text or len(text) > 3500:
        await message.answer("❌ Отправьте текст предложения (до 3500 символов).")
        return
    await state.update_data(suggestion_text=text)
    await state.set_state(SuggestionForm.waiting_for_media)
    await message.answer(
        "Шаг 3. Прикрепите фото/видео/документы (если есть).\n"
        "Когда закончите, напишите «Готово». Если вложений нет — «Пропустить»."
    )

@dp.message(SuggestionForm.waiting_for_media)
async def suggestion_media(message: Message, state: FSMContext):
    data = await state.get_data()
    media_items = list(data.get("suggestion_media_items", []))
    media_refs = list(data.get("suggestion_media_refs", []))

    command = (message.text or "").strip().lower()
    if command in {"готово", "гатово", "done", "завершить"}:
        if not media_items:
            await message.answer("Вы ещё не прикрепили файлы. Отправьте фото/видео или напишите «Пропустить».")
            return
        await submit_suggestion(message, state, media_items=media_items, media_refs=media_refs)
        return

    if command in {"пропустить", "нет", "без фото", "без видео"}:
        await submit_suggestion(message, state)
        return

    media_type = None
    media_file_id = None
    if message.photo:
        media_type, media_file_id = "photo", message.photo[-1].file_id
    elif message.video:
        media_type, media_file_id = "video", message.video.file_id
    elif message.document:
        media_type, media_file_id = "document", message.document.file_id

    if media_type:
        lock = _user_locks.setdefault(message.from_user.id, asyncio.Lock())
        async with lock:
            latest = await state.get_data()
            media_items = list(latest.get("suggestion_media_items", []))
            media_refs = list(latest.get("suggestion_media_refs", []))
            media_items.append((media_type, media_file_id))
            media_refs.append((message.chat.id, message.message_id))
            await state.update_data(suggestion_media_items=media_items, suggestion_media_refs=media_refs)
            count = len(media_items)
        await message.answer(f"📎 Файл добавлен ({count}). Можете отправить ещё или написать «Готово».")
        return

    await message.answer("Прикрепите фото, видео или документ. Когда закончите, напишите «Готово».")


async def submit_suggestion(message: Message, state: FSMContext, media_items: list | None = None, media_refs: list | None = None):
    data = await state.get_data()
    user = message.from_user
    username = f"@{user.username}" if user.username else user.full_name
    
    media_type = media_items[0][0] if media_items else None
    media_file_id = media_items[0][1] if media_items else None

    with get_db() as db:
        cursor = db.execute("""
            INSERT INTO suggestions (user_id, username, nickname, text, media_type, media_file_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            user.id, username, data["suggestion_nickname"],
            data["suggestion_text"], media_type, media_file_id,
            datetime.now().strftime("%d.%m.%Y %H:%M")
        ))
        suggestion_id = cursor.lastrowid
        for source_chat_id, message_id in (media_refs or []):
            db.execute(
                "INSERT INTO suggestion_media_refs (suggestion_id, source_chat_id, message_id) VALUES (?, ?, ?)",
                (suggestion_id, source_chat_id, message_id)
            )

    admin_ids = set(get_registered_admin_ids())
    admin_ids.add(OWNER_ID)
    
    admin_text = (
        "💡 <b>Новое предложение по улучшению</b>\n\n"
        f"🆔 Заявка №<code>{suggestion_id}</code>\n"
        f"👤 Отправитель: {escape(username)}\n"
        f"🆔 Telegram ID: <code>{user.id}</code>\n"
        f"🎮 Никнейм: {escape(data['suggestion_nickname'])}\n\n"
        f"📝 <b>Предложение:</b>\n{escape(data['suggestion_text'])}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✉️ Ответить", callback_data=f"suggestion_reply:{suggestion_id}")]])

    delivered = 0
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, admin_text, reply_markup=keyboard)
            delivered += 1
            if media_items:
                album = []
                for kind, file_id in media_items:
                    if kind == "photo":
                        album.append(InputMediaPhoto(media=file_id))
                    elif kind == "video":
                        album.append(InputMediaVideo(media=file_id))
                    elif kind == "document":
                        album.append(InputMediaDocument(media=file_id))
                
                for offset in range(0, len(album), 10):
                    chunk = album[offset:offset + 10]
                    if len(chunk) >= 2:
                        await bot.send_media_group(admin_id, chunk)
                    elif len(chunk) == 1:
                        kind, file_id = media_items[offset]
                        if kind == "photo":
                            await bot.send_photo(admin_id, file_id)
                        elif kind == "video":
                            await bot.send_video(admin_id, file_id)
                        else:
                            await bot.send_document(admin_id, file_id)
        except TelegramAPIError:
            logger.warning("Не удалось уведомить администратора %s", admin_id, exc_info=True)

    await state.clear()
    result = (
        "✅ <b>Предложение принято и сохранено!</b>\n"
        f"Номер заявки: <code>{suggestion_id}</code>\n"
        "Администрация рассмотрит его и при необходимости ответит вам."
    )
    if not delivered:
        result += "\n\n⚠️ Заявка сохранена, но уведомления администраторам не доставлены."
    await message.answer(result)
    await show_main_menu(message)


# =====================================================
# ОТВЕТ НА ПРЕДЛОЖЕНИЕ
# =====================================================

@dp.callback_query(F.data.startswith("suggestion_reply:"))
async def suggestion_reply_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    register_admin_user(call.from_user.id, call.from_user.username)
    try:
        suggestion_id = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer("Некорректный номер заявки.", show_alert=True)
        return
    with get_db() as db:
        suggestion = db.execute("SELECT user_id FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if not suggestion:
        await call.answer("Заявка не найдена.", show_alert=True)
        return
    await state.clear()
    await state.set_state(SuggestionReplyForm.waiting_for_reply)
    await state.update_data(suggestion_id=suggestion_id, suggestion_user_id=suggestion["user_id"])
    await call.answer()
    await call.message.answer(f"✉️ Напишите ответ на предложение №{suggestion_id}.\nОтвет отправится автору заявки. Для отмены — /cancel.")

@dp.message(SuggestionReplyForm.waiting_for_reply)
async def suggestion_reply_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    reply_text = (message.text or message.caption or "").strip()
    if not reply_text and not (message.photo or message.video or message.document):
        await message.answer("Отправьте текст или медиафайл с подписью.")
        return
    data = await state.get_data()
    user_id = data["suggestion_user_id"]
    suggestion_id = data["suggestion_id"]
    header = f"💬 <b>Ответ администрации на предложение №{suggestion_id}</b>\n\n"
    try:
        if reply_text:
            await bot.send_message(user_id, header + escape(reply_text))
        elif message.photo:
            await bot.send_photo(user_id, message.photo[-1].file_id, caption=header)
        elif message.video:
            await bot.send_video(user_id, message.video.file_id, caption=header)
        elif message.document:
            await bot.send_document(user_id, message.document.file_id, caption=header)
    except TelegramAPIError:
        logger.exception("Не удалось отправить ответ автору предложения")
        await message.answer("❌ Не удалось отправить ответ. Возможно, пользователь заблокировал бота.")
        await state.clear()
        return
    with get_db() as db:
        db.execute("UPDATE suggestions SET status = 'answered' WHERE id = ?", (suggestion_id,))
    await state.clear()
    await message.answer(f"✅ Ответ на заявку №{suggestion_id} отправлен.")


# =====================================================
# ИСТОРИЯ ЖАЛОБ
# =====================================================

@dp.callback_query(F.data == "complaint_history")
async def complaint_history(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await call.answer()
    with get_db() as db:
        complaints = db.execute("SELECT * FROM complaints ORDER BY id DESC LIMIT 10").fetchall()
    text = "📋 <b>Последние жалобы</b>\n\n"
    if not complaints:
        text += "Жалоб пока нет."
    else:
        for item in complaints:
            status = {"pending": "⏳ На рассмотрении", "approved": "✅ Одобрена", "rejected": "❌ Отклонена"}.get(item["status"], item["status"])
            text += (
                f"🆔 <b>Жалоба №{item['id']}</b>\n"
                f"👤 {escape(item['username'])}\n"
                f"📅 {escape(item['created_at'])}\n"
                f"📌 {status}\n"
                f"📝 {escape(item['text'][:200])}\n\n"
            )
    await call.message.edit_text(text, reply_markup=back_keyboard())


# =====================================================
# УПРАВЛЕНИЕ АДМИНИСТРАЦИЕЙ
# =====================================================

@dp.callback_query(F.data == "manage_admins")
async def manage_admins(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить администратора", callback_data="add_admin")
    builder.button(text="➖ Удалить администратора", callback_data="remove_admin")
    builder.button(text="⬅️ Назад", callback_data="admin_panel")
    builder.adjust(1)
    await call.answer()
    await call.message.edit_text("👥 <b>Управление администрацией</b>", reply_markup=builder.as_markup())


@dp.callback_query(F.data == "add_admin")
async def add_admin_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    
    builder = InlineKeyboardBuilder()
    for level in LEVELS:
        builder.button(text=level, callback_data=f"level:{level}")
    builder.button(text="⬅️ Назад", callback_data="manage_admins")
    builder.adjust(1)
    
    await state.set_state(AddAdminForm.waiting_for_level)
    await call.answer()
    await call.message.edit_text(
        "➕ <b>Добавление администратора</b>\n\n"
        "Шаг 1. Выберите уровень администратора:",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(AddAdminForm.waiting_for_level, F.data.startswith("level:"))
async def add_admin_level(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    
    level = call.data.split(":", 1)[1]
    if level not in LEVELS:
        await call.answer("Некорректный уровень.")
        return
    
    await state.update_data(level=level)
    
    builder = InlineKeyboardBuilder()
    for position in POSITIONS:
        builder.button(text=position, callback_data=f"position:{position}")
    builder.button(text="⬅️ Назад", callback_data="add_admin")
    builder.adjust(1)
    
    await state.set_state(AddAdminForm.waiting_for_position)
    await call.answer()
    await call.message.edit_text(
        f"Уровень: <b>{level}</b>\n\n"
        "Шаг 2. Выберите должность:",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(AddAdminForm.waiting_for_position, F.data.startswith("position:"))
async def add_admin_position(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    
    position = call.data.split(":", 1)[1]
    if position not in POSITIONS:
        await call.answer("Некорректная должность.")
        return
    
    await state.update_data(position=position)
    await state.set_state(AddAdminForm.waiting_for_name)
    await call.answer()
    await call.message.answer("Шаг 3. Введите Никнейм + номер (например: h1zEr #99):")


@dp.message(AddAdminForm.waiting_for_name)
async def add_admin_name(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name or len(name) > 100:
        await message.answer("❌ Введите корректный никнейм (до 100 символов).")
        return
    await state.update_data(name=name)
    await state.set_state(AddAdminForm.waiting_for_link)
    await message.answer("Шаг 4. Отправьте ссылку Telegram в формате:\n<code>https://t.me/username</code>")


@dp.message(AddAdminForm.waiting_for_link)
async def add_admin_link(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    link = (message.text or "").strip()
    if not valid_telegram_link(link):
        await message.answer("❌ Некорректная ссылка. Используйте https://t.me/username")
        return
    
    data = await state.get_data()
    
    with get_db() as db:
        db.execute("DELETE FROM admins WHERE position = ?", (data["position"],))
        db.execute(
            "INSERT INTO admins (position, name, telegram, level) VALUES (?, ?, ?, ?)",
            (data["position"], data["name"], link, data["level"])
        )
    
    await state.clear()
    await message.answer("✅ Администратор добавлен.")
    await message.answer("⚙️ Админ-панель:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]]))


@dp.callback_query(F.data == "remove_admin")
async def remove_admin_start(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await call.answer()
    with get_db() as db:
        admins = db.execute("SELECT id, name, position FROM admins WHERE position != 'Владелец лиги' ORDER BY id").fetchall()
    if not admins:
        await call.message.edit_text("Нет администраторов для удаления.", reply_markup=back_keyboard())
        return
    builder = InlineKeyboardBuilder()
    for admin in admins:
        builder.button(text=f"❌ {admin['name']} — {admin['position']}", callback_data=f"delete_admin:{admin['id']}")
    builder.button(text="⬅️ Назад", callback_data="manage_admins")
    builder.adjust(1)
    await call.message.edit_text("➖ <b>Выберите администратора для удаления:</b>", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("delete_admin:"))
async def remove_admin_confirm(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    try:
        admin_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer("Некорректный запрос.")
        return
    with get_db() as db:
        cursor = db.execute("DELETE FROM admins WHERE id = ? AND position != 'Владелец лиги'", (admin_id,))
    if cursor.rowcount == 0:
        await call.answer("Администратор не найден.", show_alert=True)
        return
    await call.answer("Администратор удалён.")
    await call.message.edit_text("✅ Администратор удалён.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]]))


# =====================================================
# ЗАПУСК
# =====================================================

async def main():
    init_db()
    logger.info("MPHL bot started")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
