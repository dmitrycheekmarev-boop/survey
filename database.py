from __future__ import annotations
"""
database.py — подключение к PostgreSQL (SQLAlchemy 2.x) для системы
психологического тестирования.

Что реализовано:
  * чтение конфигурации из `.env` (DB_USER / DB_PASSWORD / DB_HOST / DB_PORT /
    DB_NAME / DB_DRIVER) либо единый `DATABASE_URL`;
  * `engine` — пул соединений с `pool_pre_ping`, `pool_recycle` и таймаутом
    подключения; для каждого соединения выставляется `statement_timeout`;
  * `SessionLocal` — фабрика сессий ORM;
  * `Base` — декларативная база с конвенцией именования ограничений
    (pk_ / uq_ / ck_ / fk_ / ix_ — читаемые логи PostgreSQL и Alembic);
  * `get_db()` — FastAPI-зависимость с гарантированным закрытием сессии;
  * `session_scope()` — контекстный менеджер для скриптов, Celery и Telegram-бота;
  * `init_db()` / `drop_db()` — создание и удаление схемы по моделям;
  * аварийный fallback на SQLite (`DB_ALLOW_SQLITE_FALLBACK=1`) — чтобы проект
    запускался без развёрнутого PostgreSQL на этапе разработки.

Переменные окружения (см. `.env`):

    DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db
    DB_USER=postgres
    DB_PASSWORD=pass
    DB_HOST=127.0.0.1
    DB_PORT=5432
    DB_NAME=postgres
    DB_DRIVER=psycopg2            # psycopg2 (sync) | psycopg (v3)
    DB_POOL_SIZE=10
    DB_MAX_OVERFLOW=20
    DB_POOL_TIMEOUT=30
    DB_POOL_RECYCLE=1800
    DB_POOL_PRE_PING=1
    DB_CONNECT_TIMEOUT=5
    DB_STATEMENT_TIMEOUT_MS=30000
    DB_ECHO=0
    DB_ALLOW_SQLITE_FALLBACK=1
    DB_SQLITE_PATH=survey_fallback.db

Использование в FastAPI:

    from fastapi import Depends
    from sqlalchemy.orm import Session

    from database import get_db
    from models import Test

    @app.get("/api/tests")
    def list_tests(db: Session = Depends(get_db)):
        return db.query(Test).all()

Создание таблиц при старте:

    python -c "import database; database.init_db()"
"""

"""
database.py — подключение к PostgreSQL (SQLAlchemy 2.x) для системы
психологического тестирования.
"""

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()

logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Конвенция именования ограничений PostgreSQL:
#   pk_tests, uq_tests_code, fk_tests_created_by_users, ck_users_xxx, ix_users_login
# ---------------------------------------------------------------------------
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Декларативная база для всех ORM-моделей проекта."""

    __allow_unmapped__ = True  # Разрешаем легаси/прямые аннотации типов без Mapped[]
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def to_dict(self, exclude: tuple[str, ...] = ()) -> dict:
        """Представление строки БД как словаря (API, отчёты, логи)."""
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
            if column.name not in exclude
        }

    def __repr__(self) -> str:  # pragma: no cover — удобно в отладке
        pk = getattr(self, "id", None)
        return f"<{self.__class__.__name__} id={pk}>"


# ---------------------------------------------------------------------------
# Конфигурация подключения
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "да"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Некорректное значение env %s=%r, беру по умолчанию %s", name, raw, default)
        return default


@dataclass(frozen=True)
class DBSettings:
    """Параметры подключения к базе данных."""

    user: str = "postgres"
    password: str = "postgres"
    host: str = "127.0.0.1"
    port: int = 5432
    name: str = "postgres"
    driver: str = "psycopg2"            # psycopg2 (sync) | psycopg (v3)
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30
    pool_recycle: int = 1800
    pool_pre_ping: bool = True
    connect_timeout: int = 5
    statement_timeout_ms: int = 30_000
    echo: bool = False
    allow_sqlite_fallback: bool = True
    sqlite_path: str = "survey_fallback.db"

    @property
    def url(self) -> URL:
        """URL подключения, собранный из параметров DB_*."""
        return URL.create(
            drivername=f"postgresql+{self.driver}",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.name,
        )


@lru_cache(maxsize=1)
def get_settings() -> DBSettings:
    """Настройки БД из переменных окружения (результат кэшируется)."""
    return DBSettings(
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres"),
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=_env_int("DB_PORT", 5432),
        name=os.getenv("DB_NAME", "postgres"),
        driver=os.getenv("DB_DRIVER", "psycopg2"),
        pool_size=_env_int("DB_POOL_SIZE", 10),
        max_overflow=_env_int("DB_MAX_OVERFLOW", 20),
        pool_timeout=_env_int("DB_POOL_TIMEOUT", 30),
        pool_recycle=_env_int("DB_POOL_RECYCLE", 1800),
        pool_pre_ping=_env_bool("DB_POOL_PRE_PING", True),
        connect_timeout=_env_int("DB_CONNECT_TIMEOUT", 5),
        statement_timeout_ms=_env_int("DB_STATEMENT_TIMEOUT_MS", 30_000),
        echo=_env_bool("DB_ECHO", False),
        allow_sqlite_fallback=_env_bool("DB_ALLOW_SQLITE_FALLBACK", True),
        sqlite_path=os.getenv("DB_SQLITE_PATH", "survey_fallback.db"),
    )


def get_database_url() -> str:
    """Итоговый URL: `DATABASE_URL` имеет приоритет над параметрами DB_*."""
    return os.getenv("DATABASE_URL") or get_settings().url.render_as_string(hide_password=False)


# ---------------------------------------------------------------------------
# Создание Engine
# ---------------------------------------------------------------------------


def _register_postgres_listeners(engine: Engine) -> None:
    """На каждом новом соединении выставляем UTC и таймаут запроса."""

    @event.listens_for(engine, "connect")
    def _set_session_params(dbapi_connection, connection_record) -> None:  # pragma: no cover
        try:
            with dbapi_connection.cursor() as cursor:
                cursor.execute("SET TIME ZONE 'UTC'")
                timeout = get_settings().statement_timeout_ms
                if timeout > 0:
                    cursor.execute(f"SET statement_timeout = {int(timeout)}")
        except Exception as exc:  # pragma: no cover
            logger.debug("Не удалось применить параметры сессии PostgreSQL: %r", exc)


def _register_sqlite_listeners(engine: Engine) -> None:
    """Включаем внешние ключи SQLite (иначе ON DELETE CASCADE не работает)."""

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record) -> None:  # pragma: no cover
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        except Exception as exc:  # pragma: no cover
            logger.debug("Не удалось включить PRAGMA foreign_keys: %r", exc)


def _create_engine(url: str) -> Engine:
    """Создаёт Engine с параметрами, подходящими для конкретного драйвера."""
    settings = get_settings()
    is_sqlite = url.startswith("sqlite")

    connect_args: dict = {}
    engine_kwargs: dict = {"echo": settings.echo}

    if is_sqlite:
        connect_args["check_same_thread"] = False
    else:
        connect_args["connect_timeout"] = settings.connect_timeout
        engine_kwargs.update(
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_timeout=settings.pool_timeout,
            pool_recycle=settings.pool_recycle,
            pool_pre_ping=settings.pool_pre_ping,
        )

    engine = create_engine(url, connect_args=connect_args, **engine_kwargs)
    _register_sqlite_listeners(engine) if is_sqlite else _register_postgres_listeners(engine)
    return engine


def _check_connection(engine: Engine) -> None:
    """Проверка живости соединения — иначе ошибка всплывёт только на первом запросе."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def _init_engine() -> Engine:
    """Основной engine к PostgreSQL; при недоступности — fallback на SQLite."""
    settings = get_settings()
    url = get_database_url()

    try:
        engine = _create_engine(url)
        _check_connection(engine)
        logger.info("Подключение к PostgreSQL успешно: %s", engine.url.render_as_string(hide_password=True))
        return engine
    except Exception as exc:  # pragma: no cover
        if not settings.allow_sqlite_fallback or url.startswith("sqlite"):
            logger.error("Не удалось подключиться к БД (%s): %r", url, exc)
            raise
        fallback_url = f"sqlite:///./{settings.sqlite_path}"
        logger.warning(
            "PostgreSQL недоступен (%r). Резервная база SQLite: %s. "
            "Для продакшена установите DB_ALLOW_SQLITE_FALLBACK=0.",
            exc,
            fallback_url,
        )
        engine = _create_engine(fallback_url)
        _check_connection(engine)
        return engine


#: Основной Engine приложения.
engine: Engine = _init_engine()

#: True — работаем с PostgreSQL, False — аварийный SQLite.
IS_POSTGRESQL: bool = not engine.dialect.name.startswith("sqlite")

#: Имя диалекта ("postgresql" / "sqlite").
DB_DIALECT: str = engine.dialect.name

#: URL подключения (для /health и логов; пароль скрывается в логах).
DATABASE_URL: str = engine.url.render_as_string(hide_password=False)

#: Фабрика сессий ORM.
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


# ---------------------------------------------------------------------------
# Работа с сессиями
# ---------------------------------------------------------------------------


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: выдаёт сессию и гарантированно её закрывает."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Сессия с commit/rollback для скриптов, Celery-тасков и Telegram-бота."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Управление схемой
# ---------------------------------------------------------------------------


def init_db(create_tables: bool = True, echo: bool = True) -> None:
    """Регистрирует модели и создаёт все таблицы по моделям."""
    import models  # noqa: F401

    if create_tables:
        Base.metadata.create_all(bind=engine, checkfirst=True)
        if echo:
            logger.info("Схема БД синхронизирована. Таблиц в проекте: %d", len(Base.metadata.tables))


def drop_db() -> None:
    """Удаляет все таблицы (только для dev/test-окружения!)."""
    import models  # noqa: F401

    Base.metadata.drop_all(bind=engine, checkfirst=True)
    logger.warning("Все таблицы базы данных удалены.")


def dispose_engine() -> None:
    """Закрывает пул соединений (graceful shutdown)."""
    engine.dispose()
    logger.info("Пул соединений с БД закрыт.")


def ping() -> bool:
    """Быстрая проверка доступности БД — для эндпоинта /health."""
    try:
        _check_connection(engine)
        return True
    except SQLAlchemyError:
        return False


__all__ = [
    "Base",
    "DBSettings",
    "DATABASE_URL",
    "DB_DIALECT",
    "IS_POSTGRESQL",
    "SessionLocal",
    "dispose_engine",
    "drop_db",
    "engine",
    "get_database_url",
    "get_db",
    "get_settings",
    "init_db",
    "ping",
    "session_scope",
]