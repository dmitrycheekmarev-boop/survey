-- ============================================================
-- ТЕСТ: Краткая версия опросника ЭмИн Д.В. Люсина
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES (
        'emotional_intelligence_lusin',
        'Краткая версия опросника ЭмИн Д.В. Люсина',
        'Краткая версия опросника эмоционального интеллекта Д.В. Люсина. 8 утверждений, шкала 1–4.',
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
        'Прочитайте внимательно каждое утверждение и отметьте степень согласия с каждым из них по шкале от «совсем не согласен» до «полностью согласен».',
        true,
        1
    FROM ins_test
    ON CONFLICT (test_id, version_no) DO UPDATE
        SET instruction = EXCLUDED.instruction
    RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('vp',  'ВП',  'Понимание своих эмоций',     'Пункты 1, 5 (5 — обратный)',           'sum',        2.00,  8.00, 'Сумма баллов по пунктам 1, 5 с учётом обратного по п. 5.'),
        ('vu',  'ВУ',  'Управление своими эмоциями', 'Пункты 6 (обратный), 8',               'sum',        2.00,  8.00, 'Сумма баллов по пунктам 6, 8 с учётом обратного по п. 6.'),
        ('mp',  'МП',  'Понимание чужих эмоций',     'Пункты 3, 7',                          'sum',        2.00,  8.00, 'Сумма баллов по пунктам 3, 7.'),
        ('mu',  'МУ',  'Управление чужими эмоциями', 'Пункты 2 (обратный), 4 (обратный)',    'sum',        2.00,  8.00, 'Сумма баллов по пунктам 2, 4 с учётом обратных.'),
        ('vei', 'ВЭИ', 'Внутриличностный ЭИ',        'ВП + ВУ',                              'composite',  4.00, 16.00, 'Составная шкала: ВП + ВУ.'),
        ('mei', 'МЭИ', 'Межличностный ЭИ',           'МП + МУ',                              'composite',  4.00, 16.00, 'Составная шкала: МП + МУ.'),
        ('oei', 'ОЭИ', 'Общий ЭИ',                   'ВЭИ + МЭИ',                            'composite',  8.00, 32.00, 'Составная шкала: ВЭИ + МЭИ.')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
    ON CONFLICT (version_id, code) DO UPDATE
        SET name = EXCLUDED.name
    RETURNING id
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'scale', true
    FROM ins_version v
    CROSS JOIN (VALUES
        (1, 'q1', 'Как правило, я понимаю, какую эмоцию испытываю'),
        (2, 'q2', 'У меня обычно не получается повлиять на эмоциональное состояние своего собеседника'),
        (3, 'q3', 'Я понимаю душевное состояние некоторых людей без слов'),
        (4, 'q4', 'Я не умею управлять эмоциями других людей'),
        (5, 'q5', 'У меня бывают чувства, которые я не могу точно определить'),
        (6, 'q6', 'Когда я раздражаюсь, то не могу сдержаться и говорю все, что думаю'),
        (7, 'q7', 'Глядя на человека, я легко могу понять его эмоциональное состояние'),
        (8, 'q8', 'Я умею контролировать свои эмоции')
    ) AS q(order_num, code, text)
    ON CONFLICT (version_id, code) DO UPDATE
        SET text = EXCLUDED.text
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
    (1, 'Совсем не согласен', '1'),
    (2, 'Скорее не согласен', '2'),
    (3, 'Скорее согласен',    '3'),
    (4, 'Полностью согласен', '4')
) AS a(order_num, text, raw_value)
ON CONFLICT (question_id, order_num) DO UPDATE
    SET text = EXCLUDED.text;

-- ============================================================
-- option_scores для ЭмИн (обратные: 2, 4, 5, 6 → 5 - raw)
-- ============================================================
INSERT INTO option_scores (option_id, scale_id, score)
SELECT
    ao.id                                   AS option_id,
    sc.id                                   AS scale_id,
    CASE
        WHEN q.order_num IN (2, 4, 5, 6) THEN 5 - ao.raw_value::numeric
        ELSE ao.raw_value::numeric
    END                                     AS score
