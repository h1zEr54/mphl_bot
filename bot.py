import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

# ================== НАСТРОЙКИ ==================
# Токен берется из переменных окружения (настраивается в Bothost)
BOT_TOKEN = os.getenv("BOT_TOKEN")
# ===============================================

# Приветствие
WELCOME_TEXT = """
Добро пожаловать в официальный бот лиги MPHL!
Выбери нужный раздел на клавиатуре ниже 👇
"""

# Тексты кнопок
BTN_CAPTAINS = "Капитаны и их команды"
BTN_ADMINS = "Состав администрации"
BTN_UNBAN = "Разбан"
BTN_COMPLAINTS = "Жалобы на администрацию"
BTN_GAMES = "Игры"
BTN_RESULTS = "Итоги игр"

logging.basicConfig(level=logging.INFO)

if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN! Добавь переменную окружения BOT_TOKEN в настройках Bothost.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def main_keyboard() -> ReplyKeyboardMarkup:
    """Главная клавиатура с 6 кнопками (по 2 в ряд)."""
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CAPTAINS), KeyboardButton(text=BTN_ADMINS)],
            [KeyboardButton(text=BTN_UNBAN), KeyboardButton(text=BTN_COMPLAINTS)],
            [KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_RESULTS)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери раздел 👇",
    )
    return kb


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(WELCOME_TEXT, reply_markup=main_keyboard())


# ================== ОБРАБОТЧИКИ КНОПОК ==================

@dp.message(F.text == BTN_CAPTAINS)
async def on_captains(message: Message):
    await message.answer("👥 Раздел: Капитаны и их команды\n(Здесь будет список капитанов)")

@dp.message(F.text == BTN_ADMINS)
async def on_admins(message: Message):
    await message.answer("🛡 Раздел: Состав администрации\n(Здесь будет список админов)")

@dp.message(F.text == BTN_UNBAN)
async def on_unban(message: Message):
    await message.answer("🔓 Раздел: Разбан\n(Здесь будет форма для подачи заявки на разбан)")

@dp.message(F.text == BTN_COMPLAINTS)
async def on_complaints(message: Message):
    await message.answer("⚠️ Раздел: Жалобы на администрацию\n(Здесь будет форма для жалоб)")

@dp.message(F.text == BTN_GAMES)
async def on_games(message: Message):
    await message.answer("🎮 Раздел: Игры\n(Здесь будет расписание матчей)")

@dp.message(F.text == BTN_RESULTS)
async def on_results(message: Message):
    await message.answer("📊 Раздел: Итоги игр\n(Здесь будут результаты матчей)")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
