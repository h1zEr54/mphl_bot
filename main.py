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

# База данных в /app/data для Bothost
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
        if "warnings" not in columns:
            db.execute("ALTER TABLE admins ADD COLUMN warnings INTEGER NOT NULL DEFAULT 0")
        if "reprimands" not in columns:
            db.execute("ALTER TABLE admins ADD COLUMN reprimands INTEGER NOT NULL DEFAULT 0")
        db.execute("""
            CREATE TABLE IF NOT EXISTS admin_discipline_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_admin_id INTEGER,
                target_name TEXT NOT NULL,
                target_position TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                owner_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

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
        captain_columns = [row["name"] for row in db.execute("PRAGMA table_info(captains)").fetchall()]
        if "telegram_id" not in captain_columns:
            db.execute("ALTER TABLE captains ADD COLUMN telegram_id INTEGER")
        if "warnings" not in captain_columns:
            db.execute("ALTER TABLE captains ADD COLUMN warnings INTEGER NOT NULL DEFAULT 0")
        if "reprimands" not in captain_columns:
            db.execute("ALTER TABLE captains ADD COLUMN reprimands INTEGER NOT NULL DEFAULT 0")

        db.execute("""
            CREATE TABLE IF NOT EXISTS captain_discipline_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captain_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                admin_id INTEGER NOT NULL,
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
        db.execute("""
            CREATE TABLE IF NOT EXISTS team_stats (
                team_name TEXT PRIMARY KEY,
                games INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                points INTEGER DEFAULT 0
            )
        """)
        result_columns = [row["name"] for row in db.execute("PRAGMA table_info(results)").fetchall()]
        if "overtime" not in result_columns:
            db.execute("ALTER TABLE results ADD COLUMN overtime INTEGER NOT NULL DEFAULT 0")

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


def telegram_username_from_link(link: str) -> str:
    match = re.fullmatch(r"https?://(?:www\.)?t\.me/([A-Za-z0-9_]+)/?", (link or "").strip(), flags=re.IGNORECASE)
    return (match.group(1).lower() if match else "")


def bind_captain_telegram_id(user_id: int, username: str | None):
    """Привязывает Telegram ID к капитану по username из сохранённой ссылки."""
    if not username:
        return
    normalized = username.lower().lstrip("@")
    with get_db() as db:
        rows = db.execute("SELECT id, telegram FROM captains").fetchall()
        for row in rows:
            if telegram_username_from_link(row["telegram"]) == normalized:
                db.execute("UPDATE captains SET telegram_id = ? WHERE id = ?", (user_id, row["id"]))
                break


def is_captain(user_id: int) -> bool:
    with get_db() as db:
        return db.execute(
            "SELECT 1 FROM captains WHERE telegram_id = ?", (user_id,)
        ).fetchone() is not None


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


def valid_telegram_profile_link(link: str):
    return bool(re.fullmatch(r"https?://(?:www\.)?t\.me/[A-Za-z0-9_]+/?", (link or "").strip(), flags=re.IGNORECASE))


def update_stats(match_name: str, winner: str, overtime: bool = False):
    """Обновляет статистику: ОТ — 2 очка победителю и 1 проигравшему; основное время — 1 и 0."""
    teams = re.split(r'\s+vs\.?\s+', match_name, flags=re.IGNORECASE)
    if len(teams) != 2:
        return

    team1, team2 = teams[0].strip(), teams[1].strip()
    if not team1 or not team2:
        return

    with get_db() as db:
        for team in (team1, team2):
            db.execute("INSERT OR IGNORE INTO team_stats (team_name) VALUES (?)", (team,))

        db.execute("UPDATE team_stats SET games = games + 1 WHERE team_name IN (?, ?)", (team1, team2))

        if winner.casefold() == team1.casefold():
            winner_points, loser_points = (2, 1) if overtime else (1, 0)
            db.execute("UPDATE team_stats SET wins = wins + 1, points = points + ? WHERE team_name = ?", (winner_points, team1))
            db.execute("UPDATE team_stats SET losses = losses + 1, points = points + ? WHERE team_name = ?", (loser_points, team2))
        elif winner.casefold() == team2.casefold():
            winner_points, loser_points = (2, 1) if overtime else (1, 0)
            db.execute("UPDATE team_stats SET wins = wins + 1, points = points + ? WHERE team_name = ?", (winner_points, team2))
            db.execute("UPDATE team_stats SET losses = losses + 1, points = points + ? WHERE team_name = ?", (loser_points, team1))


def rebuild_stats_from_results():
    """Пересчитывает таблицу статистики по сохранённым итогам, включая старые матчи."""
    with get_db() as db:
        results = db.execute("SELECT match_name, winner, overtime FROM results ORDER BY id").fetchall()
        db.execute("DELETE FROM team_stats")
    for result in results:
        update_stats(result["match_name"], result["winner"], bool(result["overtime"]))


def main_menu_keyboard(user_id: int, username: str | None = None):
    builder = InlineKeyboardBuilder()
    builder.button(text="🏆 Капитаны и их команды", callback_data="captains")
    builder.button(text="📜 Правила лиги", callback_data="rules")
    builder.button(text="👥 Состав администрации", callback_data="admins")
    builder.button(text="🔓 Разбан", callback_data="unban")
    builder.button(text="⚠️ Жалоба на администрацию", callback_data="complaint")
    builder.button(text="💡 Предложение по улучшению", callback_data="suggestion")
    builder.button(text="🎮 Игры", callback_data="games")
    builder.button(text="📊 Итоги игр", callback_data="results")
    builder.button(text="🏆 Таблица команд", callback_data="top_teams")
    if is_admin(user_id, username) or is_captain(user_id):
        builder.button(text="📋 Таблица капитанов", callback_data="captain_discipline_table")
    if is_admin(user_id, username):
        builder.button(text="⚙️ Админ-панель", callback_data="admin_panel")
    builder.adjust(2)
    return builder.as_markup()


def cancel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
    )


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

class CaptainDisciplineForm(StatesGroup):
    waiting_for_reason = State()

class AdminDisciplineForm(StatesGroup):
    waiting_for_reason = State()


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
    waiting_for_overtime = State()

class DeleteResultForm(StatesGroup):
    waiting_for_match = State()


class TableEditForm(StatesGroup):
    waiting_for_new_team = State()
    waiting_for_value = State()



# =====================================================
# СТАРТ
# =====================================================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    register_admin_user(message.from_user.id, message.from_user.username)
    bind_captain_telegram_id(message.from_user.id, message.from_user.username)
    await show_main_menu(message)


@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено.", reply_markup=cancel_keyboard())
    await show_main_menu(message)


@dp.callback_query(F.data == "cancel_action")
async def cancel_action_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Действие отменено.")
    await call.message.edit_text(
        "❌ <b>Действие отменено.</b>\n\nВы вернулись в главное меню.",
        reply_markup=main_menu_keyboard(call.from_user.id, call.from_user.username)
    )


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