FROM answer_options ao
JOIN questions q       ON ao.question_id = q.id
JOIN test_versions v   ON q.version_id   = v.id
JOIN tests t           ON v.test_id      = t.id
JOIN scales sc         ON sc.version_id  = v.id
WHERE t.code = 'emotional_intelligence_lusin'
  AND (
        (sc.code = 'vp' AND q.order_num IN (1, 5))
     OR (sc.code = 'vu' AND q.order_num IN (6, 8))
     OR (sc.code = 'mp' AND q.order_num IN (3, 7))
     OR (sc.code = 'mu' AND q.order_num IN (2, 4))
  )
ON CONFLICT (option_id, scale_id) DO UPDATE
    SET score = EXCLUDED.score;

-- ============================================================
-- ТЕСТ: Черты характера (Русалов)
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES (
        'character_traits_rusalov',
        'Черты характера',
        'Опросник черт характера (Русалов). 20 утверждений, шкала 1–5.',
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
        'Вам предлагается список утверждений, характеризующих поведение человека. Некоторые черты поведения у вас могут быть более выраженными, другие — менее. Укажите степень выраженности каждой из предъявляемых характеристик.',
        true,
        1
    FROM ins_test
    ON CONFLICT (test_id, version_no) DO UPDATE
        SET instruction = EXCLUDED.instruction
    RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('hyperthymia',      'Гипертимность',      'Пункты 1, 2',   'sum', 2.00, 10.00, 'Сумма баллов по пунктам 1, 2.'),
        ('stuck',            'Застревание',        'Пункты 3, 4',   'sum', 2.00, 10.00, 'Сумма баллов по пунктам 3, 4.'),
        ('emotivity',        'Эмотивность',        'Пункты 5, 6',   'sum', 2.00, 10.00, 'Сумма баллов по пунктам 5, 6.'),
        ('pedantry',         'Педантичность',      'Пункты 7, 8',   'sum', 2.00, 10.00, 'Сумма баллов по пунктам 7, 8.'),
        ('anxiety',          'Тревожность',        'Пункты 9, 10',  'sum', 2.00, 10.00, 'Сумма баллов по пунктам 9, 10.'),
        ('cyclothymia',      'Циклотимность',      'Пункты 11, 12', 'sum', 2.00, 10.00, 'Сумма баллов по пунктам 11, 12.'),
        ('demonstrativnost', 'Демонстративность',  'Пункты 13, 14', 'sum', 2.00, 10.00, 'Сумма баллов по пунктам 13, 14.'),
        ('excitability',     'Возбудимость',       'Пункты 15, 16', 'sum', 2.00, 10.00, 'Сумма баллов по пунктам 15, 16.'),
        ('dysthymia',        'Дистимность',        'Пункты 17, 18', 'sum', 2.00, 10.00, 'Сумма баллов по пунктам 17, 18.'),
        ('exaltation',       'Экзальтированность', 'Пункты 19, 20', 'sum', 2.00, 10.00, 'Сумма баллов по пунктам 19, 20.')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
    ON CONFLICT (version_id, code) DO UPDATE
        SET name = EXCLUDED.name
    RETURNING id
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'scale', true
    FROM ins_version v
    CROSS JOIN (VALUES
        (1,  'q1',  'Я заражаю людей оптимизмом'),
        (2,  'q2',  'Я легко занимаюсь организаторской деятельностью'),
        (3,  'q3',  'Я с трудом забываю нанесенные мне незначительные обиды'),
        (4,  'q4',  'Оскорбления, нанесенные мне, вызывают у меня скрытую ярость'),
        (5,  'q5',  'Я — чувствительный, мягкосердечный человек'),
        (6,  'q6',  'Трагические фильмы могут взволновать меня до слез'),
        (7,  'q7',  'Я слежу за тем, чтобы каждая вещь лежала на своем месте'),
        (8,  'q8',  'Я стремлюсь навести порядок везде и во всем'),
        (9,  'q9',  'Я боюсь спуститься в темный погреб'),
        (10, 'q10', 'Я — боязливый человек'),
        (11, 'q11', 'У меня бывают беспричинные колебания настроения'),
        (12, 'q12', 'Период хорошего настроения у меня может смениться апатией'),
        (13, 'q13', 'Я постоянно стремлюсь быть в центре внимания'),
        (14, 'q14', 'Я прекрасно чувствую себя на сцене'),
        (15, 'q15', 'Я готов ударить человека, если он меня сильно оскорбил'),
        (16, 'q16', 'Когда меня задевают за «больное», я могу ударить'),
        (17, 'q17', 'Я замкнутый, немногословный человек'),
        (18, 'q18', 'Я долгое время могу быть в подавленном настроении'),
        (19, 'q19', 'Мое радостное настроение резко сменяется глубокой печалью'),
        (20, 'q20', 'Я подвержен частым перепадам настроения — от радости к печали')
    ) AS q(order_num, code, text)
    ON CONFLICT (version_id, code) DO UPDATE
        SET text = EXCLUDED.text
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
    (1, 'не выражена',         '1'),
    (2, 'скорее не выражена',  '2'),
    (3, 'выражена',            '3'),
    (4, 'заметно выражена',    '4'),
    (5, 'очень выражена',      '5')
) AS a(order_num, text, raw_value)
ON CONFLICT (question_id, order_num) DO UPDATE
    SET text = EXCLUDED.text;

