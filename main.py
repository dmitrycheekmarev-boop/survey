"""
main.py — Backend (FastAPI) конструктора опросников.

Что реализовано:
  * Инициализация схемы БД при старте приложения (lifespan) + сид ролей,
    прав и стартовых пользователей;
  * Сессионная авторизация (cookie-сессия через SessionMiddleware);
  * REST API:
      - аутентификация: login / logout / me;
      - тесты и версии: CRUD, создание вопросов, вариантов, ключей и шкал;
      - последовательности (наборы тестов) и назначение их студентам;
      - прохождение тестов: старт попытки, сохранение ответов, завершение;
      - статистика: по тесту и по попытке;
      - служебные: /health, журнал аудита, список пользователей;
  * Отдача HTML-страниц через
        templates.TemplateResponse(request=request, name="...")
  * Автоматический расчёт результатов (sum / average / count / composite),
    нормализация и интерпретация баллов;
  * Ведение журнала аудита (audit_log).

Управление версиями тестов (см. раздел 8):
    GET  /api/tests/{test_id}/versions   — список всех версий теста;
    POST /api/tests/{test_id}/versions   — создать версию (version_number,
                                           comment, instruction, publish,
                                           copy_from_version_id);
    GET  /api/versions/{version_id}      — полная информация о версии
                                           (структура, шкалы, вопросы).

Редактор вопросов и шкал (см. раздел 8):
    GET    /api/versions/{version_id}/scales    — список шкал версии;
    POST   /api/versions/{version_id}/scales    — создать шкалу (с формулой
                                                  составной шкалы);
    PUT    /api/scales/{scale_id}               — обновить шкалу;
    PATCH  /api/scales/{scale_id}               — частично обновить шкалу;
    DELETE /api/scales/{scale_id}               — удалить шкалу;
    GET    /api/versions/{version_id}/questions — список вопросов версии;
    POST   /api/versions/{version_id}/questions — создать вопрос с вариантами
                                                  ответа и ключами;
    PUT    /api/questions/{question_id}         — обновить вопрос целиком;
    PATCH  /api/questions/{question_id}         — частично обновить вопрос
                                                  (текст, тип, варианты);
    DELETE /api/questions/{question_id}         — удалить вопрос.

Запуск:
    uvicorn main:app --reload
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

import database
import models
from database import get_db, init_db
from models import (
    PERMISSIONS,
    ROLE_ADMIN,
    ROLE_METHODIST,
    ROLE_STUDENT,
    AnswerOption,
    Attempt,
    AttemptAnswer,
    AttemptAnswerOption,
    AttemptStatus,
    AuditLog,
    ManualAnswerScore,
    OptionScore,
    Permission,
    ProgressStatus,
    Question,
    QuestionType,
    Role,
    Scale,
    ScaleDependency,
    ScaleOperation,
    ScaleType,
    SequenceItem,
    Test,
    TestSequence,
    TestVersion,
    User,
    UserSequence,
    UserTestProgress,
    utcnow,
)

# ===========================================================================
# 0. УТИЛИТЫ: ПАРОЛИ, ВРЕМЯ, СЕРИАЛИЗАЦИЯ
# ===========================================================================

# --- Хеширование паролей: безопасный fallback при проблемах с passlib/bcrypt ---
_PBKDF2_ITERATIONS = 120_000

def _pbkdf2_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"

def _pbkdf2_verify(password: str, password_hash: str) -> bool:
    try:
        algo, iterations, salt, digest = password_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
        return hmac.compare_digest(check.hex(), digest)
    except Exception:
        return False

try:
    from passlib.context import CryptContext
    _pwd_context = CryptContext(schemes=["bcrypt", "pbkdf2_sha256"], deprecated="auto")

    def hash_password(password: str) -> str:
        try:
            return _pwd_context.hash(password)
        except Exception:
            return _pbkdf2_hash(password)

    def verify_password(password: str, password_hash: str) -> bool:
        if password_hash.startswith("pbkdf2_sha256$"):
            return _pbkdf2_verify(password, password_hash)
        try:
            return _pwd_context.verify(password, password_hash)
        except Exception:
            return _pbkdf2_verify(password, password_hash)

except Exception:
    def hash_password(password: str) -> str:
        return _pbkdf2_hash(password)

    def verify_password(password: str, password_hash: str) -> bool:
        return _pbkdf2_verify(password, password_hash)


def dt(value: Optional[datetime]) -> Optional[str]:
    """ISO-представление даты (или None)."""
    return value.isoformat() if value else None


def num(value) -> Optional[float]:
    """Decimal/int -> float (или None) для JSON."""
    return None if value is None else float(value)


def log_action(
    db: Session,
    *,
    user_id: Optional[int],
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    details: Optional[dict] = None,
) -> None:
    """Добавляет запись в журнал аудита (без commit — коммитит вызывающий код)."""
    db.add(
        AuditLog.make(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )


# --- Сериализация ORM-объектов в словари ---


def user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "login": user.login,
        "full_name": user.full_name,
        "email": user.email,
        "is_active": user.is_active,
        "roles": user.role_codes,
        "permissions": sorted({p.code for r in user.roles for p in r.permissions}),
        "created_at": dt(user.created_at),
    }


def me_to_dict(user: User) -> dict:
    """Краткий профиль текущего пользователя: id, login, email, role."""
    return {
        "id": user.id,
        "login": user.login,
        "email": user.email,
        "role": user.get_primary_role(),
    }


def option_to_dict(option: AnswerOption, scores: Optional[dict] = None) -> dict:
    if scores is None:
        scores = {s.scale_id: num(s.score) for s in option.scores}
    return {
        "id": option.id,
        "order_num": option.order_num,
        "text": option.text,
        "raw_value": option.raw_value,
        "is_correct": option.is_correct,
        "scores": scores,
    }


def question_to_dict(question: Question, with_options: bool = True) -> dict:
    data = {
        "id": question.id,
        "order_num": question.order_num,
        "code": question.code,
        "text": question.text,
        "question_type": question.question_type,
        "is_required": question.is_required,
    }
    if with_options:
        data["options"] = [option_to_dict(o) for o in question.answer_options]
    return data


def scale_to_dict(scale: Scale, with_dependencies: bool = True) -> dict:
    data = {
        "id": scale.id,
        "code": scale.code,
        "name": scale.name,
        "scale_type": scale.scale_type,
        "min_score": num(scale.min_score),
        "max_score": num(scale.max_score),
        "description": scale.description,
        "interpretation": scale.interpretation,
        "is_composite": scale.is_composite,
        "formula": [d.formula_part for d in scale.composite_dependencies],
    }
    if with_dependencies and scale.is_composite:
        data["dependencies"] = [
            {
                "component_scale_id": d.component_scale_id,
                "component_code": d.component_scale.code,
                "coefficient": num(d.coefficient),
                "operation": d.operation,
            }
            for d in scale.composite_dependencies
        ]
    return data


def version_to_dict(version: TestVersion, with_content: bool = False) -> dict:
    """Краткое представление версии (без вопросов/шкал, если with_content=False)."""
    data = {
        "id": version.id,
        "test_id": version.test_id,
        "version_no": version.version_no,
        "version_number": version.version_no,
        "comment": version.comment,
        "is_published": version.is_published,
        "status": version.status_label,
        "instruction": version.instruction,
        "question_count": version.question_count,
        "scale_count": len(version.scales),
        "created_by": version.created_by,
        "created_at": dt(version.created_at),
        "published_at": dt(version.published_at),
    }
    if with_content:
        data["scales"] = [scale_to_dict(s) for s in version.scales]
        data["questions"] = [question_to_dict(q) for q in version.questions]
    return data


def version_detail_to_dict(version: TestVersion) -> dict:
    """Полная информация о версии: базовые настройки, структура, шкалы, вопросы.

    Используется роутом GET /api/versions/{version_id}.
    """
    data = version_to_dict(version, with_content=True)

    # Сводка по структуре теста — удобно для методиста и фронтенда.
    by_type: dict[str, int] = {}
    for q in version.questions:
        by_type[q.question_type] = by_type.get(q.question_type, 0) + 1

    data["structure"] = {
        "questions_total": len(version.questions),
        "questions_required": sum(1 for q in version.questions if q.is_required),
        "questions_optional": sum(1 for q in version.questions if not q.is_required),
        "questions_by_type": by_type,
        "scales_total": len(version.scales),
        "has_open_text": any(q.is_open_text for q in version.questions),
        "is_ready": version.is_ready,
    }
    data["test"] = {
        "id": version.test.id,
        "code": version.test.code,
        "title": version.test.title,
        "description": version.test.description,
        "is_active": version.test.is_active,
    }
    data["author"] = {
        "id": version.author.id,
        "login": version.author.login,
        "full_name": version.author.full_name,
    } if version.author else None
    return data


def test_to_dict(test: Test, with_versions: bool = False) -> dict:
    published = test.published_version
    data = {
        "id": test.id,
        "code": test.code,
        "title": test.title,
        "description": test.description,
        "is_active": test.is_active,
        "created_by": test.created_by,
        "created_at": dt(test.created_at),
        "published_version": version_to_dict(published) if published else None,
        "versions_count": len(test.versions),
    }
    if with_versions:
        data["versions"] = [version_to_dict(v) for v in test.versions]
    return data


def sequence_to_dict(sequence: TestSequence, with_items: bool = False) -> dict:
    data = {
        "id": sequence.id,
        "title": sequence.title,
        "description": sequence.description,
        "created_by": sequence.created_by,
        "created_at": dt(sequence.created_at),
        "tests_count": sequence.tests_count,
    }
    if with_items:
        data["items"] = [
            {
                "id": item.id,
                "test_id": item.test_id,
                "test_title": item.test.title,
                "order_num": item.order_num,
                "is_required": item.is_required,
            }
            for item in sequence.items
        ]
    return data


def assignment_to_dict(us: UserSequence) -> dict:
    return {
        "id": us.id,
        "user_id": us.user_id,
        "user_name": us.user.full_name,
        "sequence_id": us.sequence_id,
        "sequence_title": us.sequence.title,
        "status": us.status,
        "progress_percent": us.progress_percent,
        "assigned_at": dt(us.assigned_at),
    }


def progress_to_dict(progress: UserTestProgress) -> dict:
    return {
        "id": progress.id,
        "test_id": progress.test_id,
        "test_title": progress.test.title,
        "status": progress.status,
        "attempt_id": progress.attempt_id,
        "completed_at": dt(progress.completed_at),
    }


def attempt_to_dict(attempt: Attempt, with_answers: bool = False) -> dict:
    data = {
        "id": attempt.id,
        "user_id": attempt.user_id,
        "version_id": attempt.version_id,
        "test_id": attempt.version.test_id,
        "test_title": attempt.version.test.title,
        "sequence_id": attempt.sequence_id,
        "status": attempt.status,
        "started_at": dt(attempt.started_at),
        "finished_at": dt(attempt.finished_at),
        "total_score": num(attempt.total_score),
        "duration_seconds": attempt.duration_seconds,
    }
    if with_answers:
        data["answers"] = [
            {
                "question_id": a.question_id,
                "answer_text": a.answer_text,
                "selected_option_ids": a.selected_option_ids,
            }
            for a in attempt.answers
        ]
    return data


def scale_result_to_dict(result) -> dict:
    return {
        "scale_id": result.scale_id,
        "scale_code": result.scale.code,
        "scale_name": result.scale.name,
        "raw_score": num(result.raw_score),
        "normalized_score": num(result.normalized_score),
        "interpretation": result.interpretation,
        "level": result.level,
    }


# ===========================================================================
# 1. PYDANTIC-СХЕМЫ (тела запросов API)
# ===========================================================================


class LoginRequest(BaseModel):
    login: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class RegisterRequest(BaseModel):
    login: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=4)
    full_name: str = Field(..., min_length=1, max_length=200)
    email: Optional[str] = None


class TestCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class TestUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class VersionCreate(BaseModel):
    """Тело запроса на создание новой версии теста.

    Все поля опциональны:
      * version_number — явный номер версии (если не задан — берётся следующий);
      * comment        — комментарий/причина создания версии;
      * instruction    — инструкция для студента;
      * publish        — опубликовать версию сразу после создания;
      * copy_from_version_id — скопировать структуру (шкалы, вопросы,
                               варианты и ключи) из указанной версии этого же теста.
    """

    version_number: Optional[int] = Field(default=None, ge=1, description="Явный номер версии")
    comment: Optional[str] = Field(default=None, description="Комментарий к версии")
    instruction: Optional[str] = Field(default=None, description="Инструкция для студента")
    publish: bool = Field(default=False, description="Опубликовать сразу после создания")
    copy_from_version_id: Optional[int] = Field(
        default=None, description="Скопировать структуру из версии с этим id"
    )


class AnswerOptionIn(BaseModel):
    order_num: Optional[int] = None
    text: str
    raw_value: Optional[str] = None
    is_correct: Optional[bool] = None
    scores: dict[int, float] = Field(default_factory=dict)  # scale_id -> score


class QuestionCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    text: str
    question_type: QuestionType = QuestionType.SINGLE_CHOICE
    order_num: Optional[int] = None
    is_required: bool = True
    options: list[AnswerOptionIn] = Field(default_factory=list)


class AnswerOptionUpdate(BaseModel):
    """Обновление варианта ответа.

    Поле `id` — идентификатор существующего варианта. Если он задан и найден у
    вопроса, вариант обновляется на месте (сохраняются связи с историей попыток),
    иначе создаётся новый вариант. Поле `scores` (scale_id -> балл) полностью
    заменяет матрицу ключей варианта. Варианты, отсутствующие в списке, удаляются.
    """

    id: Optional[int] = None
    order_num: Optional[int] = None
    text: Optional[str] = None
    raw_value: Optional[str] = None
    is_correct: Optional[bool] = None
    scores: Optional[dict[int, float]] = None


class QuestionUpdate(BaseModel):
    """Частичное обновление вопроса (PUT/PATCH /api/questions/{question_id}).

    Все поля опциональны и применяются, только если переданы (exclude_unset).
    Поле `options` (если присутствует) синхронизирует варианты ответов.
    """

    code: Optional[str] = Field(default=None, min_length=1, max_length=50)
    text: Optional[str] = None
    question_type: Optional[QuestionType] = None
    order_num: Optional[int] = None
    is_required: Optional[bool] = None
    options: Optional[list[AnswerOptionUpdate]] = None


class ScaleDependencyIn(BaseModel):
    """Зависимость составной шкалы: component_scale_id (operation) coefficient."""

    component_scale_id: int
    coefficient: float = 1.0
    operation: ScaleOperation = ScaleOperation.ADD


class ScaleCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    name: str
    scale_type: ScaleType = ScaleType.SUM
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    description: Optional[str] = None
    interpretation: Optional[str] = None
    dependencies: list[ScaleDependencyIn] = Field(
        default_factory=list, description="Формула для составной (composite) шкалы"
    )


class ScaleUpdate(BaseModel):
    """Обновление шкалы (PUT/PATCH /api/scales/{scale_id})."""

    name: Optional[str] = None
    scale_type: Optional[ScaleType] = None
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    description: Optional[str] = None
    interpretation: Optional[str] = None
    dependencies: Optional[list[ScaleDependencyIn]] = None


class SequenceCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    test_ids: list[int] = Field(default_factory=list)  # порядок = порядок тестов


class AssignRequest(BaseModel):
    user_ids: list[int] = Field(..., min_length=1)


class StartAttemptRequest(BaseModel):
    test_id: Optional[int] = None
    version_id: Optional[int] = None
    sequence_id: Optional[int] = None


class AnswerSubmit(BaseModel):
    question_id: int
    option_ids: list[int] = Field(default_factory=list)
    text: Optional[str] = None


class AttemptSubmit(BaseModel):
    answers: list[AnswerSubmit] = Field(default_factory=list)
    finish: bool = True


class ManualScoreIn(BaseModel):
    attempt_answer_id: int
    scale_id: int
    score: float
    comment: Optional[str] = None


# ===========================================================================
# 2. ЖИЗНЕННЫЙ ЦИКЛ ПРИЛОЖЕНИЯ (lifespan)
# ===========================================================================


def seed_defaults(db: Session) -> None:
    """Создаёт роли, права и стартовых пользователей (идемпотентно)."""
    # --- Права ---
    perm_map: dict[str, Permission] = {}
    for code, name in PERMISSIONS.items():
        perm = db.query(Permission).filter(Permission.code == code).first()
        if not perm:
            perm = Permission(code=code, name=name)
            db.add(perm)
            db.flush()
        perm_map[code] = perm

    # --- Роли ---
    role_perms = {
        ROLE_ADMIN: list(PERMISSIONS.keys()),
        ROLE_METHODIST: [
            "tests.view",
            "tests.edit",
            "tests.publish",
            "sequences.edit",
            "assign.manage",
            "attempts.take",
            "results.view.own",
            "results.view.all",
            "results.manual",
            "audit.view",
        ],
        ROLE_STUDENT: ["tests.view", "attempts.take", "results.view.own"],
    }
    role_names = {
        ROLE_ADMIN: "Администратор",
        ROLE_METHODIST: "Методист",
        ROLE_STUDENT: "Студент",
    }

    role_map: dict[str, Role] = {}
    for code, name in role_names.items():
        role = db.query(Role).filter(Role.code == code).first()
        if not role:
            role = Role(code=code, name=name)
            db.add(role)
            db.flush()
        have = {p.code for p in role.permissions}
        for perm_code in role_perms[code]:
            if perm_code not in have:
                role.permissions.append(perm_map[perm_code])
        role_map[code] = role

    # --- Пользователи ---
    default_users = [
        ("admin", "admin123", "Администратор системы", ROLE_ADMIN),
        ("methodist", "methodist123", "Мария Методист", ROLE_METHODIST),
        ("student", "student123", "Иван Студентов", ROLE_STUDENT),
    ]
    for login, password, full_name, role_code in default_users:
        user = db.query(User).filter(User.login == login).first()
        if not user:
            user = User(
                login=login,
                password_hash=hash_password(password),
                full_name=full_name,
                is_active=True,
            )
            user.roles.append(role_map[role_code])
            db.add(user)

    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация схемы БД и стартовых данных при запуске."""
    init_db(create_tables=True, echo=True)
    with database.session_scope() as db:
        seed_defaults(db)
    yield
    database.dispose_engine()


