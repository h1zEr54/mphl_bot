
import asyncio
import logging
import os
import sqlite3

from contextlib import contextmanager
from html import escape
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

# =====================================================
# 1. НАСТРОЙКИ
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

DB_NAME = "mphl_bot.db"

if not BOT_TOKEN:
    raise ValueError("ОШИБКА: Не задан BOT_TOKEN!")

if OWNER_ID <= 0:
    raise ValueError("ОШИБКА: Не задан OWNER_ID!")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =====================================================
# 2. ТЕКСТЫ И НАЗВАНИЯ КНОПОК
# =====================================================

WELCOME_TEXT = (
    "🏒 <b>MPHL — ОФИЦИАЛЬНЫЙ БОТ ЛИГИ</b>\n\n"
    "Приветствуем тебя!\n\n"
    "Этот бот создан для лиги MPHL в игре Puck.\n\n"
    "📢 Наш Telegram: @MPHL_Puck\n"
    "💬 Наш Discord: В разработке\n\n"
    "Выбери нужный раздел:"
)

BTN_CAPTAINS = "Капитаны и их команды"
BTN_ADMINS = "Состав администрации"
BTN_UNBAN = "Разбан"
BTN_COMPLAINTS = "Жалобы на администрацию"
BTN_GAMES = "Игры"
BTN_RESULTS = "Итоги игр"
BTN_ADD_ADMIN = "Добавить администратора"
BTN_REMOVE_ADMIN = "Удалить администратора"

ADMIN_POSITIONS = [
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
# 3. СОСТОЯНИЯ FSM
# =====================================================

class ComplaintForm(StatesGroup):
    waiting_for_complaint = State()


class AddAdminForm(StatesGroup):
    waiting_nickname = State()
    waiting_position = State()
    waiting_level = State()
    waiting_telegram = State()


class RemoveAdminForm(StatesGroup):
    waiting_position = State()


# =====================================================
# 4. БАЗА ДАННЫХ SQLITE
# =====================================================

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row

    try:
        yield conn
        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def init_db():
    with get_db() as conn:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                position TEXT PRIMARY KEY,
                nickname TEXT NOT NULL DEFAULT '',
                telegram_link TEXT NOT NULL DEFAULT '',
                level TEXT NOT NULL DEFAULT ''
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_at DATETIME
            )
        """)

        initial_admins = {
            "Владелец лиги": (
                "h1zEr #99",
                "https://t.me/h1zEr54",
                "Высшая администрация"
            ),
            "Главный администратор": (
                "", "", "Высшая администрация"
            ),
            "Главный судья": (
                "", "", "Высшая администрация"
            ),
            "Зам.главного администратора": (
                "", "", "Старшая администрация"
            ),
            "Зам.главного судьи": (
                "", "", "Старшая администрация"
            ),
            "Технический специалист": (
                "", "", "Старшая администрация"
            ),
            "Специалист по соц сетям": (
                "", "", "Старшая администрация"
            ),
            "Судья 1": (
                "", "", "Администрация"
            ),
            "Судья 2": (
                "", "", "Администрация"
            ),
            "Судья 3": (
                "", "", "Администрация"
            ),
        }

        for position, values in initial_admins.items():
            conn.execute("""
                INSERT OR IGNORE INTO admins
                (position, nickname, telegram_link, level)
                VALUES (?, ?, ?, ?)
            """, (position, *values))

    logger.info("База данных MPHL успешно инициализирована.")


# ---------------- АДМИНИСТРАТОРЫ ----------------

def get_admins():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM admins ORDER BY rowid"
        ).fetchall()


def get_admin(position):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM admins WHERE position = ?",
            (position,)
        ).fetchone()


def save_admin(position, nickname, telegram_link, level):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO admins
            (position, nickname, telegram_link, level)
            VALUES (?, ?, ?, ?)

            ON CONFLICT(position) DO UPDATE SET
                nickname = excluded.nickname,
                telegram_link = excluded.telegram_link,
                level = excluded.level
        """, (
            position,
            nickname,
            telegram_link,
            level
        ))


