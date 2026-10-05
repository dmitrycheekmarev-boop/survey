import os
import sys
import asyncio
import logging
import httpx
import psycopg
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart

# Фикс для Windows + Python 3.14
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5432")

# Строка подключения для psycopg
CONN_INFO = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# 1. Сохранение сообщения в БД (асинхронно через psycopg)
async def save_message(user_id: int, role: str, content: str):
    async with await psycopg.AsyncConnection.connect(CONN_INFO) as aconn:
        async with aconn.cursor() as acur:
            await acur.execute(
                "INSERT INTO chat_history (user_id, role, content) VALUES (%s, %s, %s)",
                (user_id, role, content)
            )

# 2. Получение истории сообщений
async def get_chat_history(user_id: int, limit: int = 10) -> list:
    async with await psycopg.AsyncConnection.connect(CONN_INFO) as aconn:
        async with aconn.cursor() as acur:
            await acur.execute(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at 
                    FROM chat_history 
                    WHERE user_id = %s 
                    ORDER BY id DESC 
                    LIMIT %s
                ) sub 
                ORDER BY created_at ASC
                """,
                (user_id, limit)
            )
            rows = await acur.fetchall()
            return [{"role": row[0], "content": row[1]} for row in rows]

# 3. Асинхронный запрос к OpenRouter (Hermes)
async def ask_hermes_with_context(messages_history: list) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    
    system_prompt = [{"role": "system", "content": "Ты умный ассистент. Помни контекст беседы."}]
    full_messages = system_prompt + messages_history

    payload = {
        "model": "nousresearch/hermes-3-llama-3.1-405b",
        "messages": full_messages
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                data = response.json()
                return data['choices'][0]['message']['content'].strip()
            else:
                return f"Ошибка API ({response.status_code}): {response.text}"
        except Exception as e:
            return f"Ошибка запроса: {e}"

# Обработчик /start
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("Привет! Я помню контекст нашего разговора благодаря PostgreSQL. Напиши мне что-нибудь!")

# Обработчик текста
@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # 1. Сохраняем вопрос пользователя в БД
    await save_message(user_id, "user", user_text)

    # 2. Достаем контекст
    history = await get_chat_history(user_id, limit=10)

    # 3. Получаем ответ ИИ
    ai_response = await ask_hermes_with_context(history)

    # 4. Сохраняем ответ ИИ
    await save_message(user_id, "assistant", ai_response)

    # 5. Отправляем пользователю
    await message.answer(ai_response)

async def main():
    print("Проверяем подключение к PostgreSQL...")
    # Тестовое подключение перед стартом
    async with await psycopg.AsyncConnection.connect(CONN_INFO) as aconn:
        print("Успешное подключение к PostgreSQL!")

    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())