# ===========================================================================
# 3. ПРИЛОЖЕНИЕ, MIDDLEWARE, ШАБЛОНЫ, СТАТИКА
# ===========================================================================

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-to-random-string")

#: Имя сессионной cookie (используется и для очистки при выходе).
SESSION_COOKIE_NAME = "survey_session"

app = FastAPI(
    title="Конструктор опросников",
    description="FastAPI + SQLAlchemy/PostgreSQL + Jinja2 — система психологического тестирования.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie=SESSION_COOKIE_NAME,
    max_age=60 * 60 * 12,  # 12 часов
    same_site="lax",
    https_only=False,
)

templates = Jinja2Templates(directory="templates")

# Статические файлы (директория создаётся, если её нет).
STATIC_DIR = "static"
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ===========================================================================
# 4. ЗАВИСИМОСТИ АВТОРИЗАЦИИ
# ===========================================================================


def _session_user(request: Request, db: Session) -> Optional[User]:
    """Пользователь из сессии или None."""
    uid = request.session.get("user_id")
    if not uid:
        return None
    user = db.get(User, uid)
    if user and not user.is_active:
        return None
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Обязательная авторизация: возвращает User или выбрасывает 401."""
    user = _session_user(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")
    return user


def require_roles(*codes: str):
    """Фабрика зависимостей: доступ только для указанных ролей."""

    def _dep(user: User = Depends(get_current_user)) -> User:
        if not any(user.has_role(code) for code in codes):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
        return user

    return _dep


def require_permission(code: str):
    """Фабрика зависимостей: доступ по конкретному праву."""

    def _dep(user: User = Depends(get_current_user)) -> User:
        if not user.has_permission(code):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Нужно право: {code}")
        return user

    return _dep


def require_staff(user: User = Depends(get_current_user)) -> User:
    """Сотрудник (администратор или методист)."""
    if not (user.has_role(ROLE_ADMIN) or user.has_role(ROLE_METHODIST)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступно только персоналу")
    return user


def render(request: Request, name: str, **context):
    """Хелпер отдачи страницы: TemplateResponse(request=request, name=...)."""
    context.setdefault("user", None)
    return templates.TemplateResponse(request=request, name=name, context=context)


# ===========================================================================
# 5. СЛУЖЕБНЫЕ ЭНДПОИНТЫ
# ===========================================================================


@app.get("/health", tags=["service"])
def health() -> JSONResponse:
    """Проверка живости приложения и БД."""
    ok = database.ping()
    return JSONResponse(
        {
            "status": "ok" if ok else "degraded",
            "database": "up" if ok else "down",
            "dialect": database.DB_DIALECT,
            "time": dt(utcnow()),
        },
        status_code=200 if ok else 503,
    )


# ===========================================================================
# 6. API: АУТЕНТИФИКАЦИЯ И СЕССИИ
# ===========================================================================


@app.post("/api/auth/login", tags=["auth"])
def api_login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Вход по логину/паролю. Пишет сессию и возвращает профиль с редиректом."""
    user = db.query(User).filter(User.login == payload.login).first()
    if not user or not verify_password(payload.password, user.password_hash):
        log_action(db, user_id=None, action="login_failed", entity_type="user", details={"login": payload.login})
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный логин или пароль")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Учётная запись заблокирована")

    request.session["user_id"] = user.id
    role = user.get_primary_role()
    redirect_map = {ROLE_ADMIN: "/dashboard", ROLE_METHODIST: "/dashboard", ROLE_STUDENT: "/student"}
    log_action(db, user_id=user.id, action="login", entity_type="user", entity_id=user.id)
    db.commit()
    return {"ok": True, "user": user_to_dict(user), "redirect": redirect_map.get(role, "/dashboard")}


