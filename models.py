"""
models.py — ORM-модели (SQLAlchemy 2.x) системы психологического тестирования.

Схема в точности соответствует ТЗ («структура_БД_методика.docx») — 23 таблицы:

    RBAC:        roles, users, user_roles, permissions, role_permissions
    Тесты:       tests, test_versions, questions, answer_options, option_scores
    Шкалы:       scales, scale_dependencies
    Назначения:  test_sequences, sequence_items, user_sequences, user_test_progress
    Попытки:     attempts, attempt_answers, attempt_answer_options
    Результаты:  manual_answer_scores, attempt_scale_results, attempt_total_results
    Аудит:       audit_log

Особенности реализации:
  * `BIGINT` — переносимый BIGINT (PostgreSQL BIGSERIAL / SQLite INTEGER);
  * `JSON_TYPE` — JSONB для PostgreSQL с fallback на JSON (audit_log.details);
  * `utcnow()` — naive-UTC datetime (соответствует `TIMESTAMP` без таймзоны);
  * все CHECK-ограничения доменов значений (статусы, типы вопросов/шкал);
  * все UNIQUE-ограничения и индексы из ТЗ;
  * Enum-классы с текстовыми значениями — для приложения, API и шаблонов Jinja2;
  * обратные действия ON DELETE (CASCADE / SET NULL) как в ТЗ.

Порядок объявления моделей:
  1) Пользователи и права      4) Шкалы, вопросы и ключи
  2) Тесты и версии            5) Попытки и результаты
  3) Последовательности        6) Аудит
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from database import Base

# ---------------------------------------------------------------------------
# Переносимые базовые типы и утилиты
# ---------------------------------------------------------------------------

#: BIGINT-идентификатор с автоинкрементом (SQLite — INTEGER).
BIGINT = BigInteger().with_variant(Integer(), "sqlite")

#: JSONB для PostgreSQL, JSON для остальных диалектов (audit_log.details).
JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


def utcnow() -> datetime:
    """Текущее время UTC без tzinfo (naive) — как ожидает колонка `TIMESTAMP`."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def fk(target: str, ondelete: Optional[str] = None) -> ForeignKey:
    """Внешний ключ с действием при удалении (CASCADE / None = без действия)."""
    return ForeignKey(target, ondelete=ondelete)


# ---------------------------------------------------------------------------
# Домены значений (совпадают с CHECK-ограничениями из ТЗ)
# ---------------------------------------------------------------------------


class QuestionType(str, Enum):
    """Типы вопросов теста."""

    SINGLE_CHOICE = "single_choice"     # одиночный выбор (радиокнопки)
    MULTI_CHOICE = "multi_choice"       # множественный выбор (чекбоксы)
    SCALE = "scale"                     # шкала Ликерта
    OPEN_TEXT = "open_text"             # открытый ответ (ручная оценка)


class ScaleType(str, Enum):
    """Типы шкал."""

    SUM = "sum"              # простая сумма ключей
    AVERAGE = "average"      # среднее значение
    COUNT = "count"          # количество совпадений с ключом
    COMPOSITE = "composite"  # составная (по scale_dependencies)


class ScaleOperation(str, Enum):
    """Операции в формуле составной шкалы."""

    ADD = "+"
    SUB = "-"
    MUL = "*"
    DIV = "/"


class AttemptStatus(str, Enum):
    """Статусы попытки прохождения теста."""

    STARTED = "started"
    COMPLETED = "completed"
    ABORTED = "aborted"


