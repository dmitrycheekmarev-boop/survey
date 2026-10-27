-- ============================================================
-- СОЗДАНИЕ ТАБЛИЦ (если их ещё нет)
-- ============================================================

CREATE TABLE IF NOT EXISTS tests (
  id BIGSERIAL PRIMARY KEY,
  code VARCHAR(50) NOT NULL UNIQUE,
  title VARCHAR(255) NOT NULL,
  description TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_by BIGINT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS test_versions (
  id BIGSERIAL PRIMARY KEY,
  test_id BIGINT NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  instruction TEXT,
  is_published BOOLEAN NOT NULL DEFAULT FALSE,
  created_by BIGINT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  published_at TIMESTAMP,
  UNIQUE (test_id, version_no)
);

CREATE TABLE IF NOT EXISTS scales (
  id BIGSERIAL PRIMARY KEY,
  version_id BIGINT NOT NULL REFERENCES test_versions(id) ON DELETE CASCADE,
  code VARCHAR(50) NOT NULL,
  name VARCHAR(200) NOT NULL,
  description TEXT,
  scale_type VARCHAR(20) NOT NULL CHECK (scale_type IN ('sum','average','count','composite')),
  min_score NUMERIC(10,2),
  max_score NUMERIC(10,2),
  interpretation TEXT,
  UNIQUE (version_id, code)
);

CREATE TABLE IF NOT EXISTS questions (
  id BIGSERIAL PRIMARY KEY,
  version_id BIGINT NOT NULL REFERENCES test_versions(id) ON DELETE CASCADE,
  order_num INTEGER NOT NULL,
  code VARCHAR(50) NOT NULL,
  text TEXT NOT NULL,
  question_type VARCHAR(30) NOT NULL CHECK (question_type IN ('single_choice','multi_choice','scale','open_text')),
  is_required BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (version_id, code),
  UNIQUE (version_id, order_num)
);

CREATE TABLE IF NOT EXISTS answer_options (
  id BIGSERIAL PRIMARY KEY,
  question_id BIGINT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  order_num INTEGER NOT NULL,
  text TEXT NOT NULL,
  raw_value VARCHAR(50),
  is_correct BOOLEAN,
  UNIQUE (question_id, order_num)
);

CREATE TABLE IF NOT EXISTS option_scores (
  id BIGSERIAL PRIMARY KEY,
  option_id BIGINT NOT NULL REFERENCES answer_options(id) ON DELETE CASCADE,
  scale_id BIGINT NOT NULL REFERENCES scales(id) ON DELETE CASCADE,
  score NUMERIC(10,2) NOT NULL DEFAULT 0,
  UNIQUE (option_id, scale_id)
);

-- ============================================================
-- ТЕСТ 1: Критическое мышление (Корешникова, Фрумин, 2020)
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES (
        'critical_thinking',
        'Критическое мышление',
        'Тест на оценку критического мышления (Корешникова, Фрумин, 2020). 10 пунктов, шкала 1–4.',
        true,
        1
    )
    ON CONFLICT (code) DO UPDATE SET updated_at = now()
    RETURNING id
),
ins_version AS (
    INSERT INTO test_versions (test_id, version_no, instruction, is_published, created_by)
    SELECT
        id,
        1,
        'Оцените, пожалуйста, насколько хорошо Вы умеете делать следующее:',
        true,
        1
    FROM ins_test
    RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('info_processing',        'Работа с информацией',   'Субшкала: пункты 1–2',  'sum',  2.00,  8.00, 'Сумма баллов по пунктам 1–2.'),
        ('argument_selection',     'Подбор аргументов',     'Субшкала: пункты 3–6',  'sum',  4.00, 16.00, 'Сумма баллов по пунктам 3–6.'),
        ('conclusion_formulation', 'Формулировка выводов',  'Субшкала: пункты 7–10', 'sum',  4.00, 16.00, 'Сумма баллов по пунктам 7–10.'),
        ('total',                  'Общий балл',            'Сумма всех 10 пунктов', 'sum', 10.00, 40.00, 'Общий балл по всем 10 пунктам.')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'scale', true
    FROM ins_version v
    CROSS JOIN (VALUES
        (1,  'q1',  'Оценивать актуальность информации'),
        (2,  'q2',  'Оценивать достоверность информации'),
        (3,  'q3',  'Определять информацию, которую возможно использовать как аргумент'),
        (4,  'q4',  'Относить аргументы к различным контекстам'),
        (5,  'q5',  'Оценивать убедительность аргумента'),
        (6,  'q6',  'Определять недостаточность информации в аргументации'),
        (7,  'q7',  'На основе представленной информации для аргументации выносить чёткое суждение'),
        (8,  'q8',  'Разрабатывать валидные выводы'),
        (9,  'q9',  'Оценивать причинно-следственные связи'),
        (10, 'q10', 'Создавать объяснения (отвечать на вопрос «почему?»)')
    ) AS q(order_num, code, text)
    RETURNING id
)
INSERT INTO answer_options (question_id, order_num, text, raw_value, is_correct)
SELECT
    q.id,
    a.order_num,
    a.text,
    a.raw_value,
    NULL
