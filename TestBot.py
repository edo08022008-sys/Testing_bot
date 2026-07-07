import asyncio
import logging
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

API_TOKEN = "API_TOKEN"
DB_NAME = "test_bot.db"

# Вопросы для теста
QUESTIONS = [
    {
        "text": "Столица Франции?",
        "options": ["Берлин", "Мадрид", "Париж", "Рим"],
        "correct": 2
    },
    {
        "text": "Сколько планет в Солнечной системе?",
        "options": ["7", "8", "9", "10"],
        "correct": 1
    },
    {
        "text": "2 + 2 = ?",
        "options": ["3", "4", "5", "22"],
        "correct": 1
    }
]

class RegistrationStates(StatesGroup):
    waiting_for_first_name = State()
    waiting_for_last_name = State()
    waiting_for_group = State()

class TestStates(StatesGroup):
    answering = State()

reg_router = Router()   # для регистрации
test_router = Router()  # для теста

async def init_db():
    """Создаёт таблицы, если их нет."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS students (
                chat_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                group_name TEXT NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES students (chat_id)
            )
        ''')
        await db.commit()

async def get_student(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT first_name, last_name, group_name FROM students WHERE chat_id = ?",
            (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row  # (first_name, last_name, group_name) или None

async def register_student(chat_id: int, first_name: str, last_name: str, group_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT OR REPLACE INTO students (chat_id, first_name, last_name, group_name)
            VALUES (?, ?, ?, ?)
        ''', (chat_id, first_name, last_name, group_name))
        await db.commit()

async def save_result(chat_id: int, score: int, total: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT INTO results (chat_id, score, total, created_at)
            VALUES (?, ?, ?, ?)
        ''', (chat_id, score, total, datetime.now().isoformat()))
        await db.commit()

async def send_question(chat_id: int, state: FSMContext, bot: Bot):
    data = await state.get_data()
    idx = data.get("current_q", 0)

    if idx >= len(QUESTIONS):
        await finish_test(chat_id, state, bot)
        return

    question = QUESTIONS[idx]
    text = f"Вопрос {idx + 1}/{len(QUESTIONS)}\n\n{question['text']}"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=option,
            callback_data=f"answer_{i}"
        )] for i, option in enumerate(question["options"])
    ])

    await bot.send_message(chat_id, text, reply_markup=keyboard)

async def finish_test(chat_id: int, state: FSMContext, bot: Bot):
    """Завершает тест: сохраняет результат в БД и выводит итог."""
    data = await state.get_data()
    score = data["score"]
    total = len(QUESTIONS)

    await save_result(chat_id, score, total)

    student = await get_student(chat_id)
    if student:
        first_name, last_name, group_name = student
        student_info = f"Студент: {first_name} {last_name}, группа {group_name}"
    else:
        student_info = "Студент не найден в базе"

    await bot.send_message(
        chat_id,
        f"🎉 Тест завершён!\n{student_info}\nПравильных ответов: {score} из {total}"
    )
    await state.clear()

@reg_router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Начало работы: проверка регистрации или её начало."""
    chat_id = message.chat.id
    student = await get_student(chat_id)

    if student:
        await state.set_state(TestStates.answering)
        await state.update_data(current_q=0, score=0)
        await send_question(chat_id, state, message.bot)
    else:
        await state.set_state(RegistrationStates.waiting_for_first_name)
        await message.answer("Добро пожаловать! Введите ваше имя:")

@reg_router.message(RegistrationStates.waiting_for_first_name)
async def process_first_name(message: types.Message, state: FSMContext):
    await state.update_data(first_name=message.text.strip())
    await state.set_state(RegistrationStates.waiting_for_last_name)
    await message.answer("Теперь введите фамилию:")

@reg_router.message(RegistrationStates.waiting_for_last_name)
async def process_last_name(message: types.Message, state: FSMContext):
    await state.update_data(last_name=message.text.strip())
    await state.set_state(RegistrationStates.waiting_for_group)
    await message.answer("Введите номер группы (класса):")

@reg_router.message(RegistrationStates.waiting_for_group)
async def process_group(message: types.Message, state: FSMContext):
    group_name = message.text.strip()
    data = await state.get_data()
    first_name = data.get("first_name")
    last_name = data.get("last_name")

    await register_student(message.chat.id, first_name, last_name, group_name)

    await state.set_state(TestStates.answering)
    await state.update_data(current_q=0, score=0)
    await message.answer(f"Регистрация завершена, {first_name} {last_name}! Начинаем тест.")
    await send_question(message.chat.id, state, message.bot)

@test_router.callback_query(F.data.startswith("answer_"), TestStates.answering)
async def process_answer(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает ответ."""
    await callback.answer()
    await callback.message.delete()  # убираем сообщение с вопросом

    data = await state.get_data()
    idx = data["current_q"]
    score = data["score"]
    question = QUESTIONS[idx]
    chosen = int(callback.data.split("_")[1])

    if chosen == question["correct"]:
        score += 1
        await callback.message.answer("✅ Верно!")
    else:
        correct_option = question["options"][question["correct"]]
        await callback.message.answer(f"❌ Неверно. Правильный ответ: {correct_option}")

    idx += 1
    await state.update_data(current_q=idx, score=score)
    await send_question(callback.message.chat.id, state, callback.bot)

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()

    bot = Bot(token=API_TOKEN)
    dp = Dispatcher()

    # Подключаем роутеры (порядок важен, /start будет пойман в reg_router)
    dp.include_router(reg_router)
    dp.include_router(test_router)
    print('start...')
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())