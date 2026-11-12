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
OWNER_ID = int(os.getenv("OWNER_ID", 0))
# ===============================================

WELCOME_TEXT = """
Привет!

Этот бот был создан для лиги MPHL в игре Puck

Наш тгк: @MPHL_Puck

Наш дискорд сервер: В разработке
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


class ComplaintForm(StatesGroup):
    waiting_for_complaint = State()


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


@dp.message(F.text == BTN_COMPLAINTS)
async def start_complaint(message: Message, state: FSMContext):
    await state.set_state(ComplaintForm.waiting_for_complaint)
    await message.answer(
        "📝 <b>Форма подачи жалобы на администратора</b>\n\n"
        "Напиши всё в одном сообщении по следующему шаблону:\n\n"
        "1. Твой никнейм\n"
        "2. Никнейм администратора\n"
        "3. Что нарушил (суть проблемы)\n"
        "4. Доказательства (ссылку, текст или прикрепи фото)\n\n"
        "📸 <i>Можно прикрепить до 4-х фотографий за раз.</i>\n\n"
        "Отправь это одним сообщением, или напиши /cancel для отмены.",
        parse_mode="HTML"
    )


@dp.message(ComplaintForm.waiting_for_complaint)
async def process_complaint(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_keyboard())
        return

    # --- ПРОВЕРКА НА КОЛИЧЕСТВО ФОТО ---
    # В Telegram фото приходят как media_group. Если пользователь отправил альбом,
    # aiogram получит несколько сообщений. Но здесь мы смотрим на текущее сообщение.
    # Если пользователь прикрепил больше 4 фото как альбом, бот получит их по одному,
    # но мы можем проверить, есть ли в сообщении photo, и попросить не спамить.
    
    # Проверка на количество фото в одном сообщении (если вдруг пришло как документы или фото)
    if message.photo and len(message.photo) > 4:
        await message.answer("❌ К сожалению, я не принимаю больше 4-х фоток за раз.")
        return

    # Собираем текст жалобы
    complaint_body = message.text if message.text else "(Текст не приложен, только фото/файл)"
    
    complaint_text = (
        "🔔 <b>НОВАЯ ЖАЛОБА НА АДМИНИСТРАЦИЮ</b>\n\n"
        f"<b>Отправитель:</b> @{message.from_user.username or message.from_user.id}\n\n"
        f"<b>Содержание:</b>\n{complaint_body}"
    )

    try:
        await bot.send_message(OWNER_ID, complaint_text, parse_mode="HTML")
        # Если пользователь отправил фото/файл — пересылаем владельцу
        if message.photo or message.document:
            await message.forward(OWNER_ID)
    except Exception as e:
        logging.error(f"Не удалось отправить жалобу владельцу: {e}")
        await message.answer("❌ Ошибка: не удалось доставить жалобу. Сообщите владельцу.")
        return

    await state.clear()
    await message.answer(
        "✅ Жалоба была успешно отправлена, ожидайте рассмотрения в течение трёх рабочих дней.",
        reply_markup=main_keyboard(),
    )


# ---------- Остальные кнопки ----------
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