FROM ins_questions q
CROSS JOIN (VALUES
    (1, 'Плохо',             '1'),
    (2, 'Удовлетворительно', '2'),
    (3, 'Хорошо',            '3'),
    (4, 'Отлично',           '4')
) AS a(order_num, text, raw_value);

-- ============================================================
-- ТЕСТ 2: Индекс устойчивости идентичности
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES (
        'identity_stability',
        'Индекс устойчивости идентичности',
        'Тест на оценку устойчивости идентичности. 16 утверждений, шкала 1–5.',
        true,
        1
    )
    ON CONFLICT (code) DO UPDATE SET updated_at = now()
    RETURNING id
),
ins_version AS (
    INSERT INTO test_versions (test_id, version_no, instruction, is_published, created_by)
    SELECT
        id,
        1,
        'Прочтите каждое утверждение и определите, насколько Вы удовлетворены собой и своим поведением.',
        true,
        1
    FROM ins_test
    RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('self_esteem',   'Самооценка',        'Пункты 1, 2*, 3*, 4* (обратные: 2, 3, 4)', 'sum', 4.00, 20.00, 'Сумма баллов по пунктам 1–4 с учётом обратных.'),
        ('self_efficacy', 'Самоэффективность', 'Пункты 5, 6, 7, 8',                         'sum', 4.00, 20.00, 'Сумма баллов по пунктам 5–8.'),
        ('integrity',     'Целостность',       'Пункты 9, 10, 11, 12',                      'sum', 4.00, 20.00, 'Сумма баллов по пунктам 9–12.'),
        ('uniqueness',    'Уникальность',      'Пункты 13, 14*, 15, 16 (обратный: 14)',     'sum', 4.00, 20.00, 'Сумма баллов по пунктам 13–16 с учётом обратного.')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'scale', true
    FROM ins_version v
    CROSS JOIN (VALUES
        (1,  'q1',  'В целом я доволен собой'),
        (2,  'q2',  'Я чувствую, что мне нечем гордиться'),
        (3,  'q3',  'Иногда я чувствую себя бесполезным'),
        (4,  'q4',  'По большому счету я склонен считать себя неудачником'),
        (5,  'q5',  'Мне всегда удается решить сложные проблемы, если я достаточно стараюсь'),
        (6,  'q6',  'Если кто-то сопротивляется мне, я найду средства и способы получить то, что хочу'),
        (7,  'q7',  'Я уверен, что смогу эффективно справиться с неожиданными событиями'),
        (8,  'q8',  'Благодаря своей находчивости я знаю, как справляться с непредвиденными ситуациями'),
        (9,  'q9',  'Мое прошлое и настоящее неразрывно связаны друг с другом'),
        (10, 'q10', 'Мое настоящее — просто продолжение прошлого'),
        (11, 'q11', 'Между моим прошлым и настоящим существует непрерывность'),
        (12, 'q12', 'Мое прошлое прекрасно сливается с моим настоящим'),
        (13, 'q13', 'Я чувствую себя уникальным'),
        (14, 'q14', 'Я не думаю, что имею много особенных характеристик, которые отличают меня от других'),
        (15, 'q15', 'Я думаю, что мои характеристики отличаются от характеристик других'),
        (16, 'q16', 'Я чувствую, что некоторые мои характеристики полностью уникальны для меня')
    ) AS q(order_num, code, text)
    RETURNING id
)
INSERT INTO answer_options (question_id, order_num, text, raw_value, is_correct)
SELECT
    q.id,
    a.order_num,
    a.text,
    a.raw_value,
    NULL