@app.post("/api/auth/logout", tags=["auth"])
def api_logout(request: Request, db: Session = Depends(get_db)):
    """Выход из системы (POST). Очищает сессию и возвращает {"ok": true}."""
    _logout_session(request, db)
    return {"ok": True}


@app.get("/api/auth/logout", tags=["auth"])
def api_logout_get(request: Request, db: Session = Depends(get_db)):
    """Выход из системы (GET) — удобен для ссылок/переходов в браузере."""
    _logout_session(request, db)
    return {"ok": True}


@app.get("/logout", tags=["auth"], include_in_schema=False)
def page_logout(request: Request, db: Session = Depends(get_db)):
    """Выход с очисткой сессии/куки и редиректом на страницу входа."""
    _logout_session(request, db)
    response = RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    # Явно удаляем cookie сессии (в дополнение к request.session.clear()).
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/api/auth/me", tags=["auth"])
def api_me(user: User = Depends(get_current_user)):
    """Текущий авторизованный пользователь (полный профиль с ролями и правами)."""
    return user_to_dict(user)


@app.get("/api/me", tags=["auth"])
def api_me_short(request: Request, db: Session = Depends(get_db)):
    """Текущий авторизованный пользователь: id, login, email, role.

    Данные берутся из сессионной cookie (SessionMiddleware). Если пользователь
    не авторизован — 401.
    """
    user = _session_user(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")
    return me_to_dict(user)


def _logout_session(request: Request, db: Session) -> None:
    """Общая логика выхода: аудит + очистка сессии."""
    uid = request.session.get("user_id")
    if uid:
        log_action(db, user_id=uid, action="logout", entity_type="user", entity_id=uid)
        db.commit()
    request.session.clear()


@app.post("/api/auth/register", tags=["auth"])
def api_register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Регистрация нового студента (публичный эндпоинт)."""
    if db.query(User).filter(User.login == payload.login).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Логин уже занят")
    student_role = db.query(Role).filter(Role.code == ROLE_STUDENT).first()
    user = User(
        login=payload.login,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        email=payload.email,
        is_active=True,
    )
    if student_role:
        user.roles.append(student_role)
    db.add(user)
    db.flush()
    log_action(db, user_id=user.id, action="register", entity_type="user", entity_id=user.id)
    db.commit()
    return {"ok": True, "user": user_to_dict(user)}


# ===========================================================================
# 7. API: КАТАЛОГ ТЕСТОВ
# ===========================================================================


@app.get("/api/tests", tags=["tests"])
def list_tests(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    only_published: bool = False,
):
    """Список тестов. Студенты видят только опубликованные."""
    query = db.query(Test)
    staff = user.has_role(ROLE_ADMIN) or user.has_role(ROLE_METHODIST)
    if not staff:
        only_published = True
    if only_published:
        query = query.filter(Test.is_active.is_(True))
    tests = query.order_by(Test.title).all()
    return [test_to_dict(t) for t in tests]


@app.post("/api/tests", tags=["tests"], status_code=status.HTTP_201_CREATED)
def create_test(
    payload: TestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tests.edit")),
):
    """Создать тест (методист/администратор)."""
    if db.query(Test).filter(Test.code == payload.code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Тест с таким кодом уже существует")
    test = Test(
        code=payload.code,
        title=payload.title,
        description=payload.description,
        created_by=user.id,
    )
    db.add(test)
    db.flush()
    log_action(db, user_id=user.id, action="create", entity_type="test", entity_id=test.id,
               details={"code": test.code})
    db.commit()
    return test_to_dict(test, with_versions=True)


@app.get("/api/tests/{test_id}", tags=["tests"])
def get_test(
    test_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Карточка теста со всеми версиями."""
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тест не найден")
    return test_to_dict(test, with_versions=True)


@app.put("/api/tests/{test_id}", tags=["tests"])
def update_test(
    test_id: int,
    payload: TestUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tests.edit")),
):
    """Обновить тест."""
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тест не найден")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(test, key, value)
    log_action(db, user_id=user.id, action="update", entity_type="test", entity_id=test.id, details=changes)
    db.commit()
    return test_to_dict(test)


@app.delete("/api/tests/{test_id}", tags=["tests"])
def delete_test(
    test_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("system.manage")),
):
    """Удалить тест (только администратор)."""
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тест не найден")
    db.delete(test)
    log_action(db, user_id=user.id, action="delete", entity_type="test", entity_id=test_id)
    db.commit()
    return {"ok": True}


# ===========================================================================
# 8. API: ВЕРСИИ, ШКАЛЫ, ВОПРОСЫ (редактор теста)
# ===========================================================================


# --- Общие помощники редактора версии --------------------------------------


