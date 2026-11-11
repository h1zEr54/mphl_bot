import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = "ВСТАВЬ_СЮДА_ТОКЕН_ОТ_BOTFATHER"
# ===============================================

# Сюда впиши своё приветствие (оставлено пустым специально)
WELCOME_TEXT = """
"""

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    # Реагирует на кнопку "Старт" и команду /start
    await message.answer(WELCOME_TEXT)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())