-- ============================================================
-- option_scores для "Черты характера" (обратных нет)
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
WHERE t.code = 'character_traits_rusalov'
  AND (
        (sc.code = 'hyperthymia'      AND q.order_num IN (1, 2))
     OR (sc.code = 'stuck'            AND q.order_num IN (3, 4))
     OR (sc.code = 'emotivity'        AND q.order_num IN (5, 6))
     OR (sc.code = 'pedantry'         AND q.order_num IN (7, 8))
     OR (sc.code = 'anxiety'          AND q.order_num IN (9, 10))
     OR (sc.code = 'cyclothymia'      AND q.order_num IN (11, 12))
     OR (sc.code = 'demonstrativnost' AND q.order_num IN (13, 14))
     OR (sc.code = 'excitability'     AND q.order_num IN (15, 16))
     OR (sc.code = 'dysthymia'        AND q.order_num IN (17, 18))
     OR (sc.code = 'exaltation'       AND q.order_num IN (19, 20))
  )
ON CONFLICT (option_id, scale_id) DO UPDATE
    SET score = EXCLUDED.score;

-- ============================================================
-- ТЕСТ: Фундаментальные свойства личности (Айзенк)
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES (
        'fundamental_properties_eysenck',
        'Фундаментальные свойства личности (Айзенк)',
        'Опросник фундаментальных свойств личности (Айзенк). 21 утверждение, шкала 1–5.',
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
        'Вам предлагается список утверждений, характеризующих поведение человека. Укажите степень выраженности каждой из предъявляемых характеристик.',
        true,
        1
    FROM ins_test
    ON CONFLICT (test_id, version_no) DO UPDATE
        SET instruction = EXCLUDED.instruction
    RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('extraversion', 'Экстраверсия/интроверсия',
         'Пункты 1, 3, 6, 11, 13, 15, 20', 'sum', 7.00, 35.00,
         'Сумма баллов по пунктам 1, 3, 6, 11, 13, 15, 20.'),
        ('neuroticism',  'Нейротизм/эмоциональная стабильность',
         'Пункты 2, 5, 7, 10, 14, 17, 18', 'sum', 7.00, 35.00,
         'Сумма баллов по пунктам 2, 5, 7, 10, 14, 17, 18.'),
        ('psychoticism', 'Психотизм/мягкосердечие',
         'Пункты 4, 8, 9, 12, 16, 19, 21', 'sum', 7.00, 35.00,
         'Сумма баллов по пунктам 4, 8, 9, 12, 16, 19, 21.')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
    ON CONFLICT (version_id, code) DO UPDATE
        SET name = EXCLUDED.name
    RETURNING id
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'scale', true
    FROM ins_version v
    CROSS JOIN (VALUES
        (1,  'q1',  'Я — разговорчивый человек'),
        (2,  'q2',  'Меня легко обидеть'),
        (3,  'q3',  'Я легко общаюсь на вечеринках и в компаниях'),
        (4,  'q4',  'Для меня мало значат хорошие манеры и чистоплотность'),
        (5,  'q5',  'Меня часто беспокоит чувство вины'),
        (6,  'q6',  'У меня много друзей'),
        (7,  'q7',  'Я считаю себя нервным человеком'),
        (8,  'q8',  'Я безразличен к ошибкам, допущенным в своей работе'),
        (9,  'q9',  'Я считаю, что брак старомоден и его следует отменить'),
        (10, 'q10', 'Мои нервы редко бывают натянуты до предела'),
        (11, 'q11', 'Я много говорю в обществе других людей'),
        (12, 'q12', 'Я отвечаю на грубость тем же'),
        (13, 'q13', 'Мне по силам организовать вечеринку'),
        (14, 'q14', 'Я долго переживаю после случившегося конфуза'),
        (15, 'q15', 'Я легко вношу оживление в скучную компанию'),
        (16, 'q16', 'Мне безразличен вид страдающего животного'),
        (17, 'q17', 'Меня беспокоят мои нервы'),
        (18, 'q18', 'Я часто испытываю чувство одиночества'),
        (19, 'q19', 'Мне безразличен вид животного в капкане'),
        (20, 'q20', 'Мне нравится находиться среди людей'),
        (21, 'q21', 'Я бы принял лекарство с опасными побочными явлениями')
    ) AS q(order_num, code, text)
    ON CONFLICT (version_id, code) DO UPDATE
        SET text = EXCLUDED.text
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
    (1, 'не выражена',        '1'),
    (2, 'скорее не выражена', '2'),
    (3, 'выражена',           '3'),
    (4, 'заметно выражена',   '4'),
    (5, 'очень выражена',     '5')
) AS a(order_num, text, raw_value)
ON CONFLICT (question_id, order_num) DO UPDATE
    SET text = EXCLUDED.text;