@dp.callback_query(F.data == "rules")
async def rules_callback(call: CallbackQuery):
    await call.answer()
    text = (
        "<b>📜 ОФИЦИАЛЬНЫЕ ПРАВИЛА MPHL</b>\n"
        "<i>Регламент лиги · 8 разделов</i>\n\n"
        "Добро пожаловать в MPHL! Перед участием в матчах ознакомьтесь с правилами.\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>⚖️ 1. ОБЩИЕ ПОЛОЖЕНИЯ</b>\n"
        "1.1. Участие в турнирах означает согласие игроков и капитанов с правилами.\n"
        "1.2. Незнание правил не освобождает от ответственности.\n"
        "1.3. Администрация вправе трактовать спорные ситуации и изменять регламент. Её решения являются окончательными.\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>🤝 2. ДИСЦИПЛИНА И УВАЖЕНИЕ</b>\n"
        "2.1. <b>Запрещены любые оскорбления</b> игроков, капитанов и администрации в текстовом и голосовом чате.\n"
        "2.2. <b>Категорически запрещено</b> упоминать родителей, семью и родных в негативном или оскорбительном контексте, даже в шутку.\n"
        "2.3. Запрещено разжигать конфликты на национальной, религиозной или политической почве. Лига — вне политики.\n"
        "2.4. Запрещены спам, флуд, капс и провокации в чате во время матчей.\n"
        "2.5. Запрещены токсичность и намеренное унижение новичков.\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>🏒 3. ИГРОВОЙ ПРОЦЕСС</b>\n"
        "3.1. <b>Запрещено намеренно затягивать время</b> (тайм-ауты, катание шайбы у борта, бесконечные паузы) без игровой необходимости.\n"
        "3.2. <b>Запрещено «дрочерство»</b> — намеренное дёргание клюшки соперника, мешающее ему вести шайбу или бросать. Это не блокировка шайбы, а помеха движению клюшки.\n"
        "3.3. Запрещены сторонние программы (читы, макросы, скрипты, аимботы), дающие преимущество.\n"
        "3.4. Запрещены броски по воротам соперника после свистка (после гола или остановки игры).\n"
        "3.5. Запрещено намеренно использовать баги игры ради преимущества (выход за карту, застревание в текстурах и т. д.).\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>⏱ 4. МАТЧИ И ЯВКА</b>\n"
        "4.1. Команда обязана явиться на матч <b>за 10 минут до начала</b>.\n"
        "4.2. Если команда не явилась в течение <b>15 минут</b> после начала, ей засчитывается техническое поражение (0:3).\n"
        "4.3. Перенос возможен <b>только по согласованию с администрацией</b> не позднее чем за 24 часа до игры.\n"
        "4.4. Капитан обязан обеспечить полный состав. Игра в неполном составе запрещена, если иное не предусмотрено регламентом.\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>🚫 5. НАКАЗАНИЯ</b>\n"
        "5.1. Первое нарушение — <b>предупреждение</b> (устное или в чате).\n"
        "5.2. Повторное нарушение — <b>бан на 1 матч</b>.\n"
        "5.3. За грубые нарушения (оскорбление родителей, читы, агрессия, дрочерство, слив матча) — <b>бан от 1 недели до полного исключения из лиги</b>.\n"
        "5.4. Решение о бане принимает администрация после просмотра записи матча, если она есть.\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>⚖️ 6. АПЕЛЛЯЦИИ</b>\n"
        "6.1. Капитан может подать апелляцию в течение <b>24 часов</b> после матча.\n"
        "6.2. Апелляция подаётся в Telegram-канал лиги или лично администратору.\n"
        "6.3. Решение принимается в течение <b>48 часов</b> и является окончательным.\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>🔌 7. ФОРС-МАЖОР (ДИСКОННЕКТЫ)</b>\n"
        "7.1. Если игрок вылетел во время матча, игра <b>не останавливается</b>, если не оговорено иное.\n"
        "7.2. Если вылетел капитан или вся команда, матч может быть остановлен по решению администрации.\n"
        "7.3. Переигровка возможна только при технических проблемах сервера (лаги, падение сервера), подтверждённых администратором.\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>🏆 8. ЗАКЛЮЧИТЕЛЬНЫЕ ПОЛОЖЕНИЯ</b>\n"
        "8.1. Участие в лиге означает полное согласие с регламентом.\n"
        "8.2. Администрация вправе отстранить игрока или команду за систематические нарушения.\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>🏒 MPHL — ЧЕСТНАЯ ИГРА. УВАЖЕНИЕ. СПОРТ.</b>"
    )
    await call.message.edit_text(
        text,
        reply_markup=back_keyboard(),
        disable_web_page_preview=True,
    )


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
            match_type = "🏒 Овертайм" if res["overtime"] else "⏱ Основное время"
            text += f"📅 {escape(res['date_time'])} | {escape(res['match_name'])} | {escape(res['score'])} — победа: {escape(res['winner'])}\n{match_type}\n\n"
    
    await call.message.edit_text(text, reply_markup=back_keyboard())


# =====================================================
# ТОП КОМАНД
# =====================================================

@dp.callback_query(F.data == "top_teams")
async def top_teams_callback(call: CallbackQuery):
    await call.answer()

    with get_db() as db:
        stats = db.execute("""
            SELECT team_name, games, wins, losses, points
            FROM team_stats
            ORDER BY points DESC, wins DESC, team_name COLLATE NOCASE ASC
        """).fetchall()

    if not stats:
        await call.message.edit_text(
            "🏆 <b>ТУРНИРНАЯ ТАБЛИЦА MPHL</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Пока нет данных для отображения.",
            reply_markup=back_keyboard()
        )
        return

    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    lines = [
        "🏆 <b>ТУРНИРНАЯ ТАБЛИЦА MPHL</b>",
        "━━━━━━━━━━━━━━━━━━",
        "<i>Рейтинг команд по набранным очкам</i>",
        ""
    ]

    for index, row in enumerate(stats):
        place = medals.get(index, f"<b>{index + 1}.</b>")
        team_name = escape(str(row["team_name"]))
        points = int(row["points"] or 0)
        games = int(row["games"] or 0)
        wins = int(row["wins"] or 0)
        losses = int(row["losses"] or 0)

        lines.append(f"{place} <b>{team_name}</b>")
        lines.append(
            f"   🎯 <b>{points}</b> очк.   │   🏒 {games} игр.\n"
            f"   ✅ Побед: {wins}   │   ❌ Поражений: {losses}"
        )
        if index != len(stats) - 1:
            lines.append("<i>──────────────────</i>")
        lines.append("")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━",
        "<i>Очки — главный показатель рейтинга</i>"
    ])
    await call.message.edit_text("\n".join(lines), reply_markup=back_keyboard())


# =====================================================
# КАПИТАНЫ
# =====================================================

@dp.callback_query(F.data == "captains")
async def captains_callback(call: CallbackQuery):
    await call.answer()
    with get_db() as db:
        captains = db.execute(
            "SELECT name, team, telegram FROM captains ORDER BY id LIMIT 8"
        ).fetchall()

    captain_blocks = []
    for number in range(1, 9):
        if number <= len(captains):
            captain = captains[number - 1]
            name = escape(captain["name"])
            team = escape(captain["team"])
            link = (captain["telegram"] or "").strip()

            if link and re.match(r"^https?://", link, flags=re.IGNORECASE):
                captain_line = f'  └ Капитан: <a href="{escape(link)}">{name} ↗</a>'
            else:
                captain_line = f"  └ Капитан: {name}"

            block = (
                f"<b>🏒 КОМАНДА №{number}</b>\n"
                f"  ├ Название: {team}\n"
                f"{captain_line}"
            )
        else:
            block = (
                f"<b>🏒 КОМАНДА №{number}</b>\n"
                "  └ <i>Свободное место</i>"
            )
        captain_blocks.append(block)

    text = (
        "<b>🏆 КАПИТАНЫ И КОМАНДЫ MPHL</b>\n"
        "<i>Официальный список команд и их капитанов</i>\n\n"
        + "\n\n━━━━━━━━━━━━━━\n\n".join(captain_blocks)
        + "\n\n━━━━━━━━━━━━━━\n"
        + '📝 <a href="https://t.me/infomphl/7">Подать заявку на участие</a>'
    )
    await call.message.edit_text(
        text,
        reply_markup=back_keyboard(),
        disable_web_page_preview=True,
    )


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
        admin_dict.setdefault(admin["position"], []).append(admin)

    def person_line(admin):
        name = escape(admin["name"])
        link = (admin["telegram"] or "").strip()
        # Ссылки проверяются при добавлении; дополнительно ограничиваем схему для HTML.
        if re.match(r"^https?://", link, flags=re.IGNORECASE):
            return f'  ├ <a href="{escape(link)}">{name} ↗</a>'
        return f"  ├ {name}"

    def role_block(position, label=None):
        people = admin_dict.get(position, [])
        title = escape(label or position)
        if not people:
            return f"<b>{title}</b>\n  └ <i>Вакантно</i>"
        lines = [f"<b>{title}</b>"]
        lines.extend(person_line(person) for person in people)
        return "\n".join(lines)

    sections = [
        "<b>🏆 ВЫСШАЯ АДМИНИСТРАЦИЯ</b>\n\n" + "\n\n".join([
            role_block("Владелец лиги"),
            role_block("Главный администратор"),
            role_block("Главный судья"),
        ]),
        "<b>⚙️ СТАРШАЯ АДМИНИСТРАЦИЯ</b>\n\n" + "\n\n".join([
            role_block("Зам.главного администратора", "Заместитель главного администратора"),
            role_block("Зам.главного судьи", "Заместитель главного судьи"),
            role_block("Технический специалист"),
            role_block("Специалист по соц сетям", "Специалист по социальным сетям"),
        ]),
        "<b>🛡 АДМИНИСТРАЦИЯ</b>\n\n" + "\n\n".join([
            role_block("Судья 1", "Судья №1"),
            role_block("Судья 2", "Судья №2"),
            role_block("Судья 3", "Судья №3"),
        ]),
    ]
    text = (
        "<b>👥 СОСТАВ MPHL</b>\n"
        "<i>Официальная администрация лиги</i>\n\n"
        + "\n\n━━━━━━━━━━━━━━\n\n".join(sections)
        + "\n\n━━━━━━━━━━━━━━\n"
        + '📝 <a href="https://t.me/infomphl/5">Заявка в администрацию</a>'
    )
    await call.message.edit_text(text, reply_markup=back_keyboard(), disable_web_page_preview=True)