def _get_version_or_404(db: Session, version_id: int) -> TestVersion:
    """Возвращает версию по id или выбрасывает 404."""
    version = db.get(TestVersion, version_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Версия не найдена")
    return version


def _validate_score_range(min_score: Optional[float], max_score: Optional[float]) -> None:
    """Диапазон шкалы должен быть корректным: min_score <= max_score."""
    if min_score is not None and max_score is not None:
        if Decimal(str(min_score)) > Decimal(str(max_score)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="min_score не может быть больше max_score",
            )


def _validate_option_orders(options: Iterable) -> None:
    """Проверяет уникальность номеров порядка вариантов ответа.

    Защищает от IntegrityError на UNIQUE(question_id, order_num): явные
    дубликаты order_num отклоняются с понятным сообщением (HTTP 400).
    """
    orders = [
        (opt.order_num if getattr(opt, "order_num", None) is not None else idx)
        for idx, opt in enumerate(options, start=1)
    ]
    if len(set(orders)) != len(orders):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Дублирующиеся номера порядка в вариантах ответа",
        )


def _next_version_no(db: Session, test_id: int) -> int:
    """Следующий свободный номер версии теста."""
    last = (
        db.query(TestVersion)
        .filter(TestVersion.test_id == test_id)
        .order_by(TestVersion.version_no.desc())
        .first()
    )
    return (last.version_no + 1) if last else 1


def _copy_version_structure(db: Session, source: TestVersion, target: TestVersion) -> None:
    """Копирует шкалы, вопросы, варианты и ключи из одной версии в другую."""
    # 1) Шкалы (сохраняем соответствие source_scale_id -> target_scale).
    scale_map: dict[int, Scale] = {}
    for src_scale in source.scales:
        new_scale = Scale(
            version_id=target.id,
            code=src_scale.code,
            name=src_scale.name,
            description=src_scale.description,
            scale_type=src_scale.scale_type,
            min_score=src_scale.min_score,
            max_score=src_scale.max_score,
            interpretation=src_scale.interpretation,
        )
        db.add(new_scale)
        db.flush()
        scale_map[src_scale.id] = new_scale

    # 2) Вопросы, варианты и ключи (option_scores).
    for src_q in source.questions:
        new_q = Question(
            version_id=target.id,
            order_num=src_q.order_num,
            code=src_q.code,
            text=src_q.text,
            question_type=src_q.question_type,
            is_required=src_q.is_required,
        )
        db.add(new_q)
        db.flush()
        for src_o in src_q.answer_options:
            new_o = AnswerOption(
                question_id=new_q.id,
                order_num=src_o.order_num,
                text=src_o.text,
                raw_value=src_o.raw_value,
                is_correct=src_o.is_correct,
            )
            db.add(new_o)
            db.flush()
            for osc in src_o.scores:
                if osc.scale_id in scale_map:
                    db.add(
                        OptionScore(
                            option_id=new_o.id,
                            scale_id=scale_map[osc.scale_id].id,
                            score=osc.score,
                        )
                    )
    db.flush()


def _sync_scale_dependencies(
    db: Session,
    scale: Scale,
    deps_in: Iterable[ScaleDependencyIn],
    valid_component_ids: set[int],
) -> None:
    """Синхронизирует формулу составной шкалы (полная замена зависимостей)."""
    # Удаляем старые зависимости.
    for old in list(scale.composite_dependencies):
        db.delete(old)
    db.flush()

    seen: set[int] = set()
    for dep in deps_in:
        component_id = dep.component_scale_id
        if component_id == scale.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Составная шкала не может зависеть от самой себя",
            )
        if component_id not in valid_component_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Компонентная шкала {component_id} не принадлежит этой версии",
            )
        if component_id in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Дублирующаяся зависимость в формуле шкалы",
            )
        seen.add(component_id)
        db.add(
            ScaleDependency(
                composite_scale_id=scale.id,
                component_scale_id=component_id,
                coefficient=Decimal(str(dep.coefficient)),
                operation=dep.operation.value,
            )
        )
    db.flush()


def _sync_question_options(
    db: Session,
    question: Question,
    options_in: list[AnswerOptionUpdate],
    valid_scale_ids: set[int],
) -> None:
    """Приводит варианты ответа вопроса к виду, описанному в `options_in`.

    * варианты с `id` обновляются на месте (история попыток сохраняется);
    * варианты без `id` создаются заново;
    * отсутствующие во входящем списке варианты удаляются;
    * `scores` полностью заменяет матрицу ключей варианта.
    """
    existing = {o.id: o for o in question.answer_options}
    incoming_ids = {o.id for o in options_in if o.id is not None}

    # 1) Удаляем варианты, которых нет во входящем списке.
    for option in [o for oid, o in existing.items() if oid not in incoming_ids]:
        if option.attempt_answer_options:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Нельзя удалить вариант, использованный в пройденных попытках",
            )
        db.delete(option)
        existing.pop(option.id, None)
    db.flush()

    # 2) Порядковые номера должны быть уникальны (иначе — конфликт в БД).
    final_orders = [opt.order_num or idx for idx, opt in enumerate(options_in, start=1)]
    if len(set(final_orders)) != len(final_orders):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Дублирующиеся номера порядка в вариантах ответа",
        )

    # 3) Временно «паркуем» оставшиеся варианты в отрицательные номера, чтобы
    #    перестановка order_num не нарушала уникальность (question_id, order_num).
    for idx, option in enumerate(existing.values(), start=1):
        option.order_num = -idx
    db.flush()

    # 4) Обновляем существующие / создаём новые варианты.
    final_options: list[tuple[AnswerOption, AnswerOptionUpdate]] = []
    for idx, opt_in in enumerate(options_in, start=1):
        order_num = opt_in.order_num or idx
        if opt_in.id is not None and opt_in.id in existing:
            option = existing[opt_in.id]
            if opt_in.text is not None:
                option.text = opt_in.text
            if opt_in.raw_value is not None:
                option.raw_value = opt_in.raw_value
            if opt_in.is_correct is not None:
                option.is_correct = opt_in.is_correct
            option.order_num = order_num
        else:
            if not opt_in.text:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="У нового варианта ответа должен быть непустой текст",
                )
            option = AnswerOption(
                question_id=question.id,
                order_num=order_num,
                text=opt_in.text,
                raw_value=opt_in.raw_value,
                is_correct=opt_in.is_correct,
            )
            db.add(option)
            db.flush()
        final_options.append((option, opt_in))

    db.flush()

    # 5) Ключи (option_scores) — полная замена для переданных вариантов.
    for option, opt_in in final_options:
        if opt_in.scores is None:
            continue
        for scale_id in opt_in.scores:
            if scale_id not in valid_scale_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Шкала {scale_id} не принадлежит версии теста",
                )
        for old in list(option.scores):
            db.delete(old)
        db.flush()
        for scale_id, score in opt_in.scores.items():
            db.add(OptionScore(option_id=option.id, scale_id=scale_id, score=Decimal(str(score))))
    db.flush()


@app.get("/api/tests/{test_id}/versions", tags=["versions"])
def list_versions(
    test_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Список всех версий данного теста (по возрастанию номера версии).

    Студент видит только опубликованные версии; персонал — все.
    """
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тест не найден")

    versions = sorted(test.versions, key=lambda v: v.version_no)
    staff = user.has_role(ROLE_ADMIN) or user.has_role(ROLE_METHODIST)
    if not staff:
        versions = [v for v in versions if v.is_published]

    return {
        "test_id": test.id,
        "test_title": test.title,
        "versions_count": len(versions),
        "versions": [version_to_dict(v) for v in versions],
    }


@app.post("/api/tests/{test_id}/versions", tags=["versions"], status_code=status.HTTP_201_CREATED)
def create_version(
    test_id: int,
    payload: VersionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tests.edit")),
):
    """Создать новую версию теста.

    Принимает:
      * version_number — явный номер версии (иначе — следующий свободный);
      * comment        — комментарий/причина создания версии;
      * instruction    — инструкция для студента;
      * publish        — сразу опубликовать версию;
      * copy_from_version_id — скопировать структуру из версии этого же теста.
    """
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тест не найден")

    # --- номер версии ---
    if payload.version_number is not None:
        version_no = payload.version_number
        exists = (
            db.query(TestVersion)
            .filter(TestVersion.test_id == test.id, TestVersion.version_no == version_no)
            .first()
        )
        if exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Версия с номером {version_no} уже существует",
            )
    else:
        version_no = _next_version_no(db, test.id)

    version = TestVersion(
        test_id=test.id,
        version_no=version_no,
        instruction=payload.instruction,
        comment=payload.comment,
        created_by=user.id,
    )
    db.add(version)
    db.flush()

    # --- копирование структуры из другой версии (опционально) ---
    if payload.copy_from_version_id is not None:
        source = db.get(TestVersion, payload.copy_from_version_id)
        if not source or source.test_id != test.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Исходная версия не найдена или относится к другому тесту",
            )
        _copy_version_structure(db, source, version)
        db.flush()
    elif payload.instruction is None:
        # Если структура не копируется — перенесём инструкцию последней версии.
        last = test.latest_version
        if last and last is not version:
            version.instruction = last.instruction

    # --- публикация (опционально) ---
    if payload.publish:
        if not version.is_ready:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Нельзя опубликовать пустую версию: нужны вопросы и хотя бы одна шкала",
            )
        version.publish()

    log_action(
        db,
        user_id=user.id,
        action="create",
        entity_type="test_version",
        entity_id=version.id,
        details={
            "test_id": test.id,
            "version_no": version.version_no,
            "comment": payload.comment,
            "copied_from": payload.copy_from_version_id,
            "published": version.is_published,
        },
    )
    db.commit()
    db.refresh(version)
    return version_detail_to_dict(version)


@app.get("/api/versions/{version_id}", tags=["versions"])
def get_version(
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Полная информация о версии: структура, шкалы (с формулами), вопросы и ключи."""
    version = _get_version_or_404(db, version_id)

    staff = user.has_role(ROLE_ADMIN) or user.has_role(ROLE_METHODIST)
    if not version.is_published and not staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Версия недоступна")

    return version_detail_to_dict(version)


@app.post("/api/versions/{version_id}/publish", tags=["versions"])
def publish_version(
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tests.publish")),
):
    """Опубликовать версию теста (должна быть готова: есть вопросы и шкалы)."""
    version = _get_version_or_404(db, version_id)
    if not version.is_ready:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Версия не готова: нужны вопросы и хотя бы одна шкала")
    version.publish()
    log_action(db, user_id=user.id, action="publish", entity_type="test_version", entity_id=version.id)
    db.commit()
    return version_to_dict(version, with_content=True)


# --- ШКАЛЫ -----------------------------------------------------------------


@app.get("/api/versions/{version_id}/scales", tags=["scales"])
def list_scales(
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_staff),
):
    """Список шкал версии (для редактора вопросов и шкал)."""
    version = _get_version_or_404(db, version_id)
    return [scale_to_dict(s) for s in version.scales]