def remove_admin(position):
    with get_db() as conn:
        conn.execute("""
            UPDATE admins
            SET nickname = '',
                telegram_link = '',
                level = ''
            WHERE position = ?
        """, (position,))


# ---------------- ЖАЛОБЫ ----------------

def create_complaint(user_id, username, text):
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO complaints (user_id, username, text)
            VALUES (?, ?, ?)
        """, (user_id, username, text))

        return cursor.lastrowid


def delete_complaint(complaint_id):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM complaints WHERE id = ?",
            (complaint_id,)
        )


def get_complaint(complaint_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM complaints WHERE id = ?",
            (complaint_id,)
        ).fetchone()


def review_complaint(complaint_id, status):
    with get_db() as conn:
        cursor = conn.execute("""
            UPDATE complaints
            SET status = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
        """, (status, complaint_id))

        if cursor.rowcount == 0:
            return None

        return conn.execute(
            "SELECT * FROM complaints WHERE id = ?",
            (complaint_id,)
        ).fetchone()


def get_complaint_history(limit=10):
    with get_db() as conn:
        return conn.execute("""
            SELECT *
            FROM complaints
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()


# =====================================================
# 5. КЛАВИАТУРЫ
# =====================================================

def main_keyboard(user_id):
    buttons = [
        [
            InlineKeyboardButton(
                text="👥 Капитаны и команды",
                callback_data="menu:captains"
            ),
            InlineKeyboardButton(
                text="🛡️ Администрация",
                callback_data="menu:admins"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔓 Разбан",
                callback_data="menu:unban"
            ),
            InlineKeyboardButton(
                text="⚠️ Жалобы",
                callback_data="menu:complaints"
            )
        ],
        [
            InlineKeyboardButton(
                text="🏒 Игры",
                callback_data="menu:games"
            ),
            InlineKeyboardButton(
                text="🏆 Итоги игр",
                callback_data="menu:results"
            )
        ]
    ]

    if user_id == OWNER_ID:
        buttons.extend([
            [
                InlineKeyboardButton(
                    text="➕ Добавить администратора",
                    callback_data="menu:add_admin"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑️ Удалить администратора",
                    callback_data="menu:remove_admin"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 История жалоб",
                    callback_data="menu:history"
                )
            ]
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="menu:home"
                )
            ]
        ]
    )


# =====================================================
# 6. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================

def is_owner(user_id):
    return user_id == OWNER_ID


def valid_telegram_link(link):
    try:
        parsed = urlparse(link)

        return (
            parsed.scheme == "https"
            and parsed.netloc.lower() in ("t.me", "www.t.me")
            and bool(parsed.path.strip("/"))
            and not parsed.query
            and not parsed.fragment
        )

    except Exception:
        return False


async def cancel_state(message, state):
    await state.clear()

    await message.answer(
        "❌ Действие отменено.\n\n"
        "🏒 Возвращаемся в главное меню.",
        reply_markup=main_keyboard(message.from_user.id)
    )


# =====================================================
# 7. КОМАНДА START И CANCEL
# =====================================================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_keyboard(message.from_user.id)
    )


@dp.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    await cancel_state(message, state)


# =====================================================
# 8. ГЛАВНОЕ INLINE-МЕНЮ
# =====================================================