# =====================================================
# ЗАКРЫТАЯ ТАБЛИЦА КАПИТАНОВ И ДИСЦИПЛИНА
# =====================================================

def captain_discipline_text():
    with get_db() as db:
        rows = db.execute(
            "SELECT name, team, warnings, reprimands FROM captains ORDER BY team COLLATE NOCASE"
        ).fetchall()
    if not rows:
        return "📋 <b>ТАБЛИЦА КАПИТАНОВ MPHL</b>\n\nПока нет добавленных капитанов."
    lines = [
        "📋 <b>ТАБЛИЦА КАПИТАНОВ MPHL</b>",
        "<i>Закрытая информация для капитанов и администрации</i>",
        ""
    ]
    for row in rows:
        lines.append(f"🏒 <b>{escape(row['team'])}</b>")
        lines.append(f"👤 Капитан: {escape(row['name'])}")
        lines.append(f"⚠️ Предупреждения: <b>{int(row['warnings'] or 0)}/3</b>")
        lines.append(f"❗ Выговоры: <b>{int(row['reprimands'] or 0)}</b>")
        lines.append("━━━━━━━━━━━━━━")
    return "\n".join(lines)


@dp.callback_query(F.data == "captain_discipline_table")
async def captain_discipline_table(call: CallbackQuery):
    if not (is_admin(call.from_user.id, call.from_user.username) or is_captain(call.from_user.id)):
        await call.answer("Этот раздел доступен только капитанам и администрации.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(captain_discipline_text(), reply_markup=back_keyboard())


def captain_discipline_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❗ Выдать выговор капитану", callback_data="captain_action:reprimand_add")],
        [InlineKeyboardButton(text="⚠️ Выдать предупреждение капитану", callback_data="captain_action:warning_add")],
        [InlineKeyboardButton(text="➖ Снять предупреждение капитану", callback_data="captain_action:warning_remove")],
        [InlineKeyboardButton(text="➖ Снять выговор капитану", callback_data="captain_action:reprimand_remove")],
        [InlineKeyboardButton(text="📋 Открыть таблицу капитанов", callback_data="captain_discipline_table")],
        [InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]
    ])


@dp.callback_query(F.data.startswith("captain_action:"))
async def captain_discipline_choose_action(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    action = call.data.split(":", 1)[1]
    allowed = {"reprimand_add", "warning_add", "warning_remove", "reprimand_remove"}
    if action not in allowed:
        await call.answer("Неизвестное действие.", show_alert=True)
        return
    with get_db() as db:
        captains = db.execute("SELECT id, name, team FROM captains ORDER BY team COLLATE NOCASE").fetchall()
    if not captains:
        await call.answer("Список капитанов пуст.", show_alert=True)
        return
    labels = {
        "reprimand_add": "выдать выговор",
        "warning_add": "выдать предупреждение",
        "warning_remove": "снять предупреждение",
        "reprimand_remove": "снять выговор"
    }
    builder = InlineKeyboardBuilder()
    for captain in captains:
        builder.button(
            text=f"{captain['name']} — {captain['team']}",
            callback_data=f"captain_select:{action}:{captain['id']}"
        )
    builder.button(text="⬅️ Назад", callback_data="captain_discipline_admin")
    builder.adjust(1)
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        f"Выберите капитана, которому нужно {labels[action]}:",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data == "captain_discipline_admin")
async def captain_discipline_admin_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "⚖️ <b>ДИСЦИПЛИНА КАПИТАНОВ MPHL</b>\n\nВыберите действие:",
        reply_markup=captain_discipline_admin_keyboard()
    )