@app.post("/api/versions/{version_id}/scales", tags=["scales"], status_code=status.HTTP_201_CREATED)
def create_scale(
    version_id: int,
    payload: ScaleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tests.edit")),
):
    """Создать/сохранить шкалу оценки в версии теста.

    Для составной (`composite`) шкалы можно сразу передать формулу в
    `dependencies`: [{component_scale_id, coefficient, operation}].
    """
    version = _get_version_or_404(db, version_id)
    if db.query(Scale).filter(Scale.version_id == version.id, Scale.code == payload.code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Шкала с таким кодом уже есть")

    _validate_score_range(payload.min_score, payload.max_score)

    scale = Scale(
        version_id=version.id,
        code=payload.code,
        name=payload.name,
        scale_type=payload.scale_type.value,
        min_score=payload.min_score,
        max_score=payload.max_score,
        description=payload.description,
        interpretation=payload.interpretation,
    )
    db.add(scale)
    db.flush()

    # Формула составной шкалы (если передана).
    if payload.scale_type == ScaleType.COMPOSITE and payload.dependencies:
        valid_components = {s.id for s in version.scales if s.id != scale.id}
        _sync_scale_dependencies(db, scale, payload.dependencies, valid_components)

    log_action(db, user_id=user.id, action="create", entity_type="scale", entity_id=scale.id,
               details={"version_id": version.id, "code": scale.code})
    db.commit()
    db.refresh(scale)
    return scale_to_dict(scale)


@app.put("/api/scales/{scale_id}", tags=["scales"])
@app.patch("/api/scales/{scale_id}", tags=["scales"])
def update_scale(
    scale_id: int,
    payload: ScaleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tests.edit")),
):
    """Обновить шкалу: название, тип, диапазон, интерпретацию, формулу.

    Поддерживается как полное (PUT), так и частичное (PATCH) обновление.
    Поле `dependencies` полностью заменяет формулу составной шкалы.
    """
    scale = db.get(Scale, scale_id)
    if not scale:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Шкала не найдена")

    changes = payload.model_dump(exclude_unset=True, exclude={"dependencies"})

    if changes.get("name") is not None:
        scale.name = changes["name"]
    if changes.get("scale_type") is not None:
        scale.scale_type = changes["scale_type"].value
    if "min_score" in changes:
        scale.min_score = changes["min_score"]
    if "max_score" in changes:
        scale.max_score = changes["max_score"]
    if "description" in changes:
        scale.description = changes["description"]
    if "interpretation" in changes:
        scale.interpretation = changes["interpretation"]

    _validate_score_range(scale.min_score, scale.max_score)

    valid_components = {s.id for s in scale.version.scales if s.id != scale.id}
    if payload.dependencies is not None:
        _sync_scale_dependencies(db, scale, payload.dependencies, valid_components)
    elif scale.scale_type != ScaleType.COMPOSITE.value:
        # Шкала перестала быть составной — формулы больше не нужны.
        _sync_scale_dependencies(db, scale, [], valid_components)

    db.flush()
    log_action(db, user_id=user.id, action="update", entity_type="scale", entity_id=scale.id,
               details={"version_id": scale.version_id})
    db.commit()
    db.refresh(scale)
    return scale_to_dict(scale)


@app.delete("/api/scales/{scale_id}", tags=["scales"])
def delete_scale(
    scale_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tests.edit")),
):
    """Удалить шкалу из версии теста."""
    scale = db.get(Scale, scale_id)
    if not scale:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Шкала не найдена")
    if scale.attempt_scale_results:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нельзя удалить шкалу: по ней уже есть результаты попыток",
        )
    db.delete(scale)
    log_action(db, user_id=user.id, action="delete", entity_type="scale", entity_id=scale_id)
    db.commit()
    return {"ok": True}


# --- ВОПРОСЫ ---------------------------------------------------------------


@app.get("/api/versions/{version_id}/questions", tags=["questions"])
def list_questions(
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_staff),
):
    """Список вопросов версии с вариантами ответа и ключами."""
    version = _get_version_or_404(db, version_id)
    return [question_to_dict(q) for q in version.questions]


@app.post("/api/versions/{version_id}/questions", tags=["questions"], status_code=status.HTTP_201_CREATED)
def create_question(
    version_id: int,
    payload: QuestionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tests.edit")),
):
    """Добавить вопрос с вариантами ответа и ключами (option_scores)."""
    version = _get_version_or_404(db, version_id)
    if db.query(Question).filter(Question.version_id == version.id, Question.code == payload.code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Вопрос с таким кодом уже есть")

    _validate_option_orders(payload.options)

    order_num = payload.order_num
    if order_num is None:
        order_num = (max((q.order_num for q in version.questions), default=0)) + 1
    elif any(q.order_num == order_num for q in version.questions):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Эта позиция уже занята другим вопросом",
        )

    valid_scale_ids = {s.id for s in version.scales}

    question = Question(
        version_id=version.id,
        order_num=order_num,
        code=payload.code,
        text=payload.text,
        question_type=payload.question_type.value,
        is_required=payload.is_required,
    )
    db.add(question)
    db.flush()

    for idx, opt_in in enumerate(payload.options, start=1):
        option = AnswerOption(
            question_id=question.id,
            order_num=opt_in.order_num or idx,
            text=opt_in.text,
            raw_value=opt_in.raw_value,
            is_correct=opt_in.is_correct,
        )
        db.add(option)
        db.flush()
        for scale_id, score in opt_in.scores.items():
            if scale_id not in valid_scale_ids:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Шкала {scale_id} не принадлежит версии теста")
            db.add(OptionScore(option_id=option.id, scale_id=scale_id, score=Decimal(str(score))))

    log_action(db, user_id=user.id, action="create", entity_type="question", entity_id=question.id,
               details={"version_id": version.id, "code": question.code})
    db.commit()
    db.refresh(question)
    return question_to_dict(question)


@app.put("/api/questions/{question_id}", tags=["questions"])
@app.patch("/api/questions/{question_id}", tags=["questions"])
def update_question(
    question_id: int,
    payload: QuestionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tests.edit")),
):
    """Обновить текст вопроса, тип, обязательность и варианты ответов.

    Поддерживается и полное (PUT), и частичное (PATCH) обновление — применяются
    только переданные поля. Варианты ответа синхронизируются по `id`:
    существующие обновляются на месте, новые создаются, отсутствующие удаляются.
    Поле `scores` у варианта полностью заменяет его ключи по шкалам.
    """
    question = db.get(Question, question_id)
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вопрос не найден")

    version = question.version
    valid_scale_ids = {s.id for s in version.scales}
    changes = payload.model_dump(exclude_unset=True, exclude={"options"})

    # --- код вопроса (уникален в рамках версии) ---
    new_code = changes.get("code")
    if new_code and new_code != question.code:
        clash = (
            db.query(Question)
            .filter(Question.version_id == version.id, Question.code == new_code, Question.id != question.id)
            .first()
        )
        if clash:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Вопрос с таким кодом уже есть")
        question.code = new_code

    # --- порядковый номер (уникален в рамках версии) ---
    new_order = changes.get("order_num")
    if new_order is not None and new_order != question.order_num:
        clash = (
            db.query(Question)
            .filter(Question.version_id == version.id, Question.order_num == new_order, Question.id != question.id)
            .first()
        )
        if clash:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Эта позиция уже занята другим вопросом")
        question.order_num = new_order

    # --- текстовые/логические поля ---
    if changes.get("text") is not None:
        question.text = changes["text"]
    if changes.get("question_type") is not None:
        question.question_type = changes["question_type"].value
    if changes.get("is_required") is not None:
        question.is_required = changes["is_required"]

    # --- варианты ответа ---
    if payload.options is not None:
        _sync_question_options(db, question, payload.options, valid_scale_ids)

    db.flush()
    log_action(db, user_id=user.id, action="update", entity_type="question", entity_id=question.id,
               details={"version_id": version.id, "code": question.code})
    db.commit()
    db.refresh(question)
    return question_to_dict(question)