FROM ins_questions q
CROSS JOIN (VALUES
    (1, 'Совсем не согласен',                     '1'),
    (2, 'Не согласен',                            '2'),
    (3, 'Частично согласен частично не согласен', '3'),
    (4, 'Согласен',                               '4'),
    (5, 'Полностью согласен',                     '5')
) AS a(order_num, text, raw_value);

-- ============================================================
-- ТЕСТ 3: ДУМЭОЛП
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES (
        'dumeolp',
        'ДУМЭОЛП',
        'Опросник диагностики уровня морально-этической ответственности личности. 30 утверждений, ответы: +, -, 0.',
        true,
        1
    )
    ON CONFLICT (code) DO UPDATE SET updated_at = now()
    RETURNING id
),
ins_version AS (
    INSERT INTO test_versions (test_id, version_no, instruction, is_published, created_by)
    SELECT
        id,
        1,
        'Предлагаем принять участие в оценке особенностей Вашей личности. Если Вы согласны с утверждением — поставьте +, если нет — -, если затрудняетесь — 0.',
        true,
        1
    FROM ins_test
    RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('reflection',                'Рефлексия на морально-этические ситуации',
         'Пункты 3, 9, 21, 27 — нет; 15 — да', 'sum', 0.00, 5.00,
         'Диагностирует склонность к рефлексии в морально-этических ситуациях.'),
        ('intuition',                 'Интуиция в морально-этической сфере',
         'Пункты 2, 8, 14, 20, 26 — нет', 'sum', 0.00, 5.00,
         'Диагностирует роль интуиции в морально-этической сфере.'),
        ('existential_responsibility','Экзистенциальная ответственность',
         'Пункты 4, 10, 16, 22, 28 — нет', 'sum', 0.00, 5.00,
         'Диагностирует уровень экзистенциальной ответственности.'),
        ('altruistic_emotions',       'Альтруистические эмоции',
         'Пункты 1, 7, 13, 19, 25 — нет', 'sum', 0.00, 5.00,
         'Диагностирует выраженность альтруистических эмоций.'),
        ('moral_values',              'Морально-этические ценности',
         'Пункты 5, 11, 17, 23, 29 — нет', 'sum', 0.00, 5.00,
         'Диагностирует сформированность морально-этических ценностей.'),
        ('lie',                       'Шкала лжи',
         'Пункты 6, 12, 18, 24, 30 — да', 'sum', 0.00, 5.00,
         'Шкала лжи: диагностирует социальную желательность ответов.')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'single_choice', true
    FROM ins_version v
    CROSS JOIN (VALUES
        (1,  'q1',  'Мне кажется, что любовь в современном мире потеряла свою ценность'),
        (2,  'q2',  'Я думаю, что интуитивно оценить аморальность человеческого поступка не может никто'),
        (3,  'q3',  'Я думаю, что постоянный анализ своего поведения и поступков вещь нужная, но не необходимая'),
        (4,  'q4',  'В жизни я больше придерживаюсь принципа: "Мне нравится", а не "Я должен"'),
        (5,  'q5',  'Я думаю, что такие понятия как "долг" и "честь" утратили своё первостепенное значение'),
        (6,  'q6',  'Я никогда не пропускал интересных занятий в ВУЗе (колледже)'),
        (7,  'q7',  'Мне кажется, что бескорыстно помогать другим людям — способствовать развитию у них инфантилизма'),
        (8,  'q8',  'Советоваться с внутренним голосом, решая этические проблемы, свойственно людям со слабо развитым мышлением и рефлексией'),
        (9,  'q9',  'Люди, которые постоянно занимаются "самокопанием", обычно неуверенны в себе и мало адаптивны в обществе'),
        (10, 'q10', 'Мне кажется, что только сильная личность способна отвечать за свою жизнь и поступки'),
        (11, 'q11', 'Люди, которые постоянно борются за справедливость, на самом деле выскочки и завистники'),
        (12, 'q12', 'Я никогда никому не отказываю в помощи, даже если это ущемляет мои интересы'),
        (13, 'q13', 'Сочувствие и сопереживание другому человеку — это не решение проблемы, а уход от неё'),
        (14, 'q14', 'Я думаю, что моментально выбрать правильное решение в сложной этической ситуации не может никто'),
        (15, 'q15', 'Анализируя мотивы поведения других людей, я получаю удовольствие'),
        (16, 'q16', 'Я думаю, что человек не может полностью отвечать за то, что с ним происходит в жизни'),
        (17, 'q17', 'Если постоянно прислушиваться к голосу своей совести, можно остаться "без хлеба насущного"'),
        (18, 'q18', 'Я понимаю, что спорить с родителями бесполезно, поэтому никогда не делаю этого'),
        (19, 'q19', 'Искренняя радость за успехи другого человека является, на мой взгляд, обратной стороной зависти'),
        (20, 'q20', 'Усматривание в окружающих меня явлениях действия сил "добра и зла" кажется мне примитивным и устаревшим'),
        (21, 'q21', 'Если постоянно задумываться о правильности жизни, то и жизнь может пройти мимо'),
        (22, 'q22', 'Я думаю, что человек не обязан отвечать за кризисы и конфликты, произошедшие в его жизни'),
        (23, 'q23', 'Общество диктует человеку свои ценности и идеалы и никуда от этого не деться'),
        (24, 'q24', 'Я никогда не оцениваю людей'),
        (25, 'q25', 'Обычно чувство вины свойственно людям с низкой самооценкой, неуверенным в себе'),
        (26, 'q26', 'Люди, которые постоянно следуют голосу совести, выглядят смешно и не современно'),
        (27, 'q27', 'Я думаю, что нужно делать дело, а не задумываться над тем, как это отразится на людях'),
        (28, 'q28', 'Безответственность, иногда проявляемая человеком, бывает идёт на пользу окружающим'),
        (29, 'q29', 'Я думаю, что в работе иногда нужно прибегать к принципу: "Цель оправдывает средства"'),
        (30, 'q30', 'Манипулировать неэтично и я никогда этого не делаю')
    ) AS q(order_num, code, text)
    RETURNING id
)
INSERT INTO answer_options (question_id, order_num, text, raw_value, is_correct)
SELECT
    q.id,
    a.order_num,
    a.text,
    a.raw_value,
    NULL