@dp.callback_query(F.data.startswith("captain_select:"))
async def captain_discipline_select(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    try:
        _, action, raw_id = call.data.split(":")
        captain_id = int(raw_id)
    except (ValueError, IndexError):
        await call.answer("Некорректный запрос.", show_alert=True)
        return
    with get_db() as db:
        captain = db.execute("SELECT name, team FROM captains WHERE id = ?", (captain_id,)).fetchone()
    if not captain:
        await call.answer("Капитан не найден.", show_alert=True)
        return
    await state.clear()
    await state.update_data(captain_action=action, captain_id=captain_id)
    await state.set_state(CaptainDisciplineForm.waiting_for_reason)
    await call.answer()
    await call.message.edit_text(
        f"Вы выбрали: <b>{escape(captain['name'])}</b> — <b>{escape(captain['team'])}</b>.\n\n"
        "Укажите причину действия (до 500 символов):",
        reply_markup=cancel_keyboard()
    )


@dp.message(CaptainDisciplineForm.waiting_for_reason)
async def captain_discipline_apply(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    reason = (message.text or "").strip()
    if not reason or len(reason) > 500:
        await message.answer("Введите причину от 1 до 500 символов.", reply_markup=cancel_keyboard())
        return
    data = await state.get_data()
    action = data.get("captain_action")
    captain_id = data.get("captain_id")
    action_names = {
        "warning_add": "Предупреждение выдано",
        "reprimand_add": "Выговор выдан",
        "warning_remove": "Предупреждение снято",
        "reprimand_remove": "Выговор снят"
    }
    if action not in action_names or not captain_id:
        await state.clear()
        await message.answer("Действие устарело. Откройте админ-панель заново.")
        return

    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    with get_db() as db:
        captain = db.execute("SELECT name, team, warnings, reprimands FROM captains WHERE id = ?", (captain_id,)).fetchone()
        if not captain:
            await state.clear()
            await message.answer("Капитан не найден.")
            return
        warnings = int(captain["warnings"] or 0)
        reprimands = int(captain["reprimands"] or 0)
        log_action = action

        if action == "warning_add":
            warnings += 1
            if warnings >= 3:
                warnings = 0
                reprimands += 1
                log_action = "automatic_reprimand"
                result = "⚠️ Третье предупреждение достигнуто: предупреждения обнулены, автоматически выдан 1 выговор."
            else:
                result = f"Предупреждение выдано. Сейчас предупреждений: {warnings}/3."
        elif action == "reprimand_add":
            reprimands += 1
            result = f"Выговор выдан. Всего выговоров: {reprimands}."
        elif action == "warning_remove":
            if warnings <= 0:
                await state.clear()
                await message.answer("У этого капитана нет предупреждений для снятия.")
                return
            warnings -= 1
            result = f"Одно предупреждение снято. Осталось: {warnings}."
        else:
            if reprimands <= 0:
                await state.clear()
                await message.answer("У этого капитана нет выговоров для снятия.")
                return
            reprimands -= 1
            result = f"Один выговор снят. Осталось: {reprimands}."

        db.execute(
            "UPDATE captains SET warnings = ?, reprimands = ? WHERE id = ?",
            (warnings, reprimands, captain_id)
        )
        db.execute("""
            INSERT INTO captain_discipline_log (captain_id, action, reason, admin_id, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (captain_id, log_action, reason, message.from_user.id, now))

    await state.clear()
    await message.answer(
        f"✅ <b>{action_names.get(action, 'Изменение выполнено')}</b>\n\n"
        f"🏒 Команда: <b>{escape(captain['team'])}</b>\n"
        f"👤 Капитан: <b>{escape(captain['name'])}</b>\n"
        f"⚠️ Предупреждения: <b>{warnings}/3</b>\n"
        f"❗ Выговоры: <b>{reprimands}</b>\n"
        f"📝 Причина: {escape(reason)}\n\n{result}",
        reply_markup=captain_discipline_admin_keyboard()
    )


# =====================================================
# ЗАКРЫТАЯ ТАБЛИЦА АДМИНИСТРАЦИИ И ДИСЦИПЛИНА
# =====================================================

def admin_discipline_text():
    with get_db() as db:
        rows = db.execute(
            "SELECT id, position, name, level, warnings, reprimands FROM admins "
            "WHERE position != 'Владелец лиги' ORDER BY "
            "CASE level WHEN 'Высшая администрация' THEN 1 WHEN 'Старшая администрация' THEN 2 ELSE 3 END, position COLLATE NOCASE"
        ).fetchall()
    lines = [
        "📋 <b>ТАБЛИЦА АДМИНИСТРАЦИИ MPHL</b>",
        "<i>Закрытая информация: только для администрации и владельца</i>",
        ""
    ]
    if not rows:
        return "\n".join(lines + ["Администраторы пока не добавлены."])
    for row in rows:
        lines.append(f"🛡 <b>{escape(row['position'])}</b>")
        lines.append(f"👤 {escape(row['name'])}")
        lines.append(f"🏷 Уровень: {escape(row['level'])}")
        lines.append(f"⚠️ Предупреждения: <b>{int(row['warnings'] or 0)}/3</b>")
        lines.append(f"❗ Выговоры: <b>{int(row['reprimands'] or 0)}</b>")
        lines.append("━━━━━━━━━━━━━━")
    return "\n".join(lines)


def admin_discipline_keyboard(owner: bool):
    rows = []
    if owner:
        rows.extend([
            [InlineKeyboardButton(text="❗ Выдать выговор администратору", callback_data="admin_staff_action:reprimand_add")],
            [InlineKeyboardButton(text="⚠️ Выдать предупреждение администратору", callback_data="admin_staff_action:warning_add")],
            [InlineKeyboardButton(text="➖ Снять предупреждение администратору", callback_data="admin_staff_action:warning_remove")],
            [InlineKeyboardButton(text="➖ Снять выговор администратору", callback_data="admin_staff_action:reprimand_remove")],
        ])
    rows.append([InlineKeyboardButton(text="📋 Обновить таблицу", callback_data="admin_staff_table")])
    rows.append([InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "admin_staff_table")
async def admin_staff_table(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Раздел доступен только администрации и владельцу.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        admin_discipline_text(),
        reply_markup=admin_discipline_keyboard(is_owner(call.from_user.id))
    )


@dp.callback_query(F.data.startswith("admin_staff_action:"))
async def admin_staff_choose_action(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Только владелец может изменять дисциплину администрации.", show_alert=True)
        return
    action = call.data.split(":", 1)[1]
    allowed = {"reprimand_add", "warning_add", "warning_remove", "reprimand_remove"}
    if action not in allowed:
        await call.answer("Неизвестное действие.", show_alert=True)
        return
    with get_db() as db:
        staff = db.execute(
            "SELECT id, name, position FROM admins WHERE position != 'Владелец лиги' ORDER BY position COLLATE NOCASE"
        ).fetchall()
    if not staff:
        await call.answer("В администрации пока никого нет.", show_alert=True)
        return
    labels = {
        "reprimand_add": "выдать выговор",
        "warning_add": "выдать предупреждение",
        "warning_remove": "снять предупреждение",
        "reprimand_remove": "снять выговор",
    }
    builder = InlineKeyboardBuilder()
    for person in staff:
        builder.button(
            text=f"{person['name']} — {person['position']}",
            callback_data=f"admin_staff_select:{action}:{person['id']}"
        )
    builder.button(text="⬅️ Назад", callback_data="admin_staff_table")
    builder.adjust(1)
    await state.clear()
    await call.answer()
    await call.message.edit_text(f"Выберите администратора, которому нужно {labels[action]}:", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("admin_staff_select:"))
async def admin_staff_select(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Только владелец может изменять дисциплину администрации.", show_alert=True)
        return
    try:
        _, action, raw_id = call.data.split(":")
        target_id = int(raw_id)
    except (ValueError, IndexError):
        await call.answer("Некорректный запрос.", show_alert=True)
        return
    if action not in {"reprimand_add", "warning_add", "warning_remove", "reprimand_remove"}:
        await call.answer("Некорректное действие.", show_alert=True)
        return
    with get_db() as db:
        person = db.execute(
            "SELECT name, position FROM admins WHERE id = ? AND position != 'Владелец лиги'", (target_id,)
        ).fetchone()
    if not person:
        await call.answer("Администратор не найден.", show_alert=True)
        return
    await state.clear()
    await state.update_data(admin_discipline_action=action, target_admin_id=target_id)
    await state.set_state(AdminDisciplineForm.waiting_for_reason)
    await call.answer()
    await call.message.edit_text(
        f"Выбран: <b>{escape(person['name'])}</b> — <b>{escape(person['position'])}</b>.\n\n"
        "Укажите причину действия (до 500 символов):",
        reply_markup=cancel_keyboard()
    )


@dp.message(AdminDisciplineForm.waiting_for_reason)
async def admin_staff_apply(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        await message.answer("Только владелец может изменять дисциплину администрации.")
        return
    reason = (message.text or "").strip()
    if not reason or len(reason) > 500:
        await message.answer("Введите причину от 1 до 500 символов.", reply_markup=cancel_keyboard())
        return
    data = await state.get_data()
    action = data.get("admin_discipline_action")
    target_id = data.get("target_admin_id")
    if action not in {"reprimand_add", "warning_add", "warning_remove", "reprimand_remove"} or not target_id:
        await state.clear()
        await message.answer("Действие устарело. Откройте раздел дисциплины администрации заново.")
        return
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    with get_db() as db:
        person = db.execute(
            "SELECT name, position, warnings, reprimands FROM admins WHERE id = ? AND position != 'Владелец лиги'",
            (target_id,)
        ).fetchone()
        if not person:
            await state.clear()
            await message.answer("Администратор не найден.")
            return
        warnings = int(person["warnings"] or 0)
        reprimands = int(person["reprimands"] or 0)
        log_action = action
        if action == "warning_add":
            warnings += 1
            if warnings >= 3:
                warnings = 0
                reprimands += 1
                log_action = "automatic_reprimand"
                result = "Третье предупреждение достигнуто: предупреждения обнулены, автоматически выдан 1 выговор."
            else:
                result = f"Предупреждение выдано. Сейчас предупреждений: {warnings}/3."
        elif action == "reprimand_add":
            reprimands += 1
            result = f"Выговор выдан. Всего выговоров: {reprimands}."
        elif action == "warning_remove":
            if warnings <= 0:
                await state.clear()
                await message.answer("У этого администратора нет предупреждений для снятия.")
                return
            warnings -= 1
            result = f"Одно предупреждение снято. Осталось: {warnings}."
        else:
            if reprimands <= 0:
                await state.clear()
                await message.answer("У этого администратора нет выговоров для снятия.")
                return
            reprimands -= 1
            result = f"Один выговор снят. Осталось: {reprimands}."
        db.execute("UPDATE admins SET warnings = ?, reprimands = ? WHERE id = ?", (warnings, reprimands, target_id))
        db.execute("""
            INSERT INTO admin_discipline_log
            (target_admin_id, target_name, target_position, action, reason, owner_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (target_id, person["name"], person["position"], log_action, reason, message.from_user.id, now))
    await state.clear()
    await message.answer(
        f"✅ <b>Дисциплина администрации обновлена</b>\n\n"
        f"👤 Администратор: <b>{escape(person['name'])}</b>\n"
        f"🛡 Должность: <b>{escape(person['position'])}</b>\n"
        f"⚠️ Предупреждения: <b>{warnings}/3</b>\n"
        f"❗ Выговоры: <b>{reprimands}</b>\n"
        f"📝 Причина: {escape(reason)}\n\n{escape(result)}",
        reply_markup=admin_discipline_keyboard(True)
    )


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
    builder.button(text="⚖️ Дисциплина капитанов", callback_data="captain_discipline_admin")
    builder.button(text="🛡 Дисциплина администрации", callback_data="admin_staff_table")
    builder.button(text="🎮 Добавить игру", callback_data="add_game")
    builder.button(text="🗑 Удалить игру", callback_data="delete_game")
    builder.button(text="📊 Добавить итоги", callback_data="add_result")
    builder.button(text="🗑 Удалить итоги", callback_data="delete_result")
    
    if is_owner(call.from_user.id):
        builder.button(text="⚙️ Изменение таблиц", callback_data="table_management")
        builder.button(text="🧹 Очистить таблицу", callback_data="clear_table")
        builder.button(text="👥 Управление администрацией", callback_data="manage_admins")
        builder.button(text="📋 История жалоб", callback_data="complaint_history")
        
    builder.button(text="⬅️ В главное меню", callback_data="home")
    builder.adjust(2)
    await call.answer()
    await call.message.edit_text(
        "⚙️ <b>Панель администрации MPHL</b>\n\nВыберите действие:",
        reply_markup=builder.as_markup()
    )


# =====================================================
# ОЧИСТИТЬ ТАБЛИЦУ (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА)
# =====================================================


# =====================================================
# ИЗМЕНЕНИЕ ТАБЛИЦ (ТОЛЬКО ВЛАДЕЛЕЦ)
# =====================================================

TABLE_FIELDS = {
    "games": ("games", "🎮 Всего игр"),
    "wins": ("wins", "🏆 Победы"),
    "losses": ("losses", "❌ Поражения"),
    "points": ("points", "⭐ Очки"),
}


def table_management_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить команду", callback_data="table_add_team")],
        [InlineKeyboardButton(text="➖ Убрать команду", callback_data="table_remove_team")],
        [InlineKeyboardButton(text="✏️ Изменить статистику команды", callback_data="table_edit_team")],
        [InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")],
    ])


def get_table_team_names():
    with get_db() as db:
        rows = db.execute("SELECT team_name FROM team_stats ORDER BY team_name COLLATE NOCASE").fetchall()
    return [row["team_name"] for row in rows]


@dp.callback_query(F.data == "table_management")
async def table_management(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        "⚙️ <b>ИЗМЕНЕНИЕ ТАБЛИЦ MPHL</b>\n\n"
        "Здесь можно добавлять и удалять команды, а также вручную менять игры, победы, поражения и очки.\n"
        "Изменения сохраняются в базе данных.",
        reply_markup=table_management_keyboard()
    )


@dp.callback_query(F.data == "table_add_team")
async def table_add_team_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await state.set_state(TableEditForm.waiting_for_new_team)
    await call.answer()
    await call.message.edit_text(
        "➕ <b>Добавление команды</b>\n\nВведите название новой команды:",
        reply_markup=cancel_keyboard()
    )


@dp.message(TableEditForm.waiting_for_new_team)
async def table_add_team_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name or len(name) > 60:
        await message.answer("❌ Название должно содержать от 1 до 60 символов.", reply_markup=cancel_keyboard())
        return
    with get_db() as db:
        existing = db.execute(
            "SELECT 1 FROM team_stats WHERE team_name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if existing:
            await message.answer("❌ Такая команда уже есть в таблице. Введите другое название.", reply_markup=cancel_keyboard())
            return
        db.execute("INSERT INTO team_stats (team_name, games, wins, losses, points) VALUES (?, 0, 0, 0, 0)", (name,))
    await state.clear()
    await message.answer(
        f"✅ Команда <b>{escape(name)}</b> добавлена в таблицу с нулевой статистикой.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ К изменению таблиц", callback_data="table_management")],
            [InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]
        ])
    )