@app.delete("/api/questions/{question_id}", tags=["questions"])
def delete_question(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tests.edit")),
):
    """Удалить вопрос из версии."""
    question = db.get(Question, question_id)
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вопрос не найден")
    db.delete(question)
    log_action(db, user_id=user.id, action="delete", entity_type="question", entity_id=question_id)
    db.commit()
    return {"ok": True}


# ===========================================================================
# 9. API: ПОСЛЕДОВАТЕЛЬНОСТИ И НАЗНАЧЕНИЯ
# ===========================================================================


@app.get("/api/sequences", tags=["sequences"])
def list_sequences(
    db: Session = Depends(get_db),
    user: User = Depends(require_staff),
):
    """Все последовательности тестов."""
    sequences = db.query(TestSequence).order_by(TestSequence.created_at.desc()).all()
    return [sequence_to_dict(s, with_items=True) for s in sequences]


@app.post("/api/sequences", tags=["sequences"], status_code=status.HTTP_201_CREATED)
def create_sequence(
    payload: SequenceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("sequences.edit")),
):
    """Создать последовательность тестов."""
    sequence = TestSequence(title=payload.title, description=payload.description, created_by=user.id)
    db.add(sequence)
    db.flush()
    for order, test_id in enumerate(payload.test_ids, start=1):
        test = db.get(Test, test_id)
        if not test:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Тест {test_id} не найден")
        db.add(SequenceItem(sequence_id=sequence.id, test_id=test_id, order_num=order, is_required=True))
    log_action(db, user_id=user.id, action="create", entity_type="test_sequence", entity_id=sequence.id)
    db.commit()
    db.refresh(sequence)
    return sequence_to_dict(sequence, with_items=True)


@app.post("/api/sequences/{sequence_id}/assign", tags=["sequences"])
def assign_sequence(
    sequence_id: int,
    payload: AssignRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("assign.manage")),
):
    """Назначить последовательность студентам (создаёт UserSequence и UserTestProgress)."""
    sequence = db.get(TestSequence, sequence_id)
    if not sequence:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Последовательность не найдена")

    created = []
    for uid in payload.user_ids:
        target = db.get(User, uid)
        if not target:
            continue
        existing = (
            db.query(UserSequence)
            .filter(UserSequence.user_id == uid, UserSequence.sequence_id == sequence.id)
            .first()
        )
        if existing:
            continue
        us = UserSequence(user_id=uid, sequence_id=sequence.id, assigned_by=user.id)
        db.add(us)
        for item in sequence.items:
            progress = (
                db.query(UserTestProgress)
                .filter(
                    UserTestProgress.user_id == uid,
                    UserTestProgress.sequence_id == sequence.id,
                    UserTestProgress.test_id == item.test_id,
                )
                .first()
            )
            if not progress:
                db.add(
                    UserTestProgress(
                        user_id=uid,
                        sequence_id=sequence.id,
                        test_id=item.test_id,
                        status=ProgressStatus.NOT_STARTED.value,
                    )
                )
        created.append(uid)
    log_action(db, user_id=user.id, action="assign", entity_type="test_sequence", entity_id=sequence.id,
               details={"user_ids": created})
    db.commit()
    return {"ok": True, "assigned_user_ids": created}


