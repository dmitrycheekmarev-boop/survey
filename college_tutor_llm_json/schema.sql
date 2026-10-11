-- attempts раньше user_test_progress: на неё внешний ключ

CREATE TABLE roles (
    id          BIGSERIAL PRIMARY KEY,
    code        VARCHAR(50)  NOT NULL UNIQUE,
    name        VARCHAR(100) NOT NULL,
    description TEXT
);

CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    login         VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name     VARCHAR(200) NOT NULL,
    email         VARCHAR(255) UNIQUE,
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP
);

CREATE TABLE user_roles (
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id     BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    assigned_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE permissions (
    id          BIGSERIAL PRIMARY KEY,
    code        VARCHAR(100) NOT NULL UNIQUE,
    name        VARCHAR(200) NOT NULL,
    description TEXT
);

CREATE TABLE role_permissions (
    role_id       BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id BIGINT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE tests (
    id          BIGSERIAL PRIMARY KEY,
    code        VARCHAR(50)  NOT NULL UNIQUE,
    title       VARCHAR(255) NOT NULL,
    description TEXT,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by  BIGINT       NOT NULL REFERENCES users(id),
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP
);

CREATE TABLE test_versions (
    id           BIGSERIAL PRIMARY KEY,
    test_id      BIGINT       NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    version_no   INTEGER      NOT NULL,
    instruction  TEXT,
    is_published BOOLEAN      NOT NULL DEFAULT FALSE,
    created_by   BIGINT       NOT NULL REFERENCES users(id),
    created_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
    published_at TIMESTAMP,
    UNIQUE (test_id, version_no)
);

CREATE TABLE test_sequences (
    id          BIGSERIAL PRIMARY KEY,
    title       VARCHAR(255) NOT NULL,
    description TEXT,
    created_by  BIGINT       NOT NULL REFERENCES users(id),
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE TABLE sequence_items (
    id          BIGSERIAL PRIMARY KEY,
    sequence_id BIGINT  NOT NULL REFERENCES test_sequences(id) ON DELETE CASCADE,
    test_id     BIGINT  NOT NULL REFERENCES tests(id),
    order_num   INTEGER NOT NULL,
    is_required BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (sequence_id, order_num),
    UNIQUE (sequence_id, test_id)
);

CREATE TABLE user_sequences (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sequence_id  BIGINT      NOT NULL REFERENCES test_sequences(id),
    assigned_by  BIGINT      NOT NULL REFERENCES users(id),
    assigned_at  TIMESTAMP   NOT NULL DEFAULT NOW(),
    status       VARCHAR(20) NOT NULL DEFAULT 'assigned'
                 CHECK (status IN ('assigned','in_progress','completed')),
    UNIQUE (user_id, sequence_id)
);

CREATE TABLE attempts (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    version_id  BIGINT       NOT NULL REFERENCES test_versions(id),
    sequence_id BIGINT       REFERENCES test_sequences(id),
    started_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMP,
    status      VARCHAR(20)  NOT NULL DEFAULT 'started'
                CHECK (status IN ('started','completed','aborted')),
    total_score NUMERIC(10,2),
    ip          VARCHAR(45),
    user_agent  TEXT
);

CREATE TABLE user_test_progress (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sequence_id  BIGINT      NOT NULL REFERENCES test_sequences(id),
    test_id      BIGINT      NOT NULL REFERENCES tests(id),
    status       VARCHAR(20) NOT NULL DEFAULT 'not_started'
                 CHECK (status IN ('not_started','in_progress','completed')),
    attempt_id   BIGINT      REFERENCES attempts(id),
    completed_at TIMESTAMP,
    UNIQUE (user_id, sequence_id, test_id)
);

CREATE TABLE scales (
    id             BIGSERIAL PRIMARY KEY,
    version_id     BIGINT       NOT NULL REFERENCES test_versions(id) ON DELETE CASCADE,
    code           VARCHAR(50)  NOT NULL,
    name           VARCHAR(200) NOT NULL,
    description    TEXT,
    scale_type     VARCHAR(20)  NOT NULL
                   CHECK (scale_type IN ('sum','average','count','composite')),
    min_score      NUMERIC(10,2),
    max_score      NUMERIC(10,2),
    interpretation TEXT,
    UNIQUE (version_id, code)
);

CREATE TABLE scale_dependencies (
    id                 BIGSERIAL PRIMARY KEY,
    composite_scale_id BIGINT NOT NULL REFERENCES scales(id) ON DELETE CASCADE,
    component_scale_id BIGINT NOT NULL REFERENCES scales(id),
    coefficient        NUMERIC(10,4) NOT NULL DEFAULT 1,
    operation          VARCHAR(3)    NOT NULL DEFAULT '+'
                       CHECK (operation IN ('+','-','*','/')),
    UNIQUE (composite_scale_id, component_scale_id)
);

CREATE TABLE questions (
    id            BIGSERIAL PRIMARY KEY,
    version_id    BIGINT       NOT NULL REFERENCES test_versions(id) ON DELETE CASCADE,
    order_num     INTEGER      NOT NULL,
    code          VARCHAR(50)  NOT NULL,
    text          TEXT         NOT NULL,
    question_type VARCHAR(30)  NOT NULL
                  CHECK (question_type IN ('single_choice','multi_choice','scale','open_text')),
    is_required   BOOLEAN      NOT NULL DEFAULT TRUE,
    UNIQUE (version_id, code),
    UNIQUE (version_id, order_num)
);

CREATE TABLE answer_options (
    id          BIGSERIAL PRIMARY KEY,
    question_id BIGINT      NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    order_num   INTEGER     NOT NULL,
    text        TEXT        NOT NULL,
    raw_value   VARCHAR(50),
    is_correct  BOOLEAN,
    UNIQUE (question_id, order_num)
);

CREATE TABLE option_scores (
    id        BIGSERIAL PRIMARY KEY,
    option_id BIGINT        NOT NULL REFERENCES answer_options(id) ON DELETE CASCADE,
    scale_id  BIGINT        NOT NULL REFERENCES scales(id) ON DELETE CASCADE,
    score     NUMERIC(10,2) NOT NULL DEFAULT 0,
    UNIQUE (option_id, scale_id)
);

CREATE TABLE attempt_answers (
    id          BIGSERIAL PRIMARY KEY,
    attempt_id  BIGINT    NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    question_id BIGINT    NOT NULL REFERENCES questions(id),
    answer_text TEXT,
    answered_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (attempt_id, question_id)
);

CREATE TABLE attempt_answer_options (
    id                BIGSERIAL PRIMARY KEY,
    attempt_answer_id BIGINT NOT NULL REFERENCES attempt_answers(id) ON DELETE CASCADE,
    option_id         BIGINT NOT NULL REFERENCES answer_options(id),
    UNIQUE (attempt_answer_id, option_id)
);

CREATE TABLE manual_answer_scores (
    id                BIGSERIAL PRIMARY KEY,
    attempt_answer_id BIGINT        NOT NULL REFERENCES attempt_answers(id) ON DELETE CASCADE,
    scale_id          BIGINT        NOT NULL REFERENCES scales(id),
    expert_id         BIGINT        NOT NULL REFERENCES users(id),
    score             NUMERIC(10,2) NOT NULL,
    comment           TEXT,
    UNIQUE (attempt_answer_id, scale_id, expert_id)
);

CREATE TABLE attempt_scale_results (
    id               BIGSERIAL PRIMARY KEY,
    attempt_id       BIGINT        NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    scale_id         BIGINT        NOT NULL REFERENCES scales(id),
    raw_score        NUMERIC(10,2) NOT NULL,
    normalized_score NUMERIC(10,2),
    interpretation   TEXT,
    UNIQUE (attempt_id, scale_id)
);

CREATE TABLE attempt_total_results (
    id             BIGSERIAL PRIMARY KEY,
    attempt_id     BIGINT        NOT NULL UNIQUE REFERENCES attempts(id) ON DELETE CASCADE,
    total_score    NUMERIC(10,2) NOT NULL,
    interpretation TEXT
);

CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT      REFERENCES users(id),
    action      VARCHAR(50) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id   BIGINT,
    details     JSONB,
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_attempts_user         ON attempts(user_id, version_id);
CREATE INDEX idx_attempts_status       ON attempts(status);
CREATE INDEX idx_attempt_answers_att   ON attempt_answers(attempt_id);
CREATE INDEX idx_ao_attempt_answer     ON attempt_answer_options(attempt_answer_id);
CREATE INDEX idx_option_scores_scale   ON option_scores(scale_id, option_id);
CREATE INDEX idx_questions_version     ON questions(version_id);
CREATE INDEX idx_answer_options_q      ON answer_options(question_id);
CREATE INDEX idx_scales_version        ON scales(version_id);
CREATE INDEX idx_scale_results_att     ON attempt_scale_results(attempt_id);
CREATE INDEX idx_utp_user_seq          ON user_test_progress(user_id, sequence_id);
CREATE INDEX idx_audit_entity          ON audit_log(entity_type, entity_id);