@dp.callback_query(F.data == "table_remove_team")
async def table_remove_team_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    names = get_table_team_names()
    if not names:
        await call.answer("В таблице пока нет команд.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for index, name in enumerate(names):
        builder.button(text=f"➖ {name}", callback_data=f"table_remove_select:{index}")
    builder.button(text="⬅️ Назад", callback_data="table_management")
    builder.adjust(1)
    await call.answer()
    await call.message.edit_text("➖ <b>Выберите команду для удаления из таблицы:</b>", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("table_remove_select:"))
async def table_remove_select(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    try:
        index = int(call.data.split(":", 1)[1])
        names = get_table_team_names()
        name = names[index]
    except (ValueError, IndexError):
        await call.answer("Команда не найдена. Откройте список заново.", show_alert=True)
        return
    await state.update_data(table_team_to_remove=name)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data="table_remove_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="table_management")]
    ])
    await call.answer()
    await call.message.edit_text(
        f"⚠️ Удалить <b>{escape(name)}</b> из текущей таблицы?\n\n"
        "Будет удалена её строка со статистикой. История матчей в разделе «Итоги игр» останется.",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "table_remove_confirm")
async def table_remove_confirm(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    data = await state.get_data()
    name = data.get("table_team_to_remove")
    if not name:
        await call.answer("Не удалось определить команду.", show_alert=True)
        return
    with get_db() as db:
        cursor = db.execute("DELETE FROM team_stats WHERE team_name = ?", (name,))
    await state.clear()
    if cursor.rowcount:
        answer = f"✅ Команда <b>{escape(name)}</b> удалена из таблицы."
    else:
        answer = "Команда уже отсутствует в таблице."
    await call.answer("Готово.")
    await call.message.edit_text(answer, reply_markup=table_management_keyboard())


@dp.callback_query(F.data == "table_edit_team")
async def table_edit_team_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    names = get_table_team_names()
    if not names:
        await call.answer("Сначала добавьте команду в таблицу.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for index, name in enumerate(names):
        builder.button(text=name, callback_data=f"table_edit_select:{index}")
    builder.button(text="⬅️ Назад", callback_data="table_management")
    builder.adjust(1)
    await call.answer()
    await call.message.edit_text("✏️ <b>Выберите команду для изменения статистики:</b>", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("table_edit_select:"))
async def table_edit_select(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    try:
        index = int(call.data.split(":", 1)[1])
        names = get_table_team_names()
        name = names[index]
    except (ValueError, IndexError):
        await call.answer("Команда не найдена. Откройте список заново.", show_alert=True)
        return
    await state.update_data(table_team_to_edit=name)
    builder = InlineKeyboardBuilder()
    for key, (_, label) in TABLE_FIELDS.items():
        builder.button(text=label, callback_data=f"table_edit_field:{key}")
    builder.button(text="⬅️ К списку команд", callback_data="table_edit_team")
    builder.adjust(2)
    await call.answer()
    await call.message.edit_text(
        f"✏️ <b>Редактирование: {escape(name)}</b>\n\nЧто хотите изменить?",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data.startswith("table_edit_field:"))
async def table_edit_field(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    field = call.data.split(":", 1)[1]
    if field not in TABLE_FIELDS:
        await call.answer("Неизвестный параметр.", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("table_team_to_edit"):
        await call.answer("Сначала выберите команду.", show_alert=True)
        return
    await state.update_data(table_field=field)
    await state.set_state(TableEditForm.waiting_for_value)
    await call.answer()
    await call.message.edit_text(
        f"Введите новое значение для параметра «{TABLE_FIELDS[field][1]}» команды "
        f"<b>{escape(data['table_team_to_edit'])}</b>.\n\n"
        "Нужно указать целое неотрицательное число:",
        reply_markup=cancel_keyboard()
    )


@dp.message(TableEditForm.waiting_for_value)
async def table_edit_value_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    raw_value = (message.text or "").strip()
    if not re.fullmatch(r"\d{1,7}", raw_value):
        await message.answer("❌ Введите целое число от 0 до 9 999 999.", reply_markup=cancel_keyboard())
        return
    value = int(raw_value)
    data = await state.get_data()
    team = data.get("table_team_to_edit")
    field = data.get("table_field")
    if not team or field not in TABLE_FIELDS:
        await state.clear()
        await message.answer("❌ Данные редактирования устарели. Начните заново.")
        return
    column = TABLE_FIELDS[field][0]
    with get_db() as db:
        cursor = db.execute(f"UPDATE team_stats SET {column} = ? WHERE team_name = ?", (value, team))
    await state.clear()
    if cursor.rowcount:
        result = (
            f"✅ <b>Статистика обновлена</b>\n\n"
            f"🏒 Команда: <b>{escape(team)}</b>\n"
            f"{TABLE_FIELDS[field][1]}: <b>{value}</b>"
        )
    else:
        result = "❌ Команда не найдена в таблице."
    await message.answer(
        result,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить ещё", callback_data="table_edit_team")],
            [InlineKeyboardButton(text="⚙️ К изменению таблиц", callback_data="table_management")],
            [InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]
        ])
    )


@dp.callback_query(F.data == "clear_table")
async def clear_table_start(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    
    await call.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, очистить всё", callback_data="clear_table_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")
        ]
    ])
    await call.message.edit_text(
        "⚠️ <b>Внимание!</b>\n\n"
        "Вы собираетесь <b>полностью очистить</b> таблицу итогов и статистики топ-команд.\n"
        "Это действие <b>нельзя отменить</b>.\n\n"
        "Вы уверены?",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "clear_table_confirm")
async def clear_table_confirm(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    
    with get_db() as db:
        db.execute("DELETE FROM results")
        db.execute("DELETE FROM team_stats")
    
    await call.answer("Таблицы очищены.")
    await call.message.edit_text(
        "✅ <b>Таблицы успешно очищены!</b>\n\n"
        "Все итоги и статистика топ-команд удалены.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]
        ])
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
        ""
    , reply_markup=cancel_keyboard())