FROM ins_questions q
CROSS JOIN (VALUES
    (1, '+', '+'),
    (2, '-', '-'),
    (3, '0', '0')
) AS a(order_num, text, raw_value);

-- ============================================================
-- БАЛЛЫ: Критическое мышление
-- ============================================================
INSERT INTO option_scores (option_id, scale_id, score)
SELECT
    ao.id                                   AS option_id,
    sc.id                                   AS scale_id,
    ao.raw_value::numeric                   AS score
FROM answer_options ao
JOIN questions q       ON ao.question_id = q.id
JOIN test_versions v   ON q.version_id   = v.id
JOIN tests t           ON v.test_id      = t.id
JOIN scales sc         ON sc.version_id  = v.id
WHERE t.code = 'critical_thinking'
  AND (
        (sc.code = 'info_processing'        AND q.order_num BETWEEN 1 AND 2)
     OR (sc.code = 'argument_selection'     AND q.order_num BETWEEN 3 AND 6)
     OR (sc.code = 'conclusion_formulation' AND q.order_num BETWEEN 7 AND 10)
     OR (sc.code = 'total'                  AND q.order_num BETWEEN 1 AND 10)
  );

-- ============================================================
-- ПРОВЕРКА
-- ============================================================
SELECT * FROM tests;
SELECT * FROM test_versions;
SELECT * FROM scales;
SELECT * FROM questions;
SELECT * FROM answer_options;