class AssignmentStatus(str, Enum):
    """Статусы назначенной последовательности тестов."""

    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ProgressStatus(str, Enum):
    """Статусы прохождения отдельного теста в последовательности."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


#: Коды системных ролей.
ROLE_ADMIN = "admin"
ROLE_METHODIST = "methodist"
ROLE_STUDENT = "student"

#: Справочник прав системы (коды -> человекочитаемые названия).
PERMISSIONS: dict[str, str] = {
    "tests.view": "Просмотр тестов",
    "tests.edit": "Создание и редактирование тестов",
    "tests.publish": "Публикация версий тестов",
    "sequences.edit": "Управление последовательностями",
    "assign.manage": "Назначение тестов студентам",
    "attempts.take": "Прохождение тестирования",
    "results.view.own": "Просмотр своих результатов",
    "results.view.all": "Просмотр результатов всех студентов",
    "results.manual": "Ручная оценка открытых ответов",
    "users.manage": "Управление пользователями",
    "roles.manage": "Управление ролями и правами",
    "audit.view": "Просмотр журнала аудита",
    "system.manage": "Администрирование системы",
}


# ===========================================================================
# 1. ПОЛЬЗОВАТЕЛИ, РОЛИ И ПРАВА (модуль аутентификации / авторизации)
# ===========================================================================


class Role(Base):
    """`roles` — роли пользователей системы."""

    __tablename__ = "roles"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)

    # --- связи (many-to-many) ---
    users: list["User"] = relationship(
        "User",
        secondary="user_roles",
        back_populates="roles",
        lazy="selectin",
    )
    permissions: list["Permission"] = relationship(
        "Permission",
        secondary="role_permissions",
        back_populates="roles",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Role {self.code}>"


class User(Base):
    """`users` — учётные записи пользователей (студенты, методисты, администраторы)."""

    __tablename__ = "users"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    login = Column(String(100), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)   # bcrypt / argon2
    full_name = Column(String(200), nullable=False)
    email = Column(String(255), nullable=True, unique=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, default=None, onupdate=utcnow)

    # --- связи ---
    roles: list[Role] = relationship(
        Role,
        secondary="user_roles",
        back_populates="users",
        lazy="selectin",
    )
    created_tests: list["Test"] = relationship(
        "Test",
        back_populates="author",
        foreign_keys="Test.created_by",
    )
    created_versions: list["TestVersion"] = relationship(
        "TestVersion",
        back_populates="author",
        foreign_keys="TestVersion.created_by",
    )
    created_sequences: list["TestSequence"] = relationship(
        "TestSequence",
        back_populates="author",
        foreign_keys="TestSequence.created_by",
    )
    assigned_sequences: list["UserSequence"] = relationship(
        "UserSequence",
        back_populates="user",
        foreign_keys="UserSequence.user_id",
        cascade="all, delete-orphan",
    )
    issued_assignments: list["UserSequence"] = relationship(
        "UserSequence",
        back_populates="assigner",
        foreign_keys="UserSequence.assigned_by",
        passive_deletes=True,
    )
    test_progress: list["UserTestProgress"] = relationship(
        "UserTestProgress",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    attempts: list["Attempt"] = relationship(
        "Attempt",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    manual_scores: list["ManualAnswerScore"] = relationship(
        "ManualAnswerScore",
        back_populates="expert",
        foreign_keys="ManualAnswerScore.expert_id",
        passive_deletes=True,
    )
    audit_logs: list["AuditLog"] = relationship("AuditLog", back_populates="user", passive_deletes=True)

    # --- хелперы RBAC ---
    @property
    def role_codes(self) -> list[str]:
        """Список кодов ролей пользователя."""
        return [role.code for role in self.roles]

    def has_role(self, code: str) -> bool:
        return code in self.role_codes

    @property
    def is_admin(self) -> bool:
        return self.has_role(ROLE_ADMIN)

    @property
    def is_methodist(self) -> bool:
        return self.has_role(ROLE_METHODIST)

    @property
    def is_student(self) -> bool:
        return self.has_role(ROLE_STUDENT)

    def has_permission(self, code: str) -> bool:
        """Есть ли у пользователя право (через любую из его ролей)."""
        return any(perm.code == code for role in self.roles for perm in role.permissions)

    def get_primary_role(self) -> Optional[str]:
        """Роль для редиректа после входа: admin -> methodist -> student."""
        for code in (ROLE_ADMIN, ROLE_METHODIST, ROLE_STUDENT):
            if self.has_role(code):
                return code
        return self.role_codes[0] if self.roles else None

    def __repr__(self) -> str:
        return f"<User {self.login} ({', '.join(self.role_codes) or 'no-role'})>"


class UserRole(Base):
    """`user_roles` — связь пользователь ↔ роль."""

    __tablename__ = "user_roles"

    user_id = Column(BIGINT, fk("users.id", "CASCADE"), primary_key=True)
    role_id = Column(BIGINT, fk("roles.id", "CASCADE"), primary_key=True)
    assigned_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())


class Permission(Base):
    """`permissions` — справочник разрешений (прав) системы."""

    __tablename__ = "permissions"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    code = Column(String(100), nullable=False, unique=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    roles: list[Role] = relationship(
        Role,
        secondary="role_permissions",
        back_populates="permissions",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Permission {self.code}>"


class RolePermission(Base):
    """`role_permissions` — связь роль ↔ разрешение."""

    __tablename__ = "role_permissions"

    role_id = Column(BIGINT, fk("roles.id", "CASCADE"), primary_key=True)
    permission_id = Column(BIGINT, fk("permissions.id", "CASCADE"), primary_key=True)


# ===========================================================================
# 2. ТЕСТЫ И ВЕРСИИ (модуль конструирования тестов — методист)
# ===========================================================================


class Test(Base):
    """`tests` — каталог тестов (неизменяемая «шапка» теста)."""

    __tablename__ = "tests"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    code = Column(String(50), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(BIGINT, fk("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, default=None, onupdate=utcnow)

    # --- связи ---
    author: User = relationship("User", back_populates="created_tests", foreign_keys=[created_by])
    versions: list["TestVersion"] = relationship(
        "TestVersion",
        back_populates="test",
        cascade="all, delete-orphan",
        order_by="TestVersion.version_no",
    )
    sequence_items: list["SequenceItem"] = relationship("SequenceItem", back_populates="test")
    user_progress: list["UserTestProgress"] = relationship("UserTestProgress", back_populates="test")

    # --- хелперы ---
    @property
    def latest_version(self) -> Optional["TestVersion"]:
        """Последняя созданная версия (в т.ч. черновик)."""
        return max(self.versions, key=lambda v: v.version_no) if self.versions else None

    @property
    def published_version(self) -> Optional["TestVersion"]:
        """Последняя опубликованная версия — актуальная для студента."""
        published = [v for v in self.versions if v.is_published]
        return max(published, key=lambda v: v.version_no) if published else None

    def get_version(self, version_no: int) -> Optional["TestVersion"]:
        return next((v for v in self.versions if v.version_no == version_no), None)

    def next_version_no(self) -> int:
        """Следующий свободный номер версии для этого теста."""
        return (max((v.version_no for v in self.versions), default=0)) + 1

    def __repr__(self) -> str:
        return f"<Test {self.code} '{self.title}'>"


class TestVersion(Base):
    """`test_versions` — версия теста: вопросы, шкалы и ключи этой версии."""

    __tablename__ = "test_versions"
    __table_args__ = (
        UniqueConstraint("test_id", "version_no", name="uq_test_versions_version"),
        Index("idx_test_versions_test", "test_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    test_id = Column(BIGINT, fk("tests.id", "CASCADE"), nullable=False)
    version_no = Column(Integer, nullable=False)
    instruction = Column(Text, nullable=True)                     # инструкция для студента
    comment = Column(Text, nullable=True)                         # комментарий/причина изменения версии
    is_published = Column(Boolean, nullable=False, default=False)
    created_by = Column(BIGINT, fk("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    published_at = Column(DateTime, nullable=True)

    # --- связи ---
    test: Test = relationship("Test", back_populates="versions")
    author: User = relationship("User", back_populates="created_versions", foreign_keys=[created_by])
    scales: list["Scale"] = relationship(
        "Scale", back_populates="version", cascade="all, delete-orphan", order_by="Scale.code"
    )
    questions: list["Question"] = relationship(
        "Question",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="Question.order_num",
    )
    attempts: list["Attempt"] = relationship("Attempt", back_populates="version", passive_deletes=True)

    # --- хелперы ---
    @property
    def status_label(self) -> str:
        return "опубликована" if self.is_published else "черновик"

    @property
    def question_count(self) -> int:
        return len(self.questions)

    @property
    def is_ready(self) -> bool:
        """Версия готова к публикации: есть вопросы и хотя бы одна шкала."""
        return bool(self.questions) and bool(self.scales)

    def get_scale(self, code: str) -> Optional["Scale"]:
        return next((s for s in self.scales if s.code == code), None)

    def get_question(self, code: str) -> Optional["Question"]:
        return next((q for q in self.questions if q.code == code), None)

    def publish(self, at: Optional[datetime] = None) -> None:
        """Публикация версии."""
        self.is_published = True
        self.published_at = at or utcnow()

    def unpublish(self) -> None:
        """Снятие версии с публикации."""
        self.is_published = False

    def __repr__(self) -> str:
        return f"<TestVersion test_id={self.test_id} v{self.version_no} {self.status_label}>"


# ===========================================================================
# 3. ПОСЛЕДОВАТЕЛЬНОСТИ ТЕСТОВ, НАЗНАЧЕНИЯ И ПРОГРЕСС
# ===========================================================================


class TestSequence(Base):
    """`test_sequences` — последовательность (набор) тестов для назначения."""

    __tablename__ = "test_sequences"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_by = Column(BIGINT, fk("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    # --- связи ---
    author: User = relationship("User", back_populates="created_sequences", foreign_keys=[created_by])
    items: list["SequenceItem"] = relationship(
        "SequenceItem",
        back_populates="sequence",
        cascade="all, delete-orphan",
        order_by="SequenceItem.order_num",
    )
    user_sequences: list["UserSequence"] = relationship(
        "UserSequence", back_populates="sequence", passive_deletes=True
    )
    user_progress: list["UserTestProgress"] = relationship("UserTestProgress", back_populates="sequence")
    attempts: list["Attempt"] = relationship("Attempt", back_populates="sequence", passive_deletes=True)

    # --- хелперы ---
    @property
    def tests_count(self) -> int:
        return len(self.items)

    def __repr__(self) -> str:
        return f"<TestSequence '{self.title}' items={len(self.items)}>"


class SequenceItem(Base):
    """`sequence_items` — тест внутри последовательности: порядок и обязательность."""

    __tablename__ = "sequence_items"
    __table_args__ = (
        UniqueConstraint("sequence_id", "order_num", name="uq_sequence_items_order"),
        UniqueConstraint("sequence_id", "test_id", name="uq_sequence_items_test"),
        Index("idx_sequence_items_seq", "sequence_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    sequence_id = Column(BIGINT, fk("test_sequences.id", "CASCADE"), nullable=False)
    test_id = Column(BIGINT, fk("tests.id"), nullable=False)
    order_num = Column(Integer, nullable=False)
    is_required = Column(Boolean, nullable=False, default=True)

    sequence: TestSequence = relationship("TestSequence", back_populates="items")
    test: Test = relationship("Test", back_populates="sequence_items")

    @property
    def published_version(self) -> Optional[TestVersion]:
        """Опубликованная версия теста, которую увидит студент."""
        return self.test.published_version

    def __repr__(self) -> str:
        return f"<SequenceItem seq={self.sequence_id} test={self.test_id} #{self.order_num}>"


class UserSequence(Base):
    """`user_sequences` — назначение последовательности конкретному пользователю."""

    __tablename__ = "user_sequences"
    __table_args__ = (
        UniqueConstraint("user_id", "sequence_id", name="uq_user_sequences_user_sequence"),
        CheckConstraint(
            "status IN ('assigned', 'in_progress', 'completed')",
            name="user_sequences_status",
        ),
        Index("idx_user_sequences_user", "user_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    user_id = Column(BIGINT, fk("users.id", "CASCADE"), nullable=False)
    sequence_id = Column(BIGINT, fk("test_sequences.id"), nullable=False)
    assigned_by = Column(BIGINT, fk("users.id"), nullable=False)
    assigned_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    status = Column(String(20), nullable=False, default=AssignmentStatus.ASSIGNED.value)

    # --- связи ---
    user: User = relationship("User", back_populates="assigned_sequences", foreign_keys=[user_id])
    sequence: TestSequence = relationship("TestSequence", back_populates="user_sequences")
    assigner: User = relationship("User", back_populates="issued_assignments", foreign_keys=[assigned_by])

    # --- хелперы ---
    @property
    def progress_items(self) -> list["UserTestProgress"]:
        """Прогресс пользователя по этой последовательности."""
        return [p for p in self.sequence.user_progress if p.user_id == self.user_id]

    @property
    def progress_percent(self) -> float:
        """Процент завершённых тестов последовательности."""
        items = self.progress_items
        if not items:
            return 0.0
        done = sum(1 for p in items if p.status == ProgressStatus.COMPLETED.value)
        return round(done / len(items) * 100, 1)

    def __repr__(self) -> str:
        return f"<UserSequence user={self.user_id} seq={self.sequence_id} {self.status}>"


class UserTestProgress(Base):
    """`user_test_progress` — прогресс пользователя по тесту внутри последовательности."""

    __tablename__ = "user_test_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "sequence_id", "test_id", name="uq_user_test_progress_triple"),
        CheckConstraint(
            "status IN ('not_started', 'in_progress', 'completed')",
            name="user_test_progress_status",
        ),
        Index("idx_utp_user_seq", "user_id", "sequence_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    user_id = Column(BIGINT, fk("users.id", "CASCADE"), nullable=False)
    sequence_id = Column(BIGINT, fk("test_sequences.id"), nullable=False)
    test_id = Column(BIGINT, fk("tests.id"), nullable=False)
    status = Column(String(20), nullable=False, default=ProgressStatus.NOT_STARTED.value)
    attempt_id = Column(BIGINT, fk("attempts.id"), nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # --- связи ---
    user: User = relationship("User", back_populates="test_progress")
    sequence: TestSequence = relationship("TestSequence", back_populates="user_progress")
    test: Test = relationship("Test", back_populates="user_progress")
    attempt: Optional["Attempt"] = relationship("Attempt", back_populates="progress_records")

    # --- хелперы ---
    @property
    def is_completed(self) -> bool:
        return self.status == ProgressStatus.COMPLETED.value

    def complete(self, at: Optional[datetime] = None) -> None:
        """Отметить тест завершённым."""
        self.status = ProgressStatus.COMPLETED.value
        self.completed_at = at or utcnow()

    def __repr__(self) -> str:
        return f"<UserTestProgress user={self.user_id} test={self.test_id} {self.status}>"


# ===========================================================================
# 4. ШКАЛЫ, ФОРМУЛЫ, ВОПРОСЫ И КЛЮЧИ
# ===========================================================================


class Scale(Base):
    """`scales` — шкала версии теста (sum / average / count / composite)."""

    __tablename__ = "scales"
    __table_args__ = (
        UniqueConstraint("version_id", "code", name="uq_scales_version_code"),
        CheckConstraint(
            "scale_type IN ('sum', 'average', 'count', 'composite')",
            name="scales_scale_type",
        ),
        Index("idx_scales_version", "version_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    version_id = Column(BIGINT, fk("test_versions.id", "CASCADE"), nullable=False)
    code = Column(String(50), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    scale_type = Column(String(20), nullable=False, default=ScaleType.SUM.value)
    min_score = Column(Numeric(10, 2), nullable=True)
    max_score = Column(Numeric(10, 2), nullable=True)
    interpretation = Column(Text, nullable=True)      # текст интерпретации результата

    # --- связи ---
    version: TestVersion = relationship("TestVersion", back_populates="scales")
    option_scores: list["OptionScore"] = relationship(
        "OptionScore", back_populates="scale", cascade="all, delete-orphan"
    )
    composite_dependencies: list["ScaleDependency"] = relationship(
        "ScaleDependency",
        back_populates="composite_scale",
        foreign_keys="ScaleDependency.composite_scale_id",
        cascade="all, delete-orphan",
    )
    component_of: list["ScaleDependency"] = relationship(
        "ScaleDependency",
        back_populates="component_scale",
        foreign_keys="ScaleDependency.component_scale_id",
        passive_deletes=True,
    )
    attempt_scale_results: list["AttemptScaleResult"] = relationship(
        "AttemptScaleResult", back_populates="scale", passive_deletes=True
    )
    manual_scores: list["ManualAnswerScore"] = relationship(
        "ManualAnswerScore", back_populates="scale", passive_deletes=True
    )

    # --- хелперы ---
    @property
    def is_composite(self) -> bool:
        return self.scale_type == ScaleType.COMPOSITE.value

    @property
    def has_range(self) -> bool:
        return self.min_score is not None and self.max_score is not None

    def in_range(self, value: float | Decimal) -> bool:
        """Попадает ли значение в диапазон шкалы."""
        if not self.has_range:
            return True
        return Decimal(str(self.min_score)) <= Decimal(str(value)) <= Decimal(str(self.max_score))

    def interpret(self, value: float | Decimal) -> Optional[str]:
        """Уровень выраженности по диапазону шкалы: низкий / средний / высокий."""
        if not self.has_range:
            return None
        low, high = Decimal(str(self.min_score)), Decimal(str(self.max_score))
        if high <= low:
            return None
        ratio = (Decimal(str(value)) - low) / (high - low)
        if ratio < Decimal("0.33"):
            return "низкий"
        if ratio < Decimal("0.67"):
            return "средний"
        return "высокий"

    def __repr__(self) -> str:
        return f"<Scale {self.code} ({self.scale_type}) version_id={self.version_id}>"


class ScaleDependency(Base):
    """`scale_dependencies` — формула составной (composite) шкалы."""

    __tablename__ = "scale_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "composite_scale_id", "component_scale_id", name="uq_scale_dependencies_pair"
        ),
        CheckConstraint("operation IN ('+', '-', '*', '/')", name="scale_dependencies_operation"),
        Index("idx_scale_dep_composite", "composite_scale_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    composite_scale_id = Column(BIGINT, fk("scales.id", "CASCADE"), nullable=False)
    component_scale_id = Column(BIGINT, fk("scales.id"), nullable=False)
    coefficient = Column(Numeric(10, 4), nullable=False, default=Decimal("1"))
    operation = Column(String(3), nullable=False, default=ScaleOperation.ADD.value)

    composite_scale: Scale = relationship(
        Scale,
        back_populates="composite_dependencies",
        foreign_keys=[composite_scale_id],
    )
    component_scale: Scale = relationship(
        Scale,
        back_populates="component_of",
        foreign_keys=[component_scale_id],
    )

    @property
    def formula_part(self) -> str:
        """Человекочитаемая часть формулы, например «(шкала1*0.5)»."""
        coef = Decimal(str(self.coefficient))
        coef_text = "" if coef == Decimal("1") else f"*{str(coef).rstrip('0').rstrip('.')}"
        prefix = "" if self.operation == ScaleOperation.ADD.value else self.operation
        return f"{prefix}({self.component_scale.code}{coef_text})"

    def __repr__(self) -> str:
        return f"<ScaleDependency {self.operation}{self.component_scale_id} k={self.coefficient}>"


class Question(Base):
    """`questions` — вопрос версии теста."""

    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("version_id", "code", name="uq_questions_version_code"),
        UniqueConstraint("version_id", "order_num", name="uq_questions_version_order"),
        CheckConstraint(
            "question_type IN ('single_choice', 'multi_choice', 'scale', 'open_text')",
            name="questions_question_type",
        ),
        Index("idx_questions_version", "version_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    version_id = Column(BIGINT, fk("test_versions.id", "CASCADE"), nullable=False)
    order_num = Column(Integer, nullable=False)
    code = Column(String(50), nullable=False)
    text = Column(Text, nullable=False)
    question_type = Column(String(30), nullable=False, default=QuestionType.SINGLE_CHOICE.value)
    is_required = Column(Boolean, nullable=False, default=True)

    # --- связи ---
    version: TestVersion = relationship("TestVersion", back_populates="questions")
    answer_options: list["AnswerOption"] = relationship(
        "AnswerOption",
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="AnswerOption.order_num",
    )
    attempt_answers: list["AttemptAnswer"] = relationship(
        "AttemptAnswer", back_populates="question", passive_deletes=True
    )

    # --- хелперы ---
    @property
    def type_enum(self) -> QuestionType:
        return QuestionType(self.question_type)

    @property
    def has_options(self) -> bool:
        """Есть варианты ответа (радиокнопки / чекбоксы / шкала Ликерта)."""
        return self.question_type in (
            QuestionType.SINGLE_CHOICE.value,
            QuestionType.MULTI_CHOICE.value,
            QuestionType.SCALE.value,
        )

    @property
    def is_open_text(self) -> bool:
        """Открытый ответ — требует ручной оценки методистом."""
        return self.question_type == QuestionType.OPEN_TEXT.value

    @property
    def is_multi(self) -> bool:
        return self.question_type == QuestionType.MULTI_CHOICE.value

    def __repr__(self) -> str:
        return f"<Question #{self.order_num} {self.code} {self.question_type}>"


class AnswerOption(Base):
    """`answer_options` — вариант ответа на вопрос."""

    __tablename__ = "answer_options"
    __table_args__ = (
        UniqueConstraint("question_id", "order_num", name="uq_answer_options_order"),
        Index("idx_answer_options_q", "question_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    question_id = Column(BIGINT, fk("questions.id", "CASCADE"), nullable=False)
    order_num = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    raw_value = Column(String(50), nullable=True)    # значение шкалы Ликерта (1..5 и т.п.)
    is_correct = Column(Boolean, nullable=True)     # ключ (прямой / обратный вопрос)

    question: Question = relationship("Question", back_populates="answer_options")
    scores: list["OptionScore"] = relationship(
        "OptionScore", back_populates="option", cascade="all, delete-orphan"
    )
    attempt_answer_options: list["AttemptAnswerOption"] = relationship(
        "AttemptAnswerOption", back_populates="option", passive_deletes=True
    )

    @property
    def is_key(self) -> bool:
        return bool(self.is_correct)

    def __repr__(self) -> str:
        return f"<AnswerOption #{self.order_num} '{self.text[:30]}'>"


class OptionScore(Base):
    """`option_scores` — матрица ключей: баллы варианта ответа по каждой шкале."""

    __tablename__ = "option_scores"
    __table_args__ = (
        UniqueConstraint("option_id", "scale_id", name="uq_option_scores_option_scale"),
        Index("idx_option_scores_scale", "scale_id", "option_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    option_id = Column(BIGINT, fk("answer_options.id", "CASCADE"), nullable=False)
    scale_id = Column(BIGINT, fk("scales.id", "CASCADE"), nullable=False)
    score = Column(Numeric(10, 2), nullable=False, default=Decimal("0"))

    option: AnswerOption = relationship("AnswerOption", back_populates="scores")
    scale: Scale = relationship("Scale", back_populates="option_scores")

    def __repr__(self) -> str:
        return f"<OptionScore option={self.option_id} scale={self.scale_id} ={self.score}>"


# ===========================================================================
# 5. ПОПЫТКИ, ОТВЕТЫ И РЕЗУЛЬТАТЫ
# ===========================================================================


class Attempt(Base):
    """`attempts` — попытка прохождения версии теста пользователем."""

    __tablename__ = "attempts"
    __table_args__ = (
        CheckConstraint("status IN ('started', 'completed', 'aborted')", name="attempts_status"),
        Index("idx_attempts_user", "user_id", "version_id"),
        Index("idx_attempts_status", "status"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    user_id = Column(BIGINT, fk("users.id", "CASCADE"), nullable=False)
    version_id = Column(BIGINT, fk("test_versions.id"), nullable=False)
    sequence_id = Column(BIGINT, fk("test_sequences.id"), nullable=True)
    started_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default=AttemptStatus.STARTED.value)
    total_score = Column(Numeric(10, 2), nullable=True)
    ip = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)

    # --- связи ---
    user: User = relationship("User", back_populates="attempts")
    version: TestVersion = relationship("TestVersion", back_populates="attempts")
    sequence: Optional[TestSequence] = relationship("TestSequence", back_populates="attempts")
    answers: list["AttemptAnswer"] = relationship(
        "AttemptAnswer",
        back_populates="attempt",
        cascade="all, delete-orphan",
        order_by="AttemptAnswer.question_id",
    )
    scale_results: list["AttemptScaleResult"] = relationship(
        "AttemptScaleResult", back_populates="attempt", cascade="all, delete-orphan"
    )
    total_result: Optional["AttemptTotalResult"] = relationship(
        "AttemptTotalResult",
        back_populates="attempt",
        uselist=False,
        cascade="all, delete-orphan",
    )
    progress_records: list["UserTestProgress"] = relationship("UserTestProgress", back_populates="attempt")

    # --- хелперы ---
    @property
    def is_finished(self) -> bool:
        return self.status in (AttemptStatus.COMPLETED.value, AttemptStatus.ABORTED.value)

    @property
    def is_completed(self) -> bool:
        return self.status == AttemptStatus.COMPLETED.value

    @property
    def duration_seconds(self) -> Optional[int]:
        """Длительность попытки в секундах."""
        if self.started_at and self.finished_at:
            return int((self.finished_at - self.started_at).total_seconds())
        return None

    @property
    def unanswered_questions(self) -> list[Question]:
        """Вопросы версии, на которые ещё нет ответа в этой попытке."""
        answered = {answer.question_id for answer in self.answers}
        return [q for q in self.version.questions if q.id not in answered]

    def finish(
        self,
        status: str = AttemptStatus.COMPLETED.value,
        at: Optional[datetime] = None,
    ) -> None:
        """Завершение попытки с фиксацией времени и статуса."""
        self.status = status
        self.finished_at = at or utcnow()

    def __repr__(self) -> str:
        return f"<Attempt user={self.user_id} version={self.version_id} {self.status}>"


class AttemptAnswer(Base):
    """`attempt_answers` — ответ пользователя на вопрос в конкретной попытке."""

    __tablename__ = "attempt_answers"
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_id", name="uq_attempt_answers_attempt_question"),
        Index("idx_attempt_answers_att", "attempt_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    attempt_id = Column(BIGINT, fk("attempts.id", "CASCADE"), nullable=False)
    question_id = Column(BIGINT, fk("questions.id"), nullable=False)
    answer_text = Column(Text, nullable=True)              # для open_text
    answered_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    # --- связи ---
    attempt: Attempt = relationship("Attempt", back_populates="answers")
    question: Question = relationship("Question", back_populates="attempt_answers")
    selected_options: list["AttemptAnswerOption"] = relationship(
        "AttemptAnswerOption", back_populates="attempt_answer", cascade="all, delete-orphan"
    )
    manual_scores: list["ManualAnswerScore"] = relationship(
        "ManualAnswerScore", back_populates="attempt_answer", cascade="all, delete-orphan"
    )

    # --- хелперы ---
    @property
    def selected_option_ids(self) -> list[int]:
        """ID выбранных вариантов ответа."""
        return [link.option_id for link in self.selected_options]

    @property
    def is_manual_checked(self) -> bool:
        """Есть ли ручная оценка открытого ответа."""
        return bool(self.manual_scores)

    @property
    def is_empty(self) -> bool:
        """Ответ пустой (нет текста и не выбрано ни одного варианта)."""
        return not self.answer_text and not self.selected_options

    def __repr__(self) -> str:
        return f"<AttemptAnswer attempt={self.attempt_id} question={self.question_id}>"


class AttemptAnswerOption(Base):
    """`attempt_answer_options` — выбранные варианты ответа (одиночный/множественный/шкала)."""

    __tablename__ = "attempt_answer_options"
    __table_args__ = (
        UniqueConstraint(
            "attempt_answer_id", "option_id", name="uq_attempt_answer_options_pair"
        ),
        Index("idx_ao_attempt_answer", "attempt_answer_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    attempt_answer_id = Column(BIGINT, fk("attempt_answers.id", "CASCADE"), nullable=False)
    option_id = Column(BIGINT, fk("answer_options.id"), nullable=False)

    attempt_answer: AttemptAnswer = relationship("AttemptAnswer", back_populates="selected_options")
    option: AnswerOption = relationship("AnswerOption", back_populates="attempt_answer_options")

    def __repr__(self) -> str:
        return f"<AttemptAnswerOption answer={self.attempt_answer_id} option={self.option_id}>"


class ManualAnswerScore(Base):
    """`manual_answer_scores` — экспертная (ручная) оценка открытого ответа."""

    __tablename__ = "manual_answer_scores"
    __table_args__ = (
        UniqueConstraint(
            "attempt_answer_id", "scale_id", "expert_id", name="uq_manual_answer_scores_triple"
        ),
        Index("idx_manual_answer_scores_answer", "attempt_answer_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    attempt_answer_id = Column(BIGINT, fk("attempt_answers.id", "CASCADE"), nullable=False)
    scale_id = Column(BIGINT, fk("scales.id"), nullable=False)
    expert_id = Column(BIGINT, fk("users.id"), nullable=False)
    score = Column(Numeric(10, 2), nullable=False)
    comment = Column(Text, nullable=True)

    attempt_answer: AttemptAnswer = relationship("AttemptAnswer", back_populates="manual_scores")
    scale: Scale = relationship("Scale", back_populates="manual_scores")
    expert: User = relationship("User", back_populates="manual_scores", foreign_keys=[expert_id])

    def __repr__(self) -> str:
        return f"<ManualAnswerScore answer={self.attempt_answer_id} score={self.score}>"


class AttemptScaleResult(Base):
    """`attempt_scale_results` — результат попытки по конкретной шкале."""

    __tablename__ = "attempt_scale_results"
    __table_args__ = (
        UniqueConstraint("attempt_id", "scale_id", name="uq_attempt_scale_results_pair"),
        Index("idx_scale_results_att", "attempt_id"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    attempt_id = Column(BIGINT, fk("attempts.id", "CASCADE"), nullable=False)
    scale_id = Column(BIGINT, fk("scales.id"), nullable=False)
    raw_score = Column(Numeric(10, 2), nullable=False)        # сырой балл по шкале
    normalized_score = Column(Numeric(10, 2), nullable=True)  # нормализованный балл (0..100)
    interpretation = Column(Text, nullable=True)              # текст интерпретации

    attempt: Attempt = relationship("Attempt", back_populates="scale_results")
    scale: Scale = relationship("Scale", back_populates="attempt_scale_results")

    @property
    def level(self) -> Optional[str]:
        """Уровень выраженности (низкий / средний / высокий) по диапазону шкалы."""
        return self.scale.interpret(self.raw_score)

    def __repr__(self) -> str:
        return f"<AttemptScaleResult attempt={self.attempt_id} scale={self.scale_id} {self.raw_score}>"


class AttemptTotalResult(Base):
    """`attempt_total_results` — итоговый балл попытки (1:1 к попытке)."""

    __tablename__ = "attempt_total_results"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    attempt_id = Column(BIGINT, fk("attempts.id", "CASCADE"), nullable=False, unique=True)
    total_score = Column(Numeric(10, 2), nullable=False)
    interpretation = Column(Text, nullable=True)

    attempt: Attempt = relationship("Attempt", back_populates="total_result")

    def __repr__(self) -> str:
        return f"<AttemptTotalResult attempt={self.attempt_id} total={self.total_score}>"


# ===========================================================================
# 6. АУДИТ И ЛОГИРОВАНИЕ (модуль аудита)
# ===========================================================================


class AuditLog(Base):
    """`audit_log` — журнал значимых действий пользователей и системы."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_user_date", "user_id", "created_at"),
    )

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    user_id = Column(BIGINT, fk("users.id"), nullable=True)   # NULL — системное действие
    action = Column(String(50), nullable=False, index=True)    # login / create / update / delete ...
    entity_type = Column(String(50), nullable=False)          # test / question / user ...
    entity_id = Column(BIGINT, nullable=True)
    details = Column(JSON_TYPE, nullable=True)                # JSONB: контекст изменения
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    user: Optional[User] = relationship("User", back_populates="audit_logs")

    @classmethod
    def make(
        cls,
        *,
        user_id: Optional[int] = None,
        action: str,
        entity_type: str,
        entity_id: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> "AuditLog":
        """Фабрика записи журнала аудита."""
        return cls(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.entity_type}:{self.entity_id} by user={self.user_id}>"


__all__ = [
    "AnswerOption",
    "AssignmentStatus",
    "Attempt",
    "AttemptAnswer",
    "AttemptAnswerOption",
    "AttemptScaleResult",
    "AttemptStatus",
    "AttemptTotalResult",
    "AuditLog",
    "OptionScore",
    "PERMISSIONS",
    "Permission",
    "ProgressStatus",
    "Question",
    "QuestionType",
    "ROLE_ADMIN",
    "ROLE_METHODIST",
    "ROLE_STUDENT",
    "Role",
    "RolePermission",
    "Scale",
    "ScaleDependency",
    "ScaleOperation",
    "ScaleType",
    "SequenceItem",
    "Test",
    "TestSequence",
    "TestVersion",
    "User",
    "UserRole",
    "UserSequence",
    "UserTestProgress",
    "ManualAnswerScore",
    "utcnow",
]
