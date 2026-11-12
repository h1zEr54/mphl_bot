import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0)) # Берем ID владельца из переменной Bothost
# ===============================================

if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN!")
if not OWNER_ID:
    raise ValueError("Не найден OWNER_ID!")

WELCOME_TEXT = """
Добро пожаловать в официальный бот лиги MPHL!
Выбери нужный раздел на клавиатуре ниже 👇
"""

BTN_CAPTAINS = "Капитаны и их команды"
BTN_ADMINS = "Состав администрации"
BTN_UNBAN = "Разбан"
BTN_COMPLAINTS = "Жалобы на администрацию"
BTN_GAMES = "Игры"
BTN_RESULTS = "Итоги игр"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ---------- Состояния формы жалобы ----------
class ComplaintForm(StatesGroup):
    nickname = State()
    admin_nickname = State()
    violation = State()
    proof = State()


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CAPTAINS), KeyboardButton(text=BTN_ADMINS)],
            [KeyboardButton(text=BTN_UNBAN), KeyboardButton(text=BTN_COMPLAINTS)],
            [KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_RESULTS)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери раздел 👇",
    )


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(WELCOME_TEXT, reply_markup=main_keyboard())


# ---------- Начало формы жалобы ----------
@dp.message(F.text == BTN_COMPLAINTS)
async def start_complaint(message: Message, state: FSMContext):
    await state.set_state(ComplaintForm.nickname)
    await message.answer(
        "Форма подачи жалобы на администратора👇\n\n"
        "1. Ваш никнейм:\n\n"
        "(Отправь следующим сообщением, /cancel — отмена)"
    )


@dp.message(ComplaintForm.nickname)
async def process_nickname(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_keyboard())
        return
    await state.update_data(nickname=message.text)
    await state.set_state(ComplaintForm.admin_nickname)
    await message.answer("2. Никнейм администратора:")


@dp.message(ComplaintForm.admin_nickname)
async def process_admin_nickname(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_keyboard())
        return
    await state.update_data(admin_nickname=message.text)
    await state.set_state(ComplaintForm.violation)
    await message.answer("3. Что нарушил:")


@dp.message(ComplaintForm.violation)
async def process_violation(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_keyboard())
        return
    await state.update_data(violation=message.text)
    await state.set_state(ComplaintForm.proof)
    await message.answer("4. Доказательство (текст, ссылка или фото):")


@dp.message(ComplaintForm.proof)
async def process_proof(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_keyboard())
        return

    data = await state.get_data()
    await state.clear()

    # Собираем текст жалобы
    proof_text = message.text if message.text else "(Отправлено фото/файл)"
    
    complaint_text = (
        "🔔 <b>НОВАЯ ЖАЛОБА НА АДМИНИСТРАЦИЮ</b>\n\n"
        f"<b>1. Никнейм заявителя:</b> {data['nickname']}\n"
        f"<b>2. Никнейм администратора:</b> {data['admin_nickname']}\n"
        f"<b>3. Что нарушил:</b> {data['violation']}\n"
        f"<b>4. Доказательство:</b> {proof_text}\n\n"
        f"<i>Отправитель: @{message.from_user.username or message.from_user.id}</i>"
    )

    # Отправляем ТОЛЬКО владельцу
    try:
        await bot.send_message(OWNER_ID, complaint_text, parse_mode="HTML")
        # Если пользователь отправил фото/файл — пересылаем его владельцу
        if message.photo or message.document:
            await message.forward(OWNER_ID)
    except Exception as e:
        logging.error(f"Не удалось отправить жалобу владельцу: {e}")
        await message.answer("❌ Ошибка: не удалось доставить жалобу. Сообщите владельцу.")
        return

    await message.answer(
        "✅ Жалоба отправлена. Рассмотрим в ближайшее время.",
        reply_markup=main_keyboard(),
    )


# ---------- Остальные кнопки (заглушки) ----------
@dp.message(F.text == BTN_CAPTAINS)
async def on_captains(message: Message):
    await message.answer("👥 Раздел: Капитаны и их команды\n(заполни здесь)")


@dp.message(F.text == BTN_ADMINS)
async def on_admins(message: Message):
    await message.answer("🛡 Раздел: Состав администрации\n(заполни здесь)")


@dp.message(F.text == BTN_UNBAN)
async def on_unban(message: Message):
    await message.answer("🔓 Раздел: Разбан\n(заполни здесь)")


@dp.message(F.text == BTN_GAMES)
async def on_games(message: Message):
    await message.answer("🎮 Раздел: Игры\n(заполни здесь)")


@dp.message(F.text == BTN_RESULTS)
async def on_results(message: Message):
    await message.answer("📊 Раздел: Итоги игр\n(заполни здесь)")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
