import os
import asyncio
import logging
import requests
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart

# Загружаем переменные окружения (.env)
load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Настраиваем логирование, чтобы видеть ошибки в консоли
logging.basicConfig(level=logging.INFO)

# Инициализируем бота и диспетчер
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Функция для отправки запроса в OpenRouter (Hermes)
def ask_hermes(user_text: str) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "nousresearch/hermes-3-llama-3.1-405b",
        "messages": [
            {"role": "system", "content": "Ты умный и полезный ассистент."},
            {"role": "user", "content": user_text}
        ]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            return f"Ошибка OpenRouter API ({response.status_code}): {response.text}"
    except Exception as e:
        return f"Произошла ошибка при запросе к ИИ: {e}"

# Обработчик команды /start
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("Привет! Я бот с подключенной моделью Hermes 3. Напиши мне что-нибудь!")

# Обработчик обычных текстовых сообщений
@dp.message()
async def handle_message(message: types.Message):
    # Отправляем плашку "печатает..." в чат Telegram
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    # Обращаемся к Hermes (в отдельном потоке, чтобы не блокировать бота)
    loop = asyncio.get_event_loop()
    ai_response = await loop.run_in_executor(None, ask_hermes, message.text)
    
    # Отправляем ответ пользователю
    await message.answer(ai_response)

# Главная функция запуска
async def main():
    print("Бот успешно запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())