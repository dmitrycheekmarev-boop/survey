import os
import requests
from dotenv import load_dotenv

# Загружаем переменные из файла .env
load_dotenv()

api_key = os.getenv("OPENROUTER_API_KEY")

response = requests.post(
    url="https://openrouter.ai/api/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "json",
    },
    json={
        "model": "nousresearch/hermes-3-llama-3.1-405b",  # Вызываем Hermes 3
        "messages": [
            {"role": "system", "content": "Ты ассистент разработчика."},
            {"role": "user", "content": "Привет! Если ты меня слышишь, ответь 'Вайбкод работает!'"}
        ]
    }
)

if response.status_code == 200:
    print("Ответ от Hermes:", response.json()['choices'][0]['message']['content'])
else:
    print("Ошибка:", response.status_code, response.text)