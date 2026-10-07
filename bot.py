import os
import sys
import json
import asyncio
import logging
import asyncpg
import httpx
import docx
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command

# Корректная установка event loop policy для Windows
if sys.platform == 'win32':
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

logging.basicConfig(level=logging.INFO)
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "survey_db")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db_pool = None

# Словарь для хранения активных задач пользователей (user_id -> asyncio.Task)
user_tasks = {}


# Инициализация базы данных
async def init_db():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME
        )
        logging.info("Успешное подключение к PostgreSQL!")
    except Exception as e:
        logging.error(f"Ошибка подключения к PostgreSQL: {e}")


# --- ИНСТРУМЕНТЫ АГЕНТА ДЛЯ РАБОТЫ С ФАЙЛАМИ ---

def tool_write_file(path: str, content: str) -> str:
    """Создает или перезаписывает файл по указанному пути."""
    try:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Файл '{path}' успешно сохранен."
    except Exception as e:
        return f"Ошибка записи в файл '{path}': {e}"


def tool_read_file(path: str) -> str:
    """Читает содержимое файла (.txt, .md, .py, .docx и др.)."""
    try:
        if not os.path.exists(path):
            return f"Файл '{path}' не найден."
        if path.endswith(".docx"):
            doc = docx.Document(path)
            return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Ошибка чтения файла '{path}': {e}"


def tool_list_files(directory: str = ".") -> str:
    """Возвращает список всех файлов в папке проекта."""
    ignore_dirs = {'.git', '__pycache__', 'venv', '.idea', '.vscode'}
    result = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for file in files:
            result.append(os.path.relpath(os.path.join(root, file), directory))
    return "\n".join(result) if result else "Папка пуста."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Записывает или создает файл в проекте с указанным содержимым.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Относительный путь к файлу"},
                    "content": {"type": "string", "description": "Полный текст файла"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Читает файл из проекта.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь к файлу"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Возвращает список всех файлов проекта.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Корневая директория"}
                }
            }
        }
    }
]

SYSTEM_PROMPT = """
Ты — профессиональный Fullstack AI-агент и старший разработчик.
Твоя главная задача — создавать веб-приложение (Конструктор опросников на FastAPI + SQLAlchemy/PostgreSQL + Jinja2 + Tailwind CSS) согласно архитектуре из ТЗ.

Используй инструменты write_file, read_file и list_files для работы с кодом проекта.
Пиши чистый, готовый к запуску код.
"""


@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "Привет! Я ИИ-агент разработчик. Я готов строить Fullstack-приложение прямо в этой папке.\n\n"
        "Для остановки текущего процесса генерации используй команду /cancel."
    )


@dp.message(Command("cancel", "stop"))
async def cancel_handler(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_tasks and not user_tasks[user_id].done():
        user_tasks[user_id].cancel()
        del user_tasks[user_id]
        await message.answer("⛔ Выполнение задачи остановлено!")
    else:
        await message.answer("Сейчас нет активных выполняющихся задач.")


@dp.message()
async def agent_handle_message(message: types.Message):
    user_id = message.from_user.id

    if user_id in user_tasks and not user_tasks[user_id].done():
        await message.answer("⚠️ Предыдущая задача еще выполняется! Для отмены отправь /cancel")
        return

    # Запускаем фоновую задачу
    task = asyncio.create_task(run_agent_loop(message))
    user_tasks[user_id] = task

    try:
        await task
    except asyncio.CancelledError:
        logging.info(f"Задача пользователем {user_id} отменена.")
    finally:
        user_tasks.pop(user_id, None)


async def run_agent_loop(message: types.Message):
    user_prompt = message.text
    status_msg = await message.answer("🧠 Анализирую задачу...\n*(для отмены отправь /cancel)*", parse_mode="Markdown")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]

    async with httpx.AsyncClient(timeout=120.0) as client:
        for step in range(8):
            await asyncio.sleep(0)  # Точка проверки отмены от пользователя

            payload = {
                "model": "deepseek/deepseek-v4.1-flash",
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto"
            }

            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            }

            try:
                response = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
                res_data = response.json()
            except Exception as req_err:
                await status_msg.edit_text(f"❌ Ошибка сети: {req_err}")
                return

            if "choices" not in res_data:
                await status_msg.edit_text(f"❌ Ошибка API: {res_data}")
                return

            choice = res_data["choices"][0]["message"]
            messages.append(choice)

            if choice.get("tool_calls"):
                for tool_call in choice["tool_calls"]:
                    fn_name = tool_call["function"]["name"]
                    fn_args = json.loads(tool_call["function"]["arguments"])

                    # Безопасное извлечение аргументов для защиты от KeyError
                    file_path = fn_args.get("path") or fn_args.get("filepath") or fn_args.get("file_path") or fn_args.get("file") or ""
                    file_content = fn_args.get("content") or fn_args.get("text") or fn_args.get("code") or ""

                    try:
                        await status_msg.edit_text(
                            f"⚙️ Выполняю `{fn_name}`: `{file_path}`...\n*(для отмены отправь /cancel)*",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass

                    tool_result = ""
                    if fn_name == "write_file":
                        if not file_path:
                            tool_result = "Ошибка: не указан path"
                        else:
                            tool_result = tool_write_file(file_path, file_content)

                    elif fn_name == "read_file":
                        if not file_path:
                            tool_result = "Ошибка: не указан path"
                        else:
                            tool_result = tool_read_file(file_path)

                    elif fn_name == "list_files":
                        directory = fn_args.get("directory") or file_path or "."
                        tool_result = tool_list_files(directory)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": tool_result
                    })
            else:
                final_text = choice.get("content", "Завершено.")
                try:
                    await status_msg.edit_text(final_text, parse_mode="Markdown")
                except Exception:
                    await status_msg.edit_text(final_text)
                return


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())