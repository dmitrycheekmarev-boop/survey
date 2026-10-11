import os
import json
import asyncio
import logging
import asyncpg
import docx
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from google import genai
from google.genai import types as genai_types

logging.basicConfig(level=logging.INFO)
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "pass")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")

# Инициализация клиента Google Gemini
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db_pool = None

user_tasks = {}


# --- БАЗА ДАННЫХ ---

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


async def save_chat_message(user_id: int, role: str, content: str):
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO chat_history (user_id, role, content) VALUES ($1, $2, $3)",
                user_id, role, content
            )
    except Exception as e:
        logging.error(f"Ошибка сохранения истории в БД: {e}")


async def load_chat_history(user_id: int, limit: int = 12) -> list:
    if not db_pool:
        return []
    try:
        async with db_pool.acquire() as conn:
            query = """
                SELECT role, content 
                FROM (
                    SELECT id, role, content 
                    FROM chat_history 
                    WHERE user_id = $1 
                    ORDER BY id DESC 
                    LIMIT $2
                ) sub
                ORDER BY id ASC;
            """
            rows = await conn.fetch(query, user_id, limit)

            history = []
            for r in rows:
                content = r["content"]
                if len(content) > 2500:
                    content = content[:2500] + "\n... [часть содержимого обрезана для экономии контекста]"
                
                # Gemini принимает роли: 'user' и 'model'
                role = "model" if r["role"] == "assistant" else "user"
                history.append(genai_types.Content(
                    role=role,
                    parts=[genai_types.Part.from_text(text=content)]
                ))
            return history
    except Exception as e:
        logging.error(f"Ошибка загрузки истории из БД: {e}")
        return []


# --- ИНСТРУМЕНТЫ АГЕНТА ДЛЯ РАБОТЫ С ФАЙЛАМИ ---

def write_file(path: str, content: str) -> str:
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


def read_file(path: str) -> str:
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


def list_files(directory: str = ".") -> str:
    """Возвращает список всех файлов в папке проекта."""
    ignore_dirs = {'.git', '__pycache__', 'venv', '.idea', '.vscode'}
    result = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for file in files:
            result.append(os.path.relpath(os.path.join(root, file), directory))
    return "\n".join(result) if result else "Папка пуста."


SYSTEM_PROMPT = """
Ты — профессиональный Fullstack AI-агент и старший разработчик.
Твоя главная задача — создавать веб-приложение (Конструктор опросников на FastAPI + SQLAlchemy/PostgreSQL + Jinja2 + Tailwind CSS) согласно архитектуре из ТЗ.

Используй инструменты write_file, read_file и list_files для работы с кодом проекта.
Пиши чистый, готовый к запуску код.
"""


# --- ХЭНДЛЕРЫ TELEGRAM ---

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "Привет! Я ИИ-агент разработчик на базе Google Gemini. Я готов строить Fullstack-приложение прямо в этой папке.\n\n"
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


@dp.message(Command("clear"))
async def clear_history_cmd(message: types.Message):
    user_id = message.from_user.id
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM chat_history WHERE user_id = $1", user_id)
    await message.answer("🧹 История диалога и контекст успешно очищены!")


@dp.message()
async def agent_handle_message(message: types.Message):
    user_id = message.from_user.id

    if user_id in user_tasks and not user_tasks[user_id].done():
        await message.answer("⚠️ Предыдущая задача еще выполняется! Для отмены отправь /cancel")
        return

    task = asyncio.create_task(run_agent_loop(message))
    user_tasks[user_id] = task

    try:
        await task
    except asyncio.CancelledError:
        logging.info(f"Задача пользователем {user_id} отменена.")
    finally:
        user_tasks.pop(user_id, None)


async def run_agent_loop(message: types.Message):
    user_id = message.from_user.id
    user_prompt = message.text

    status_msg = await message.answer("🧠 Анализирую задачу...\n*(для отмены отправь /cancel)*", parse_mode="Markdown")

    await save_chat_message(user_id, "user", user_prompt)
    contents = await load_chat_history(user_id, limit=12)

    # Если в истории ещё нет текущего сообщения, добавляем его
    if not contents or contents[-1].parts[0].text != user_prompt:
        contents.append(genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=user_prompt)]
        ))

    available_tools = {
        "write_file": write_file,
        "read_file": read_file,
        "list_files": list_files
    }

    config = genai_types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[write_file, read_file, list_files]
    )

    for step in range(6):
        await asyncio.sleep(0)

        try:
            # Выполняем синхронный вызов SDK в отдельном потоке
            response = await asyncio.to_thread(
                gemini_client.models.generate_content,
                model="gemini-flash-lite-latest",
                contents=contents,
                config=config
            )
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка Gemini API: {e}")
            return

        candidate = response.candidates[0]
        contents.append(candidate.content)

        # Проверяем, вызвала ли модель функции
        function_calls = [part.function_call for part in candidate.content.parts if part.function_call]

        if function_calls:
            function_responses = []
            for call in function_calls:
                fn_name = call.name
                fn_args = dict(call.args) if call.args else {}

                arg_preview = fn_args.get("path") or fn_args.get("directory") or ""
                try:
                    await status_msg.edit_text(
                        f"⚙️ Выполняю `{fn_name}`: `{arg_preview}`...\n*(для отмены отправь /cancel)*",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                func = available_tools.get(fn_name)
                result_str = str(func(**fn_args)) if func else f"Функция {fn_name} не найдена."

                if len(result_str) > 3000:
                    result_str = result_str[:3000] + "\n... [результат слишком длинный, часть вырезана]"

                function_responses.append(
                    genai_types.Part.from_function_response(
                        name=fn_name,
                        response={"result": result_str}
                    )
                )

            contents.append(genai_types.Content(role="tool", parts=function_responses))
        else:
            final_text = response.text or "Завершено."
            await save_chat_message(user_id, "assistant", final_text)

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