-- ============================================================
-- option_scores для "Фундаментальные свойства личности" (обратных нет)
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
WHERE t.code = 'fundamental_properties_eysenck'
  AND (
        (sc.code = 'extraversion' AND q.order_num IN (1, 3, 6, 11, 13, 15, 20))
     OR (sc.code = 'neuroticism'  AND q.order_num IN (2, 5, 7, 10, 14, 17, 18))
     OR (sc.code = 'psychoticism' AND q.order_num IN (4, 8, 9, 12, 16, 19, 21))
  )
ON CONFLICT (option_id, scale_id) DO UPDATE
    SET score = EXCLUDED.score;

-- ============================================================
-- ТЕСТ: Формально-динамические свойства индивидуальности (Русалов)
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES (
        'formal_dynamic_properties_rusalov',
        'Формально-динамические свойства индивидуальности (темперамент)',
        'Опросник формально-динамических свойств индивидуальности (Русалов). 26 утверждений, шкала 1–5.',
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
        'Вам предлагается список утверждений, характеризующих поведение человека. Укажите степень выраженности каждой из предъявляемых характеристик.',
        true,
        1
    FROM ins_test
    ON CONFLICT (test_id, version_no) DO UPDATE
        SET instruction = EXCLUDED.instruction
    RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('erm', 'ЭРМ', 'Эргичность психомоторная',      'Пункты 1, 14',   'sum',       2.00, 10.00, 'Сумма баллов по пунктам 1, 14.'),
        ('eri', 'ЭРИ', 'Эргичность интеллектуальная',  'Пункты 2, 15',   'sum',       2.00, 10.00, 'Сумма баллов по пунктам 2, 15.'),
        ('erk', 'ЭРК', 'Эргичность коммуникативная',   'Пункты 3, 16',   'sum',       2.00, 10.00, 'Сумма баллов по пунктам 3, 16.'),
        ('pm',  'ПМ',  'Пластичность психомоторная',   'Пункты 4, 17',   'sum',       2.00, 10.00, 'Сумма баллов по пунктам 4, 17.'),
        ('pi',  'ПИ',  'Пластичность интеллектуальная','Пункты 5, 18',   'sum',       2.00, 10.00, 'Сумма баллов по пунктам 5, 18.'),
        ('pk',  'ПК',  'Пластичность коммуникативная', 'Пункты 6, 19',   'sum',       2.00, 10.00, 'Сумма баллов по пунктам 6, 19.'),
        ('sm',  'СМ',  'Скорость психомоторная',       'Пункты 7, 20',   'sum',       2.00, 10.00, 'Сумма баллов по пунктам 7, 20.'),
        ('si',  'СИ',  'Скорость интеллектуальная',    'Пункты 8, 21',   'sum',       2.00, 10.00, 'Сумма баллов по пунктам 8, 21.'),
        ('sk',  'СК',  'Скорость коммуникативная',     'Пункты 9, 22',   'sum',       2.00, 10.00, 'Сумма баллов по пунктам 9, 22.'),
        ('em',  'ЭМ',  'Эмоциональность психомоторная','Пункты 10, 23',  'sum',       2.00, 10.00, 'Сумма баллов по пунктам 10, 23.'),
        ('ei',  'ЭИ',  'Эмоциональность интеллектуальная', 'Пункты 11, 24', 'sum',    2.00, 10.00, 'Сумма баллов по пунктам 11, 24.'),
        ('ek',  'ЭК',  'Эмоциональность коммуникативная',  'Пункты 12, 25', 'sum',    2.00, 10.00, 'Сумма баллов по пунктам 12, 25.'),
        ('ksh', 'КШ',  'Контрольная шкала',            'Пункты 13, 26',  'sum',       2.00, 10.00, 'Сумма баллов по пунктам 13, 26.'),
        ('ipa', 'ИПА', 'Индекс психомоторной активности',
         'ЭРМ (1,14) + ПМ (4,17) + СМ (7,20)', 'composite', 6.00, 30.00,
         'Составная шкала: ЭРМ + ПМ + СМ.'),
        ('iia', 'ИИА', 'Индекс интеллектуальной активности',
         'ЭРИ (2,15) + ПИ (5,18) + СИ (8,21)', 'composite', 6.00, 30.00,
         'Составная шкала: ЭРИ + ПИ + СИ.'),
        ('ika', 'ИКА', 'Индекс коммуникативной активности',
         'ЭРК (3,16) + ПК (6,19) + СК (9,22)', 'composite', 6.00, 30.00,
         'Составная шкала: ЭРК + ПК + СК.'),
        ('ioa', 'ИОА', 'Индекс общей активности',
         'ИПА + ИИА + ИКА', 'composite', 18.00, 90.00,
         'Составная шкала: ИПА + ИИА + ИКА.'),
        ('ioe', 'ИОЭ', 'Индекс общей эмоциональности',
         'ЭМ (10,23) + ЭИ (11,24) + ЭК (12,25)', 'composite', 6.00, 30.00,
         'Составная шкала: ЭМ + ЭИ + ЭК.'),
        ('ioad','ИОАД','Индекс общей адаптивности',
         'ИОА − ИОЭ', 'composite', -12.00, 84.00,
         'Составная шкала: ИОА − ИОЭ.')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
    ON CONFLICT (version_id, code) DO UPDATE
        SET name = EXCLUDED.name
    RETURNING id
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'scale', true
    FROM ins_version v
    CROSS JOIN (VALUES
        (1,  'q1',  'Я охотно выполняю физическую работу'),
        (2,  'q2',  'Я с удовольствием решаю задачи сверх того, что было задано учителем'),
        (3,  'q3',  'Мне нравится бывать в больших компаниях'),
        (4,  'q4',  'Мне нравится мастерить вещи своими руками (заниматься рукоделием)'),
        (5,  'q5',  'Мне было бы легко выполнить несколько контрольных работ по разным предметам (например, по физике и литературе) в один день'),
        (6,  'q6',  'Мне легко завязывать знакомства первым/ой'),
        (7,  'q7',  'Я люблю участвовать в спортивных играх, требующих быстрых движений'),
        (8,  'q8',  'Я быстро решаю арифметические задачи'),
        (9,  'q9',  'Мне легко говорить быстро'),
        (10, 'q10', 'Занимаясь ремеслом (рукоделием), я раздражаюсь, когда работа не ладится'),
        (11, 'q11', 'Я сильно волнуюсь перед предстоящей контрольной работой'),
        (12, 'q12', 'Я испытываю чувство обиды, когда окружающие меня люди обходятся со мной хуже, чем следовало бы'),
        (13, 'q13', 'Бывает, что я говорю о вещах, в которых не разбираюсь'),
        (14, 'q14', 'Мне легко заниматься физическим трудом длительное время'),
        (15, 'q15', 'Я предпочитаю задания, требующие большой умственной нагрузки'),
        (16, 'q16', 'Я предпочитаю постоянно находиться среди друзей'),
        (17, 'q17', 'Я легко обучаюсь ремеслу (рукоделию)'),
        (18, 'q18', 'Мне легко учиться по основным предметам и еще посещать различные кружки'),
        (19, 'q19', 'При встрече я обычно начинаю разговор первым/ой'),
        (20, 'q20', 'Мне нравятся игры, требующие быстроты и ловкости движений'),
        (21, 'q21', 'Я способен (способна) быстро принимать решения, находясь в любой ситуации'),
        (22, 'q22', 'Я разговорчивый человек'),
        (23, 'q23', 'Я огорчаюсь из-за того, что с трудом овладеваю ремеслом (рукоделием)'),
        (24, 'q24', 'Я сильно переживаю, если сдал(а) экзамен менее успешно, чем ожидал(а)'),
        (25, 'q25', 'Я сильно волнуюсь, когда мне приходится выяснять отношения с друзьями'),
        (26, 'q26', 'Я никогда не сплетничаю')
    ) AS q(order_num, code, text)
    ON CONFLICT (version_id, code) DO UPDATE
        SET text = EXCLUDED.text
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
    (1, 'не выражена',        '1'),
    (2, 'скорее не выражена', '2'),
    (3, 'выражена',           '3'),
    (4, 'заметно выражена',   '4'),
    (5, 'очень выражена',     '5')
) AS a(order_num, text, raw_value)
ON CONFLICT (question_id, order_num) DO UPDATE
    SET text = EXCLUDED.text;