@app.get("/api/assignments", tags=["sequences"])
def list_assignments(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Назначения: свои — студентам, все — персоналу."""
    query = db.query(UserSequence)
    staff = user.has_role(ROLE_ADMIN) or user.has_role(ROLE_METHODIST)
    if not staff:
        query = query.filter(UserSequence.user_id == user.id)
    return [assignment_to_dict(a) for a in query.order_by(UserSequence.assigned_at.desc()).all()]


@app.get("/api/my/assignments", tags=["sequences"])
def my_assignments(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Назначения текущего пользователя с прогрессом по тестам."""
    assignments = (
        db.query(UserSequence)
        .filter(UserSequence.user_id == user.id)
        .order_by(UserSequence.assigned_at.desc())
        .all()
    )
    result = []
    for us in assignments:
        data = assignment_to_dict(us)
        data["tests"] = [progress_to_dict(p) for p in us.progress_items]
        result.append(data)
    return result


# ===========================================================================
# 10. API: ПРОХОЖДЕНИЕ ТЕСТОВ
# ===========================================================================


def resolve_version(db: Session, payload: StartAttemptRequest) -> TestVersion:
    """Определяет версию для старта попытки (по version_id / test_id / sequence)."""
    if payload.version_id:
        version = db.get(TestVersion, payload.version_id)
        if not version:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Версия не найдена")
        return version
    if payload.test_id:
        test = db.get(Test, payload.test_id)
        if not test:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тест не найден")
        version = test.published_version
        if not version:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нет опубликованной версии теста")
        return version
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Укажите version_id или test_id")


@app.post("/api/attempts", tags=["attempts"], status_code=status.HTTP_201_CREATED)
def start_attempt(
    payload: StartAttemptRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Начать попытку прохождения теста. Возвращает вопросы версии."""
    version = resolve_version(db, payload)
    attempt = Attempt(
        user_id=user.id,
        version_id=version.id,
        sequence_id=payload.sequence_id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(attempt)
    db.flush()

    # Отметить прогресс как "в процессе", если попытка в рамках последовательности.
    if payload.sequence_id:
        progress = (
            db.query(UserTestProgress)
            .filter(
                UserTestProgress.user_id == user.id,
                UserTestProgress.sequence_id == payload.sequence_id,
                UserTestProgress.test_id == version.test_id,
            )
            .first()
        )
        if progress:
            progress.status = ProgressStatus.IN_PROGRESS.value
            progress.attempt_id = attempt.id
            us = (
                db.query(UserSequence)
                .filter(UserSequence.user_id == user.id, UserSequence.sequence_id == payload.sequence_id)
                .first()
            )
            if us and us.status == "assigned":
                us.status = "in_progress"

    log_action(db, user_id=user.id, action="start", entity_type="attempt", entity_id=attempt.id,
               details={"version_id": version.id})
    db.commit()
    return {
        "attempt": attempt_to_dict(attempt),
        "instruction": version.instruction,
        "questions": [question_to_dict(q) for q in version.questions],
    }


@app.get("/api/attempts/{attempt_id}", tags=["attempts"])
def get_attempt(
    attempt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Попытка с ответами и (если завершена) результатами по шкалам."""
    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if attempt.user_id != user.id and not (user.has_role(ROLE_ADMIN) or user.has_role(ROLE_METHODIST)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к попытке")
    data = attempt_to_dict(attempt, with_answers=True)
    data["questions"] = [question_to_dict(q) for q in attempt.version.questions]
    data["results"] = [scale_result_to_dict(r) for r in attempt.scale_results]
    return data


@app.post("/api/attempts/{attempt_id}/answers", tags=["attempts"])
def save_answers(
    attempt_id: int,
    payload: AttemptSubmit,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Сохранить (upsert) ответы попытки; при finish=True — завершить и посчитать результат."""
    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if attempt.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к попытке")
    if attempt.is_finished:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Попытка уже завершена")

    question_map = {q.id: q for q in attempt.version.questions}

    for ans in payload.answers:
        question = question_map.get(ans.question_id)
        if not question:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Вопрос {ans.question_id} не из этой версии")

        record = (
            db.query(AttemptAnswer)
            .filter(AttemptAnswer.attempt_id == attempt.id, AttemptAnswer.question_id == question.id)
            .first()
        )
        if not record:
            record = AttemptAnswer(attempt_id=attempt.id, question_id=question.id)
            db.add(record)
            db.flush()

        if question.is_open_text:
            record.answer_text = ans.text
        else:
            valid_option_ids = {o.id for o in question.answer_options}
            invalid = set(ans.option_ids) - valid_option_ids
            if invalid:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Недопустимые варианты для вопроса {question.id}: {sorted(invalid)}")
            # Полная замена выбранных вариантов.
            for link in list(record.selected_options):
                db.delete(link)
            db.flush()
            # single_choice/scale — не более одного варианта.
            option_ids = ans.option_ids[:1] if not question.is_multi else ans.option_ids
            for oid in option_ids:
                db.add(AttemptAnswerOption(attempt_answer_id=record.id, option_id=oid))

    db.flush()

    if payload.finish:
        _finalize_attempt(db, attempt, user)

    log_action(db, user_id=user.id, action="save_answers", entity_type="attempt", entity_id=attempt.id,
               details={"count": len(payload.answers), "finished": payload.finish})
    db.commit()
    return {
        "ok": True,
        "attempt": attempt_to_dict(attempt, with_answers=True),
        "results": [scale_result_to_dict(r) for r in attempt.scale_results],
    }


@app.post("/api/attempts/{attempt_id}/finish", tags=["attempts"])
def finish_attempt(
    attempt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Завершить попытку и рассчитать результаты."""
    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if attempt.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к попытке")
    if not attempt.is_finished:
        _finalize_attempt(db, attempt, user)
    db.commit()
    return {
        "ok": True,
        "attempt": attempt_to_dict(attempt),
        "results": [scale_result_to_dict(r) for r in attempt.scale_results],
    }


@app.post("/api/attempts/{attempt_id}/abort", tags=["attempts"])
def abort_attempt(
    attempt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Прервать попытку."""
    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if attempt.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к попытке")
    attempt.finish(status=AttemptStatus.ABORTED.value)
    log_action(db, user_id=user.id, action="abort", entity_type="attempt", entity_id=attempt.id)
    db.commit()
    return {"ok": True, "attempt": attempt_to_dict(attempt)}


def _finalize_attempt(db: Session, attempt: Attempt, user: User) -> None:
    """Расчёт результатов, фиксация завершения и обновление прогресса."""
    compute_attempt_results(db, attempt)
    attempt.finish(status=AttemptStatus.COMPLETED.value)

    # Обновить прогресс в последовательности (если применимо).
    if attempt.sequence_id:
        progress = (
            db.query(UserTestProgress)
            .filter(
                UserTestProgress.user_id == attempt.user_id,
                UserTestProgress.sequence_id == attempt.sequence_id,
                UserTestProgress.test_id == attempt.version.test_id,
            )
            .first()
        )
        if progress:
            progress.complete()
            progress.attempt_id = attempt.id
        us = (
            db.query(UserSequence)
            .filter(
                UserSequence.user_id == attempt.user_id,
                UserSequence.sequence_id == attempt.sequence_id,
            )
            .first()
        )
        if us:
            total = len(us.sequence.items)
            done = sum(1 for p in us.progress_items if p.status == ProgressStatus.COMPLETED.value)
            us.status = "completed" if total and done >= total else "in_progress"


def compute_attempt_results(db: Session, attempt: Attempt) -> list:
    """Считает баллы по всем шкалам версии (sum/average/count/composite) и итог."""
    version = attempt.version
    scales = list(version.scales)
    scale_raw: dict[int, Decimal] = {s.id: Decimal("0") for s in scales}
    scale_answers: dict[int, set] = {s.id: set() for s in scales}

    # 1) Сбор «сырых» вкладов из ответов и ключей (option_scores / manual_scores).
    for answer in attempt.answers:
        question = answer.question
        if question.is_open_text:
            for ms in answer.manual_scores:
                if ms.scale_id in scale_raw:
                    scale_raw[ms.scale_id] += Decimal(str(ms.score))
                    scale_answers[ms.scale_id].add(answer.id)
            continue
        for link in answer.selected_options:
            for osc in link.option.scores:
                if osc.scale_id in scale_raw:
                    scale_raw[osc.scale_id] += Decimal(str(osc.score))
                    scale_answers[osc.scale_id].add(answer.id)

    # 2) Обычные шкалы.
    final_raw: dict[int, Decimal] = {}
    for scale in scales:
        if scale.scale_type == ScaleType.COMPOSITE.value:
            continue
        total = scale_raw[scale.id]
        if scale.scale_type == ScaleType.AVERAGE.value:
            count = len(scale_answers[scale.id])
            total = (total / Decimal(count)) if count else Decimal("0")
        elif scale.scale_type == ScaleType.COUNT.value:
            total = Decimal(len(scale_answers[scale.id]))
        final_raw[scale.id] = total.quantize(Decimal("0.01"))

    # 3) Составные шкалы (несколько проходов на случай взаимных зависимостей).
    composites = [s for s in scales if s.scale_type == ScaleType.COMPOSITE.value]
    for _ in range(3):
        for scale in composites:
            acc = Decimal("0")
            for dep in scale.composite_dependencies:
                component = final_raw.get(dep.component_scale_id, Decimal("0"))
                coef = Decimal(str(dep.coefficient))
                op = dep.operation
                if op == "/":
                    term = (component / coef) if coef else Decimal("0")
                else:
                    term = component * coef
                acc = (acc - term) if op == "-" else (acc + term)
            final_raw[scale.id] = acc.quantize(Decimal("0.01"))

    # 4) Перезапись результатов по шкалам.
    for old in list(attempt.scale_results):
        db.delete(old)
    db.flush()

    results = []
    for scale in scales:
        raw = final_raw.get(scale.id, Decimal("0"))
        normalized: Optional[Decimal] = None
        if scale.has_range:
            low, high = Decimal(str(scale.min_score)), Decimal(str(scale.max_score))
            if high > low:
                normalized = ((raw - low) / (high - low) * 100).quantize(Decimal("0.01"))
        level = scale.interpret(raw)
        interpretation = scale.interpretation or (f"Уровень выраженности: {level}" if level else None)
        result = models.AttemptScaleResult(
            attempt_id=attempt.id,
            scale_id=scale.id,
            raw_score=raw,
            normalized_score=normalized,
            interpretation=interpretation,
        )
        db.add(result)
        results.append(result)

    # 5) Итоговый балл (сумма «сырых» баллов по несоставным шкалам).
    total_score = sum(
        (final_raw.get(s.id, Decimal("0")) for s in scales if not s.is_composite),
        Decimal("0"),
    ).quantize(Decimal("0.01"))
    attempt.total_score = total_score
    if attempt.total_result:
        attempt.total_result.total_score = total_score
    else:
        db.add(models.AttemptTotalResult(attempt_id=attempt.id, total_score=total_score))

    db.flush()
    return results


# ===========================================================================
# 11. API: РУЧНАЯ ОЦЕНКА ОТКРЫТЫХ ОТВЕТОВ
# ===========================================================================


@app.post("/api/manual-scores", tags=["results"], status_code=status.HTTP_201_CREATED)
def add_manual_score(
    payload: ManualScoreIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("results.manual")),
):
    """Экспертная оценка открытого ответа по шкале."""
    answer = db.get(AttemptAnswer, payload.attempt_answer_id)
    if not answer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ответ не найден")
    scale = db.get(Scale, payload.scale_id)
    if not scale or scale.version_id != answer.attempt.version_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Шкала не из версии теста")

    score = (
        db.query(ManualAnswerScore)
        .filter(
            ManualAnswerScore.attempt_answer_id == answer.id,
            ManualAnswerScore.scale_id == scale.id,
            ManualAnswerScore.expert_id == user.id,
        )
        .first()
    )
    if score:
        score.score = Decimal(str(payload.score))
        score.comment = payload.comment
    else:
        score = ManualAnswerScore(
            attempt_answer_id=answer.id,
            scale_id=scale.id,
            expert_id=user.id,
            score=Decimal(str(payload.score)),
            comment=payload.comment,
        )
        db.add(score)
    db.flush()

    # Пересчёт результатов завершённой попытки.
    if answer.attempt.is_finished:
        compute_attempt_results(db, answer.attempt)

    log_action(db, user_id=user.id, action="manual_score", entity_type="attempt_answer",
               entity_id=answer.id, details={"scale_id": scale.id, "score": payload.score})
    db.commit()
    return {"ok": True, "attempt_answer_id": answer.id, "scale_id": scale.id, "score": payload.score}


# ===========================================================================
# 12. API: СТАТИСТИКА И ОТЧЁТЫ
# ===========================================================================


@app.get("/api/statistics/tests/{test_id}", tags=["statistics"])
def test_statistics(
    test_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("results.view.all")),
):
    """Агрегированная статистика по тесту: число попыток и средние по шкалам."""
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тест не найден")

    version_ids = [v.id for v in test.versions]
    attempts = (
        db.query(Attempt)
        .filter(Attempt.version_id.in_(version_ids), Attempt.status == AttemptStatus.COMPLETED.value)
        .all()
        if version_ids
        else []
    )

    scale_stats: dict[int, dict] = {}
    for attempt in attempts:
        for res in attempt.scale_results:
            bucket = scale_stats.setdefault(
                res.scale_id,
                {
                    "scale_id": res.scale_id,
                    "scale_code": res.scale.code,
                    "scale_name": res.scale.name,
                    "values": [],
                },
            )
            bucket["values"].append(float(res.raw_score))

    scales_report = []
    for bucket in scale_stats.values():
        values = bucket.pop("values")
        bucket["count"] = len(values)
        bucket["avg"] = round(sum(values) / len(values), 2) if values else None
        bucket["min"] = min(values) if values else None
        bucket["max"] = max(values) if values else None
        scales_report.append(bucket)

    totals = [float(a.total_score) for a in attempts if a.total_score is not None]
    return {
        "test_id": test.id,
        "test_title": test.title,
        "completed_attempts": len(attempts),
        "unique_students": len({a.user_id for a in attempts}),
        "avg_total_score": round(sum(totals) / len(totals), 2) if totals else None,
        "scales": scales_report,
    }


@app.get("/api/statistics/attempts/{attempt_id}", tags=["statistics"])
def attempt_statistics(
    attempt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Результаты конкретной попытки по шкалам."""
    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if attempt.user_id != user.id and not (user.has_role(ROLE_ADMIN) or user.has_role(ROLE_METHODIST)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к попытке")
    return {
        "attempt": attempt_to_dict(attempt),
        "total_score": num(attempt.total_score),
        "total_interpretation": attempt.total_result.interpretation if attempt.total_result else None,
        "results": [scale_result_to_dict(r) for r in attempt.scale_results],
    }


@app.get("/api/statistics/my", tags=["statistics"])
def my_statistics(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Сводка результатов текущего пользователя."""
    attempts = (
        db.query(Attempt)
        .filter(Attempt.user_id == user.id, Attempt.status == AttemptStatus.COMPLETED.value)
        .order_by(Attempt.finished_at.desc())
        .all()
    )
    return {
        "completed_attempts": len(attempts),
        "attempts": [
            {
                **attempt_to_dict(a),
                "results": [scale_result_to_dict(r) for r in a.scale_results],
            }
            for a in attempts
        ],
    }


# ===========================================================================
# 13. API: ПОЛЬЗОВАТЕЛИ И АУДИТ (администрирование)
# ===========================================================================


@app.get("/api/users", tags=["users"])
def list_users(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("users.manage")),
    role: Optional[str] = None,
):
    """Список пользователей (фильтр по роли)."""
    query = db.query(User)
    users = query.order_by(User.login).all()
    if role:
        users = [u for u in users if u.has_role(role)]
    return [user_to_dict(u) for u in users]


@app.get("/api/roles", tags=["users"])
def list_roles(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("roles.manage")),
):
    """Список ролей с правами."""
    roles = db.query(Role).order_by(Role.code).all()
    return [
        {
            "id": r.id,
            "code": r.code,
            "name": r.name,
            "description": r.description,
            "permissions": sorted(p.code for p in r.permissions),
        }
        for r in roles
    ]


@app.get("/api/audit", tags=["audit"])
def list_audit(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("audit.view")),
    limit: int = 100,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
):
    """Журнал аудита (последние записи)."""
    query = db.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action == action)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    logs = query.order_by(AuditLog.created_at.desc()).limit(min(limit, 500)).all()
    return [
        {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "details": log.details,
            "created_at": dt(log.created_at),
        }
        for log in logs
    ]


# ===========================================================================
# 14. HTML-СТРАНИЦЫ (Jinja2) — отдача через TemplateResponse(request=request, ...)
# ===========================================================================


@app.get("/", tags=["pages"], include_in_schema=False)
def page_root(request: Request, db: Session = Depends(get_db)):
    """Главная страница."""
    user = _session_user(request, db)
    if user:
        return RedirectResponse(
            "/student" if user.is_student and not (user.is_admin or user.is_methodist) else "/dashboard",
            status_code=status.HTTP_302_FOUND,
        )
    return render(request, "index.html", user=None)


@app.get("/login", tags=["pages"], include_in_schema=False)
def page_login(request: Request, db: Session = Depends(get_db)):
    """Страница входа."""
    if _session_user(request, db):
        return RedirectResponse("/dashboard", status_code=status.HTTP_302_FOUND)
    return render(request, "login.html", user=None)


@app.get("/register", tags=["pages"], include_in_schema=False)
def page_register(request: Request, db: Session = Depends(get_db)):
    """Страница регистрации."""
    return render(request, "register.html", user=None)


@app.get("/dashboard", tags=["pages"], include_in_schema=False)
def page_dashboard(request: Request, db: Session = Depends(get_db)):
    """Кабинет методиста/администратора."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    if not (user.is_admin or user.is_methodist):
        return RedirectResponse("/student", status_code=status.HTTP_302_FOUND)
    tests_count = db.query(Test).count()
    sequences_count = db.query(TestSequence).count()
    return render(
        request,
        "dashboard.html",
        user=user,
        active="dashboard",
        tests_count=tests_count,
        sequences_count=sequences_count,
    )


@app.get("/tests", tags=["pages"], include_in_schema=False)
def page_tests(request: Request, db: Session = Depends(get_db)):
    """Список тестов."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    if not (user.is_admin or user.is_methodist):
        return RedirectResponse("/student", status_code=status.HTTP_302_FOUND)
    tests = db.query(Test).order_by(Test.title).all()
    return render(request, "tests.html", user=user, active="tests", tests=tests)


@app.get("/tests/{test_id}", tags=["pages"], include_in_schema=False)
def page_test_detail(test_id: int, request: Request, db: Session = Depends(get_db)):
    """Карточка теста с версиями."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тест не найден")
    return render(request, "test_detail.html", user=user, active="tests", test=test)


@app.get("/tests/{test_id}/edit", tags=["pages"], include_in_schema=False)
def page_test_edit(test_id: int, request: Request, db: Session = Depends(get_db)):
    """Редактор теста (вопросы, варианты, шкалы)."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    if not (user.is_admin or user.is_methodist):
        return RedirectResponse("/student", status_code=status.HTTP_302_FOUND)
    test = db.get(Test, test_id)
    if not test:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тест не найден")
    return render(request, "test_editor.html", user=user, active="tests", test=test)


@app.get("/sequences", tags=["pages"], include_in_schema=False)
def page_sequences(request: Request, db: Session = Depends(get_db)):
    """Последовательности тестов."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    if not (user.is_admin or user.is_methodist):
        return RedirectResponse("/student", status_code=status.HTTP_302_FOUND)
    sequences = db.query(TestSequence).order_by(TestSequence.created_at.desc()).all()
    return render(request, "sequences.html", user=user, active="sequences", sequences=sequences)


@app.get("/assignments", tags=["pages"], include_in_schema=False)
def page_assignments(request: Request, db: Session = Depends(get_db)):
    """Назначения тестов студентам."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    if not (user.is_admin or user.is_methodist):
        return RedirectResponse("/student", status_code=status.HTTP_302_FOUND)
    assignments = db.query(UserSequence).order_by(UserSequence.assigned_at.desc()).all()
    return render(request, "assignments.html", user=user, active="assignments", assignments=assignments)


@app.get("/student", tags=["pages"], include_in_schema=False)
def page_student(request: Request, db: Session = Depends(get_db)):
    """Кабинет студента: назначения и доступные тесты."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    assignments = (
        db.query(UserSequence)
        .filter(UserSequence.user_id == user.id)
        .order_by(UserSequence.assigned_at.desc())
        .all()
    )
    tests = db.query(Test).filter(Test.is_active.is_(True)).order_by(Test.title).all()
    return render(
        request,
        "student.html",
        user=user,
        active="student",
        assignments=assignments,
        tests=tests,
    )


@app.get("/attempt/{attempt_id}", tags=["pages"], include_in_schema=False)
def page_attempt(attempt_id: int, request: Request, db: Session = Depends(get_db)):
    """Прохождение теста (форма ответов)."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if attempt.user_id != user.id and not (user.is_admin or user.is_methodist):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к попытке")
    return render(request, "attempt.html", user=user, active="student", attempt=attempt)


@app.get("/results/{attempt_id}", tags=["pages"], include_in_schema=False)
def page_results(attempt_id: int, request: Request, db: Session = Depends(get_db)):
    """Страница результатов попытки."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Попытка не найдена")
    if attempt.user_id != user.id and not (user.is_admin or user.is_methodist):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к попытке")
    return render(request, "results.html", user=user, active="results", attempt=attempt)


@app.get("/statistics", tags=["pages"], include_in_schema=False)
def page_statistics(request: Request, db: Session = Depends(get_db)):
    """Статистика и отчёты."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    if not (user.is_admin or user.is_methodist):
        return RedirectResponse("/student", status_code=status.HTTP_302_FOUND)
    tests = db.query(Test).order_by(Test.title).all()
    return render(request, "statistics.html", user=user, active="statistics", tests=tests)


@app.get("/users", tags=["pages"], include_in_schema=False)
def page_users(request: Request, db: Session = Depends(get_db)):
    """Управление пользователями (администратор)."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступно только администратору")
    users = db.query(User).order_by(User.login).all()
    return render(request, "users.html", user=user, active="users", users=users)


@app.get("/audit", tags=["pages"], include_in_schema=False)
def page_audit(request: Request, db: Session = Depends(get_db)):
    """Журнал аудита."""
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    if not user.is_admin and not user.has_permission("audit.view"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступно только администратору")
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()
    return render(request, "audit.html", user=user, active="audit", logs=logs)


# ===========================================================================
# Точка входа для прямого запуска
# ===========================================================================

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