@dp.message(AddGameForm.waiting_for_match)
async def add_game_match(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    match = (message.text or "").strip()
    if not match or len(match) > 100:
        await message.answer("❌ Введите корректное название матча (до 100 символов).", reply_markup=cancel_keyboard())
        return
    await state.update_data(game_match=match)
    await state.set_state(AddGameForm.waiting_for_datetime)
    await message.answer("Шаг 2. Введите дату и время игры (например: 12.11.2026 16:00):", reply_markup=cancel_keyboard())

@dp.message(AddGameForm.waiting_for_datetime)
async def add_game_datetime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    dt = (message.text or "").strip()
    if not dt or len(dt) > 50:
        await message.answer("❌ Введите корректную дату и время.", reply_markup=cancel_keyboard())
        return
    await state.update_data(game_datetime=dt)
    await state.set_state(AddGameForm.waiting_for_stream)
    await message.answer("Шаг 3. Отправьте ссылку на стрим (если есть). Если стрима не будет, напишите «Нет»:", reply_markup=cancel_keyboard())

@dp.message(AddGameForm.waiting_for_stream)
async def add_game_stream(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    stream = (message.text or "").strip()
    if stream.lower() in {"нет", "no", "без стрима"}:
        stream = None
    elif not stream.startswith("http"):
        await message.answer("❌ Некорректная ссылка. Отправьте ссылку (начинается с http) или напишите «Нет».", reply_markup=cancel_keyboard())
        return

    data = await state.get_data()
    with get_db() as db:
        db.execute("""
            INSERT INTO games (match_name, date_time, stream_link, created_at)
            VALUES (?, ?, ?, ?)
        """, (data["game_match"], data["game_datetime"], stream, datetime.now().strftime("%d.%m.%Y %H:%M")))

    await state.clear()
    await message.answer("✅ Игра добавлена.", reply_markup=cancel_keyboard())
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
        ""
    , reply_markup=cancel_keyboard())

@dp.message(DeleteGameForm.waiting_for_match)
async def delete_game_match(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    match = (message.text or "").strip()
    with get_db() as db:
        cursor = db.execute("DELETE FROM games WHERE match_name = ?", (match,))
    
    if cursor.rowcount == 0:
        await message.answer("❌ Игра с таким названием не найдена.", reply_markup=cancel_keyboard())
    else:
        await message.answer("✅ Игра удалена.", reply_markup=cancel_keyboard())
    
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
        ""
    , reply_markup=cancel_keyboard())

@dp.message(AddResultForm.waiting_for_datetime)
async def add_result_datetime(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    dt = (message.text or "").strip()
    if not dt or len(dt) > 50:
        await message.answer("❌ Введите корректную дату и время.", reply_markup=cancel_keyboard())
        return
    await state.update_data(result_datetime=dt)
    await state.set_state(AddResultForm.waiting_for_match)
    await message.answer("Шаг 2. Введите название матча (например: Барыс vs Адмирал):", reply_markup=cancel_keyboard())

@dp.message(AddResultForm.waiting_for_match)
async def add_result_match(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    match = (message.text or "").strip()
    if not match or len(match) > 100:
        await message.answer("❌ Введите корректное название матча.", reply_markup=cancel_keyboard())
        return
    await state.update_data(result_match=match)
    await state.set_state(AddResultForm.waiting_for_score)
    await message.answer("Шаг 3. Введите счет (например: 3:0):", reply_markup=cancel_keyboard())

@dp.message(AddResultForm.waiting_for_score)
async def add_result_score(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    score = (message.text or "").strip()
    if not score or len(score) > 20:
        await message.answer("❌ Введите корректный счет.", reply_markup=cancel_keyboard())
        return
    await state.update_data(result_score=score)
    await state.set_state(AddResultForm.waiting_for_winner)
    await message.answer("Шаг 4. Введите название победившей команды (например: Барыс):", reply_markup=cancel_keyboard())

@dp.message(AddResultForm.waiting_for_winner)
async def add_result_winner(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    winner = (message.text or "").strip()
    if not winner or len(winner) > 100:
        await message.answer("❌ Введите корректное название команды.", reply_markup=cancel_keyboard())
        return

    data = await state.get_data()
    teams = re.split(r"\s+vs\.?\s+", data.get("result_match", ""), flags=re.IGNORECASE)
    if len(teams) != 2 or winner.casefold() not in [team.strip().casefold() for team in teams]:
        await message.answer(
            "❌ Победитель должен совпадать с одной из команд в названии матча.\n"
            "Введите название победившей команды ещё раз:",
            reply_markup=cancel_keyboard(),
        )
        return

    await state.update_data(result_winner=winner)
    await state.set_state(AddResultForm.waiting_for_overtime)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Победа в основное время", callback_data="result_overtime:0")],
        [InlineKeyboardButton(text="🏒 Победа в овертайме", callback_data="result_overtime:1")],
    ])
    await message.answer(
        "Шаг 5. <b>Как была одержана победа?</b>\n\n"
        "⏱ Основное время: победитель — 1 очко, проигравший — 0.\n"
        "🏒 Овертайм: победитель — 2 очка, проигравший — 1.",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("result_overtime:"))
async def finalize_result_overtime(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id, call.from_user.username):
        await call.answer("Нет доступа.", show_alert=True)
        return
    if await state.get_state() != AddResultForm.waiting_for_overtime.state:
        await call.answer("Этот этап уже завершён или устарел.", show_alert=True)
        return

    overtime = call.data.endswith(":1")
    data = await state.get_data()
    winner = data.get("result_winner", "")
    if not all(key in data for key in ("result_match", "result_datetime", "result_score")) or not winner:
        await state.clear()
        await call.answer("Не удалось получить данные матча. Начните добавление итогов заново.", show_alert=True)
        return

    with get_db() as db:
        db.execute("""
            INSERT INTO results (match_name, date_time, score, winner, overtime, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            data["result_match"], data["result_datetime"], data["result_score"], winner,
            int(overtime), datetime.now().strftime("%d.%m.%Y %H:%M")
        ))

    update_stats(data["result_match"], winner, overtime)
    await state.clear()
    await call.answer("Итоги сохранены!")
    await call.message.edit_text(
        "✅ <b>Итоги добавлены!</b>\n\n"
        f"🏒 {escape(data['result_match'])}\n"
        f"📊 Счёт: {escape(data['result_score'])}\n"
        f"🏆 Победитель: {escape(winner)}\n"
        f"{'Овертайм' if overtime else 'Основное время'}\n\n"
        f"Начисление очков: {'2 победителю / 1 проигравшему' if overtime else '1 победителю / 0 проигравшему'}.",
    )
    await call.message.answer(
        "⚙️ Админ-панель:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin_panel")]])
    )


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
        ""
    , reply_markup=cancel_keyboard())

@dp.message(DeleteResultForm.waiting_for_match)
async def delete_result_match(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    match = (message.text or "").strip()
    with get_db() as db:
        cursor = db.execute("DELETE FROM results WHERE match_name = ?", (match,))
    
    if cursor.rowcount == 0:
        await message.answer("❌ Итоги с таким названием не найдены.", reply_markup=cancel_keyboard())
    else:
        await message.answer("✅ Итоги удалены. Статистика в топ-командах осталась без изменений.", reply_markup=cancel_keyboard())
    
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
    if count >= 8:
        await call.answer("Все восемь мест капитанов уже заняты.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AddCaptainForm.waiting_for_name)
    await call.answer()
    await call.message.answer(
        "➕ <b>Добавление капитана</b>\n\n"
        "1. Введите никнейм и номер капитана.\n\n"
        "Пример: <code>h1zEr #99</code>"
    , reply_markup=cancel_keyboard())

@dp.message(AddCaptainForm.waiting_for_name)
async def add_captain_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name or len(name) > 100:
        await message.answer("❌ Введите корректный никнейм (до 100 символов).", reply_markup=cancel_keyboard())
        return
    await state.update_data(captain_name=name)
    await state.set_state(AddCaptainForm.waiting_for_team)
    await message.answer("2. Введите название команды.\n\nПример: <code>Адмирал</code>", reply_markup=cancel_keyboard())

@dp.message(AddCaptainForm.waiting_for_team)
async def add_captain_team(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    team = (message.text or "").strip()
    if not team or len(team) > 100:
        await message.answer("❌ Введите корректное название команды.", reply_markup=cancel_keyboard())
        return
    await state.update_data(captain_team=team)
    await state.set_state(AddCaptainForm.waiting_for_telegram)
    await message.answer("3. Отправьте ссылку на Telegram капитана.\n\nПример: <code>https://t.me/h1zEr54</code>", reply_markup=cancel_keyboard())

@dp.message(AddCaptainForm.waiting_for_telegram)
async def add_captain_telegram(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    telegram = (message.text or "").strip()
    if not valid_telegram_profile_link(telegram):
        await message.answer("❌ Некорректная ссылка.\nИспользуйте формат https://t.me/username", reply_markup=cancel_keyboard())
        return
    data = await state.get_data()
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) FROM captains").fetchone()[0]
        if count >= 8:
            await message.answer("❌ Все восемь мест капитанов уже заняты.", reply_markup=cancel_keyboard())
            await state.clear()
            return
        exists = db.execute("SELECT id FROM captains WHERE team = ?", (data["captain_team"],)).fetchone()
        if exists:
            await message.answer("❌ Эта команда уже добавлена. Введите другое название команды.", reply_markup=cancel_keyboard())
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
    , reply_markup=cancel_keyboard())
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
    builder.adjust(2)
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
        db.execute("DELETE FROM captain_discipline_log WHERE captain_id = ?", (captain_id,))
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
        ""
    , reply_markup=cancel_keyboard())

@dp.message(UnbanForm.waiting_for_steamid)
async def unban_steamid(message: Message, state: FSMContext):
    steam_id = (message.text or "").strip()
    if not re.fullmatch(r"765\d{14}", steam_id):
        await message.answer("❌ Неверный формат SteamID64. Он должен начинаться с 765 и содержать 17 цифр.", reply_markup=cancel_keyboard())
        return
    await state.update_data(unban_steamid=steam_id)
    await state.set_state(UnbanForm.waiting_for_reason)
    await message.answer("Шаг 2. Опишите суть бана (за что забанили):", reply_markup=cancel_keyboard())

@dp.message(UnbanForm.waiting_for_reason)
async def unban_reason(message: Message, state: FSMContext):
    reason = (message.text or "").strip()
    if not reason or len(reason) > 1000:
        await message.answer("❌ Опишите причину (до 1000 символов).", reply_markup=cancel_keyboard())
        return
    await state.update_data(unban_reason=reason)
    await state.set_state(UnbanForm.waiting_for_date)
    await message.answer("Шаг 3. Введите дату выдачи бана (например, 12.11.2026):", reply_markup=cancel_keyboard())

@dp.message(UnbanForm.waiting_for_date)
async def unban_date(message: Message, state: FSMContext):
    ban_date = (message.text or "").strip()
    if not ban_date or len(ban_date) > 50:
        await message.answer("❌ Введите корректную дату.", reply_markup=cancel_keyboard())
        return
    await state.update_data(unban_date=ban_date)
    await state.set_state(UnbanForm.waiting_for_media)
    await message.answer(
        "Шаг 4. Прикрепите доказательства (фото/видео бана), если есть.\n"
        "Когда закончите, напишите «Готово». Если доказательств нет — «Пропустить»."
    , reply_markup=cancel_keyboard())

@dp.message(UnbanForm.waiting_for_media)
async def unban_media(message: Message, state: FSMContext):
    data = await state.get_data()
    media_items = list(data.get("unban_media_items", []))
    
    command = (message.text or "").strip().lower()
    if command in {"готово", "гатово", "done", "завершить"}:
        if not media_items:
            await message.answer("Вы не прикрепили файлы. Напишите «Пропустить», если их нет.", reply_markup=cancel_keyboard())
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
        await message.answer(f"📎 Файл добавлен ({count}). Можете отправить ещё или написать «Готово».", reply_markup=cancel_keyboard())
        return

    await message.answer("Прикрепите фото/видео/документ. Когда закончите, напишите «Готово».", reply_markup=cancel_keyboard())

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
    , reply_markup=cancel_keyboard())
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
        ""
    , reply_markup=cancel_keyboard())

@dp.message(ComplaintForm.waiting_for_nickname)
async def complaint_nickname(message: Message, state: FSMContext):
    nickname = (message.text or "").strip()
    if not nickname or len(nickname) > 100:
        await message.answer("❌ Введите корректный никнейм (до 100 символов).", reply_markup=cancel_keyboard())
        return
    await state.update_data(complaint_nickname=nickname)
    await state.set_state(ComplaintForm.waiting_for_admin_nickname)
    await message.answer("Шаг 2. Введите никнейм администратора, на которого жалуетесь:", reply_markup=cancel_keyboard())

@dp.message(ComplaintForm.waiting_for_admin_nickname)
async def complaint_admin_nickname(message: Message, state: FSMContext):
    admin_nickname = (message.text or "").strip()
    if not admin_nickname or len(admin_nickname) > 100:
        await message.answer("❌ Введите корректный никнейм администратора.", reply_markup=cancel_keyboard())
        return
    await state.update_data(complaint_admin_nickname=admin_nickname)
    await state.set_state(ComplaintForm.waiting_for_violation)
    await message.answer("Шаг 3. Опишите, что нарушил администратор (суть проблемы):", reply_markup=cancel_keyboard())

@dp.message(ComplaintForm.waiting_for_violation)
async def complaint_violation(message: Message, state: FSMContext):
    violation = (message.text or "").strip()
    if not violation or len(violation) > 3500:
        await message.answer("❌ Опишите нарушение (до 3500 символов).", reply_markup=cancel_keyboard())
        return
    await state.update_data(complaint_violation=violation)
    await state.set_state(ComplaintForm.waiting_for_proof)
    await message.answer(
        "Шаг 4. Прикрепите доказательства (фото/видео/документы).\n"
        "Можно прикрепить до 4-х штук. Когда закончите, напишите «Готово». Если доказательств нет — «Пропустить»."
    , reply_markup=cancel_keyboard())

@dp.message(ComplaintForm.waiting_for_proof)
async def complaint_proof(message: Message, state: FSMContext):
    data = await state.get_data()
    media_items = list(data.get("complaint_media_items", []))
    media_refs = list(data.get("complaint_media_refs", []))

    command = (message.text or "").strip().lower()
    if command in {"готово", "гатово", "done", "завершить"}:
        if not media_items:
            await message.answer("Вы ещё не прикрепили доказательства. Отправьте фото или напишите «Пропустить».", reply_markup=cancel_keyboard())
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
                await message.answer("❌ К сожалению, я не принимаю больше 4-х фотографий за раз.", reply_markup=cancel_keyboard())
                return

            media_items.append((media_type, media_file_id))
            media_refs.append((message.chat.id, message.message_id))
            await state.update_data(complaint_media_items=media_items, complaint_media_refs=media_refs)
            count = len(media_items)
        await message.answer(f"📎 Доказательство добавлено ({count}/4). Можете отправить ещё или написать «Готово».", reply_markup=cancel_keyboard())
        return

    await message.answer("Прикрепите фото/видео/документ. Когда закончите, напишите «Готово».", reply_markup=cancel_keyboard())


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
    , reply_markup=cancel_keyboard())
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
        await call.message.answer(f"✅ Жалоба №{complaint_id} {result}.", reply_markup=cancel_keyboard())
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
        ""
    , reply_markup=cancel_keyboard())

@dp.message(SuggestionForm.waiting_for_nickname)
async def suggestion_nickname(message: Message, state: FSMContext):
    nickname = (message.text or "").strip()
    if not nickname or len(nickname) > 100:
        await message.answer("❌ Введите корректный никнейм (до 100 символов).", reply_markup=cancel_keyboard())
        return
    await state.update_data(suggestion_nickname=nickname)
    await state.set_state(SuggestionForm.waiting_for_text)
    await message.answer("Шаг 2. Опишите суть вашего предложения (до 3500 символов):", reply_markup=cancel_keyboard())

@dp.message(SuggestionForm.waiting_for_text)
async def suggestion_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text or len(text) > 3500:
        await message.answer("❌ Отправьте текст предложения (до 3500 символов).", reply_markup=cancel_keyboard())
        return
    await state.update_data(suggestion_text=text)
    await state.set_state(SuggestionForm.waiting_for_media)
    await message.answer(
        "Шаг 3. Прикрепите фото/видео/документы (если есть).\n"
        "Когда закончите, напишите «Готово». Если вложений нет — «Пропустить»."
    , reply_markup=cancel_keyboard())

@dp.message(SuggestionForm.waiting_for_media)
async def suggestion_media(message: Message, state: FSMContext):
    data = await state.get_data()
    media_items = list(data.get("suggestion_media_items", []))
    media_refs = list(data.get("suggestion_media_refs", []))

    command = (message.text or "").strip().lower()
    if command in {"готово", "гатово", "done", "завершить"}:
        if not media_items:
            await message.answer("Вы ещё не прикрепили файлы. Отправьте фото/видео или напишите «Пропустить».", reply_markup=cancel_keyboard())
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
        await message.answer(f"📎 Файл добавлен ({count}). Можете отправить ещё или написать «Готово».", reply_markup=cancel_keyboard())
        return

    await message.answer("Прикрепите фото, видео или документ. Когда закончите, напишите «Готово».", reply_markup=cancel_keyboard())


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
    await message.answer(result, reply_markup=cancel_keyboard())
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
    await call.message.answer(f"✉️ Напишите ответ на предложение №{suggestion_id}.\nОтвет отправится автору заявки.", reply_markup=cancel_keyboard())

@dp.message(SuggestionReplyForm.waiting_for_reply)
async def suggestion_reply_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await state.clear()
        return
    reply_text = (message.text or message.caption or "").strip()
    if not reply_text and not (message.photo or message.video or message.document):
        await message.answer("Отправьте текст или медиафайл с подписью.", reply_markup=cancel_keyboard())
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
        await message.answer("❌ Не удалось отправить ответ. Возможно, пользователь заблокировал бота.", reply_markup=cancel_keyboard())
        await state.clear()
        return
    with get_db() as db:
        db.execute("UPDATE suggestions SET status = 'answered' WHERE id = ?", (suggestion_id,))
    await state.clear()
    await message.answer(f"✅ Ответ на заявку №{suggestion_id} отправлен.", reply_markup=cancel_keyboard())


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
    builder.adjust(2)
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
    builder.adjust(2)
    
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
    builder.adjust(2)
    
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
    await call.message.answer("Шаг 3. Введите Никнейм + номер (например: h1zEr #99):", reply_markup=cancel_keyboard())


@dp.message(AddAdminForm.waiting_for_name)
async def add_admin_name(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name or len(name) > 100:
        await message.answer("❌ Введите корректный никнейм (до 100 символов).", reply_markup=cancel_keyboard())
        return
    await state.update_data(name=name)
    await state.set_state(AddAdminForm.waiting_for_link)
    await message.answer("Шаг 4. Отправьте ссылку Telegram в формате:\n<code>https://t.me/username</code>", reply_markup=cancel_keyboard())


@dp.message(AddAdminForm.waiting_for_link)
async def add_admin_link(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    link = (message.text or "").strip()
    if not valid_telegram_link(link):
        await message.answer("❌ Некорректная ссылка. Используйте https://t.me/username", reply_markup=cancel_keyboard())
        return
    
    data = await state.get_data()
    
    with get_db() as db:
        db.execute("DELETE FROM admins WHERE position = ?", (data["position"],))
        db.execute(
            "INSERT INTO admins (position, name, telegram, level) VALUES (?, ?, ?, ?)",
            (data["position"], data["name"], link, data["level"])
        )
    
    await state.clear()
    await message.answer("✅ Администратор добавлен.", reply_markup=cancel_keyboard())
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
    builder.adjust(2)
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