@dp.callback_query(F.data.startswith("menu:"))
async def menu_handler(call: CallbackQuery, state: FSMContext):

    if not call.message or not call.data:
        await call.answer()
        return

    action = call.data.split(":", 1)[1]

    # Защита административных разделов.
    owner_actions = (
        "add_admin",
        "remove_admin",
        "history"
    )

    if action in owner_actions and not is_owner(call.from_user.id):
        await call.answer(
            "⛔ У вас нет доступа к этому разделу.",
            show_alert=True
        )
        return

    await call.answer()

    if action == "home":
        await state.clear()

        await call.message.edit_text(
            WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=main_keyboard(call.from_user.id)
        )

    elif action == "captains":
        await state.clear()

        await call.message.edit_text(
            "👥 <b>КАПИТАНЫ И ИХ КОМАНДЫ</b>\n\n"
            "Раздел находится в разработке.",
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

    elif action == "admins":
        await state.clear()
        await show_admins(call.message)

    elif action == "unban":
        await state.clear()

        await call.message.edit_text(
            "🔓 <b>РАЗБАН</b>\n\n"
            "Раздел находится в разработке.",
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

    elif action == "complaints":
        await state.clear()

        await state.set_state(
            ComplaintForm.waiting_for_complaint
        )

        await call.message.edit_text(
            "📝 <b>ПОДАЧА ЖАЛОБЫ</b>\n\n"
            "Заполни форму одним сообщением:\n\n"
            "1. Твой никнейм\n"
            "2. Никнейм администратора\n"
            "3. Суть проблемы\n"
            "4. Доказательства (ссылки, текст или фото)\n\n"
            "📸 Можно приложить до 4 фотографий.\n\n"
            "Отправь жалобу следующим сообщением.\n"
            "Для отмены напиши /cancel.",
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

    elif action == "games":
        await state.clear()

        await call.message.edit_text(
            "🏒 <b>ИГРЫ MPHL</b>\n\n"
            "Раздел находится в разработке.",
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

    elif action == "results":
        await state.clear()

        await call.message.edit_text(
            "🏆 <b>ИТОГИ ИГР</b>\n\n"
            "Раздел находится в разработке.",
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

    elif action == "add_admin":
        await state.clear()

        await state.set_state(
            AddAdminForm.waiting_nickname
        )

        await call.message.edit_text(
            "➕ <b>ДОБАВЛЕНИЕ АДМИНИСТРАТОРА</b>\n\n"
            "1. Введите никнейм + номер.\n\n"
            "Пример: h1zEr #99\n\n"
            "/cancel — отмена",
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

    elif action == "remove_admin":
        await state.clear()

        await state.set_state(
            RemoveAdminForm.waiting_position
        )

        await call.message.edit_text(
            "🗑️ <b>УДАЛЕНИЕ АДМИНИСТРАТОРА</b>\n\n"
            "Введите должность:\n\n"
            + "\n".join(ADMIN_POSITIONS)
            + "\n\n/cancel — отмена",
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

    elif action == "history":
        await state.clear()
        await show_complaint_history(call.message)


# =====================================================
# 9. СПИСОК АДМИНИСТРАЦИИ
# =====================================================

async def show_admins(message: Message):

    admins = {
        admin["position"]: admin
        for admin in get_admins()
    }

    def fmt(position):
        admin = admins.get(position)

        if not admin or not admin["nickname"]:
            return "Свободно"

        nickname = escape(admin["nickname"])
        link = admin["telegram_link"]

        if link:
            return (
                f'<a href="{escape(link, quote=True)}">'
                f'{nickname}</a>'
            )

        return nickname

    text = (
        "🛡️ <b>СОСТАВ АДМИНИСТРАЦИИ MPHL</b>\n\n"

        "👑 <b>Высшая администрация</b>\n"
        f"Владелец лиги — {fmt('Владелец лиги')}\n"
        f"Главный администратор — {fmt('Главный администратор')}\n"
        f"Главный судья — {fmt('Главный судья')}\n\n"

        "⭐ <b>Старшая администрация</b>\n"
        f"Зам. главного администратора — "
        f"{fmt('Зам.главного администратора')}\n"
        f"Зам. главного судьи — "
        f"{fmt('Зам.главного судьи')}\n"
        f"Технический специалист — "
        f"{fmt('Технический специалист')}\n"
        f"Специалист по соц сетям — "
        f"{fmt('Специалист по соц сетям')}\n\n"

        "🏒 <b>Администрация</b>\n"
        f"Судья 1 — {fmt('Судья 1')}\n"
        f"Судья 2 — {fmt('Судья 2')}\n"
        f"Судья 3 — {fmt('Судья 3')}\n\n"

        '📩 <a href="https://t.me/infomphl/5">'
        "Подать заявку в администрацию</a>"
    )

    await message.answer(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=back_keyboard()
    )


# =====================================================
# 10. ИСТОРИЯ ЖАЛОБ
# =====================================================

async def show_complaint_history(message: Message):

    complaints = get_complaint_history(10)

    if not complaints:
        text = (
            "📋 <b>ИСТОРИЯ ЖАЛОБ</b>\n\n"
            "Жалоб пока нет."
        )

    else:
        lines = [
            "📋 <b>ПОСЛЕДНИЕ 10 ЖАЛОБ</b>\n"
        ]

        status_names = {
            "pending": "⏳ На рассмотрении",
            "approved": "✅ Одобрена",
            "rejected": "❌ Отклонена"
        }

        for complaint in complaints:
            status = status_names.get(
                complaint["status"],
                complaint["status"]
            )

            body = complaint["text"].replace("\n", " ")
            if len(body) > 120:
                body = body[:120] + "..."

            lines.append(
                f"\n<b>Жалоба #{complaint['id']}</b>\n"
                f"От: {escape(complaint['username'])}\n"
                f"Статус: {status}\n"
                f"Текст: {escape(body)}\n"
                f"Дата: {escape(complaint['created_at'])}"
            )

        text = "\n".join(lines)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


# =====================================================
# 11. ЖАЛОБЫ: СБОР ФОТОАЛЬБОМОВ
# =====================================================

album_buffers = {}
album_tasks = {}


async def submit_complaint(messages, state):

    if not messages:
        return

    first_message = messages[0]

    photos_count = sum(
        1 for msg in messages if msg.photo
    )

    if photos_count > 4:
        await first_message.answer(
            "❌ Можно прикрепить не более 4 фотографий."
        )
        return

    user_id = first_message.from_user.id

    username = (
        f"@{first_message.from_user.username}"
        if first_message.from_user.username
        else str(user_id)
    )

    texts = []

    for msg in messages:
        content = msg.caption or msg.text

        if content:
            texts.append(content)

    complaint_body = "\n".join(texts).strip()

    if not complaint_body:
        complaint_body = "(Текст не приложен, только вложения)"

    complaint_body = complaint_body[:2500]

    complaint_id = create_complaint(
        user_id,
        username,
        complaint_body
    )

    complaint_text = (
        f"🔔 <b>НОВАЯ ЖАЛОБА #{complaint_id}</b>\n\n"
        f"<b>Отправитель:</b> {escape(username)}\n\n"
        f"<b>Содержание:</b>\n{escape(complaint_body)}"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"complaint:approve:{complaint_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"complaint:reject:{complaint_id}"
                )
            ]
        ]
    )

    # Отправляем жалобу владельцу.
    try:
        await bot.send_message(
            OWNER_ID,
            complaint_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    except Exception:
        logger.exception(
            "Не удалось отправить жалобу владельцу."
        )

        delete_complaint(complaint_id)

        await first_message.answer(
            "❌ Не удалось доставить жалобу. "
            "Попробуйте позже."
        )
        return

    # Отправляем фотографии и документы.
    attachment_errors = False

    for msg in messages:
        if msg.photo or msg.document:
            try:
                await msg.forward(OWNER_ID)

            except Exception:
                attachment_errors = True
                logger.exception(
                    "Не удалось переслать вложение жалобы."
                )

    await state.clear()

    result = (
        f"✅ Жалоба #{complaint_id} успешно отправлена!\n\n"
        "Ожидайте рассмотрения в течение трёх рабочих дней."
    )

    if attachment_errors:
        result += (
            "\n\n⚠️ Не все вложения удалось доставить владельцу."
        )

    await first_message.answer(
        result,
        reply_markup=main_keyboard(user_id)
    )


async def flush_album(key, state):

    try:
        # Ждём, пока Telegram пришлёт все сообщения альбома.
        await asyncio.sleep(1.2)

        messages = album_buffers.pop(key, [])
        album_tasks.pop(key, None)

        if messages:
            await submit_complaint(messages, state)

    except asyncio.CancelledError:
        pass

    except Exception:
        logger.exception(
            "Ошибка обработки фотоальбома."
        )


@dp.message(ComplaintForm.waiting_for_complaint)
async def process_complaint(message: Message, state: FSMContext):

    if message.text and message.text.strip().lower() == "/cancel":
        await cancel_state(message, state)
        return

    if not (
        message.text
        or message.photo
        or message.document
    ):
        await message.answer(
            "❌ Отправьте текст, фотографию или документ."
        )
        return

    if message.media_group_id:

        key = (
            f"{message.chat.id}:"
            f"{message.from_user.id}:"
            f"{message.media_group_id}"
        )

        album_buffers.setdefault(key, []).append(message)

        old_task = album_tasks.get(key)

        if old_task and not old_task.done():
            old_task.cancel()

        album_tasks[key] = asyncio.create_task(
            flush_album(key, state)
        )

        return

    await submit_complaint(
        [message],
        state
    )


# =====================================================
# 12. ОБРАБОТКА ЖАЛОБ ВЛАДЕЛЬЦЕМ
# =====================================================

@dp.callback_query(F.data.startswith("complaint:"))
async def handle_complaint_action(call: CallbackQuery):

    if not is_owner(call.from_user.id):
        await call.answer(
            "⛔ Только владелец может рассматривать жалобы.",
            show_alert=True
        )
        return

    if not call.data or not call.message:
        await call.answer("Ошибка обработки.")
        return

    try:
        _, action, num_str = call.data.split(":")
        complaint_id = int(num_str)

    except (ValueError, TypeError):
        await call.answer(
            "Некорректная команда.",
            show_alert=True
        )
        return

    if action not in ("approve", "reject"):
        await call.answer(
            "Неизвестное действие.",
            show_alert=True
        )
        return

    complaint = get_complaint(complaint_id)

    if not complaint:
        await call.answer(
            "Жалоба не найдена.",
            show_alert=True
        )
        return

    new_status = (
        "approved"
        if action == "approve"
        else "rejected"
    )

    updated = review_complaint(
        complaint_id,
        new_status
    )

    if not updated:
        await call.answer(
            "⚠️ Эта жалоба уже обработана.",
            show_alert=True
        )
        return

    if action == "approve":
        result_text = (
            f"✅ Ваша жалоба #{complaint_id} одобрена.\n"
            "Администрация рассмотрит нарушение."
        )

        owner_result = (
            f"✅ Жалоба #{complaint_id} одобрена."
        )

    else:
        result_text = (
            f"❌ Ваша жалоба #{complaint_id} отклонена.\n"
            "Нарушение не подтверждено."
        )

        owner_result = (
            f"❌ Жалоба #{complaint_id} отклонена."
        )

    # Уведомление отправителя.
    try:
        await bot.send_message(
            complaint["user_id"],
            result_text
        )

    except Exception:
        logger.exception(
            "Не удалось уведомить пользователя."
        )

        owner_result += (
            "\n⚠️ Пользователь не получил уведомление."
        )

    # Убираем кнопки рассмотрения.
    try:
        await call.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:
        logger.exception(
            "Не удалось убрать кнопки жалобы."
        )

    await call.answer("Решение сохранено.")

    await call.message.answer(owner_result)


# =====================================================
# 13. ДОБАВЛЕНИЕ АДМИНИСТРАТОРА
# =====================================================

@dp.message(AddAdminForm.waiting_nickname)
async def process_add_nickname(
    message: Message,
    state: FSMContext
):

    if not is_owner(message.from_user.id):
        await state.clear()
        return

    if not message.text or not message.text.strip():
        await message.answer("❌ Введите никнейм текстом.")
        return

    nickname = message.text.strip()

    if len(nickname) > 64:
        await message.answer(
            "❌ Максимальная длина никнейма — 64 символа."
        )
        return

    await state.update_data(
        nickname=nickname
    )

    await state.set_state(
        AddAdminForm.waiting_position
    )

    await message.answer(
        "2. Выберите должность:\n\n"
        + "\n".join(ADMIN_POSITIONS),
        reply_markup=back_keyboard()
    )


@dp.message(AddAdminForm.waiting_position)
async def process_add_position(
    message: Message,
    state: FSMContext
):

    if not is_owner(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await message.answer("❌ Введите должность текстом.")
        return

    position = message.text.strip()

    if position not in ADMIN_POSITIONS:
        await message.answer(
            "❌ Такой должности нет в списке. "
            "Попробуйте ещё раз."
        )
        return

    if position == "Владелец лиги":
        await message.answer(
            "⛔ Нельзя назначить другого владельца лиги."
        )
        return

    await state.update_data(
        position=position
    )

    await state.set_state(
        AddAdminForm.waiting_level
    )

    await message.answer(
        "3. Укажите уровень администрации.\n\n"
        "Например: Старшая администрация.",
        reply_markup=back_keyboard()
    )


@dp.message(AddAdminForm.waiting_level)
async def process_add_level(
    message: Message,
    state: FSMContext
):

    if not is_owner(message.from_user.id):
        await state.clear()
        return

    if not message.text or not message.text.strip():
        await message.answer(
            "❌ Укажите уровень администрации."
        )
        return

    level = message.text.strip()

    if len(level) > 100:
        await message.answer(
            "❌ Слишком длинное название уровня."
        )
        return

    await state.update_data(
        level=level
    )

    await state.set_state(
        AddAdminForm.waiting_telegram
    )

    await message.answer(
        "4. Отправьте ссылку на Telegram.\n\n"
        "Пример: https://t.me/h1zEr54",
        reply_markup=back_keyboard()
    )


@dp.message(AddAdminForm.waiting_telegram)
async def process_add_telegram(
    message: Message,
    state: FSMContext
):

    if not is_owner(message.from_user.id):
        await state.clear()
        return

    if not message.text or not message.text.strip():
        await message.answer(
            "❌ Введите Telegram-ссылку."
        )
        return

    telegram_link = message.text.strip()

    if not valid_telegram_link(telegram_link):
        await message.answer(
            "❌ Некорректная ссылка.\n\n"
            "Используйте формат:\n"
            "https://t.me/username"
        )
        return

    data = await state.get_data()

    save_admin(
        data["position"],
        data["nickname"],
        telegram_link,
        data["level"]
    )

    await state.clear()

    await message.answer(
        "✅ <b>АДМИНИСТРАТОР ДОБАВЛЕН!</b>\n\n"
        f"👤 Никнейм: {escape(data['nickname'])}\n"
        f"🛡️ Должность: {escape(data['position'])}\n"
        f"⭐ Уровень: {escape(data['level'])}\n"
        f"🔗 Telegram: {escape(telegram_link)}",
        parse_mode="HTML",
        reply_markup=main_keyboard(message.from_user.id)
    )


# =====================================================
# 14. УДАЛЕНИЕ АДМИНИСТРАТОРА
# =====================================================

@dp.message(RemoveAdminForm.waiting_position)
async def process_remove_admin(
    message: Message,
    state: FSMContext
):

    if not is_owner(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await message.answer(
            "❌ Введите должность текстом."
        )
        return

    position = message.text.strip()

    if position not in ADMIN_POSITIONS:
        await message.answer(
            "❌ Такой должности нет. Попробуйте ещё раз."
        )
        return

    if position == "Владелец лиги":
        await message.answer(
            "⛔ Нельзя удалить владельца лиги."
        )
        return

    admin = get_admin(position)

    if not admin or not admin["nickname"]:
        await message.answer(
            "⚠️ Эта должность уже свободна."
        )
        return

    removed_nickname = admin["nickname"]

    remove_admin(position)

    await state.clear()

    await message.answer(
        "✅ <b>АДМИНИСТРАТОР УДАЛЁН</b>\n\n"
        f"👤 Никнейм: {escape(removed_nickname)}\n"
        f"🛡️ Должность: {escape(position)}",
        parse_mode="HTML",
        reply_markup=main_keyboard(message.from_user.id)
    )


# =====================================================
# 15. ЗАПУСК БОТА
# =====================================================

async def main():

    init_db()

    logger.info("🏒 MPHL BOT запускается...")

    try:
        await bot.delete_webhook(
            drop_pending_updates=True
        )

        await dp.start_polling(bot)

    finally:

        # Завершаем задачи обработки фотоальбомов.
        tasks = list(album_tasks.values())

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(
                *tasks,
                return_exceptions=True
            )

        album_tasks.clear()
        album_buffers.clear()

        await bot.session.close()

        logger.info("MPHL BOT остановлен.")


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")