-- ============================================================
-- option_scores для "Формально-динамические свойства" (обратных нет)
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
WHERE t.code = 'formal_dynamic_properties_rusalov'
  AND sc.scale_type = 'sum'
  AND (
        (sc.code = 'erm' AND q.order_num IN (1, 14))
     OR (sc.code = 'eri' AND q.order_num IN (2, 15))
     OR (sc.code = 'erk' AND q.order_num IN (3, 16))
     OR (sc.code = 'pm'  AND q.order_num IN (4, 17))
     OR (sc.code = 'pi'  AND q.order_num IN (5, 18))
     OR (sc.code = 'pk'  AND q.order_num IN (6, 19))
     OR (sc.code = 'sm'  AND q.order_num IN (7, 20))
     OR (sc.code = 'si'  AND q.order_num IN (8, 21))
     OR (sc.code = 'sk'  AND q.order_num IN (9, 22))
     OR (sc.code = 'em'  AND q.order_num IN (10, 23))
     OR (sc.code = 'ei'  AND q.order_num IN (11, 24))
     OR (sc.code = 'ek'  AND q.order_num IN (12, 25))
     OR (sc.code = 'ksh' AND q.order_num IN (13, 26))
  )
ON CONFLICT (option_id, scale_id) DO UPDATE
    SET score = EXCLUDED.score;

-- ============================================================
-- ПРОВЕРКА
-- ============================================================
SELECT * FROM tests;
SELECT * FROM test_versions;
SELECT * FROM scales;
SELECT * FROM questions;
SELECT * FROM answer_options;