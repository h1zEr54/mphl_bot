import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

# ================== НАСТРОЙКИ ==================
# Токен берется из переменной окружения (BOT_TOKEN), которую ты укажешь в Bothost
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Текст приветствия
WELCOME_TEXT = """
Добро пожаловать на сервер MPHL!
Правила и новости лиги читай в нашем Telegram-канале: @MPHL_Puck
"""
# ===============================================

logging.basicConfig(level=logging.INFO)

# Проверка, что токен вообще передан
if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN! Добавь переменную окружения BOT_TOKEN в настройках Bothost.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    # Реагирует на кнопку "Старт" и команду /start
    await message.answer(WELCOME_TEXT)


async def main():
    # Удаляем вебхуки (если они были) перед запуском поллинга
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
