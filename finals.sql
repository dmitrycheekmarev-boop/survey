-- ============================================================
-- СОЗДАНИЕ ТАБЛИЦ (если их ещё нет)
-- ============================================================
CREATE TABLE IF NOT EXISTS tests (
  id BIGSERIAL PRIMARY KEY, code VARCHAR(50) NOT NULL UNIQUE, title VARCHAR(255) NOT NULL, description TEXT, is_active BOOLEAN NOT NULL DEFAULT TRUE, created_by BIGINT NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT NOW(), updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS test_versions (
  id BIGSERIAL PRIMARY KEY, test_id BIGINT NOT NULL REFERENCES tests(id) ON DELETE CASCADE, version_no INTEGER NOT NULL, instruction TEXT, is_published BOOLEAN NOT NULL DEFAULT FALSE, created_by BIGINT NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT NOW(), published_at TIMESTAMP, UNIQUE (test_id, version_no)
);
CREATE TABLE IF NOT EXISTS scales (
  id BIGSERIAL PRIMARY KEY, version_id BIGINT NOT NULL REFERENCES test_versions(id) ON DELETE CASCADE, code VARCHAR(50) NOT NULL, name VARCHAR(200) NOT NULL, description TEXT, scale_type VARCHAR(20) NOT NULL CHECK (scale_type IN ('sum','average','count','composite')), min_score NUMERIC(10,2), max_score NUMERIC(10,2), interpretation TEXT, UNIQUE (version_id, code)
);
CREATE TABLE IF NOT EXISTS questions (
  id BIGSERIAL PRIMARY KEY, version_id BIGINT NOT NULL REFERENCES test_versions(id) ON DELETE CASCADE, order_num INTEGER NOT NULL, code VARCHAR(50) NOT NULL, text TEXT NOT NULL, question_type VARCHAR(30) NOT NULL CHECK (question_type IN ('single_choice','multi_choice','scale','open_text')), is_required BOOLEAN NOT NULL DEFAULT TRUE, UNIQUE (version_id, code), UNIQUE (version_id, order_num)
);
CREATE TABLE IF NOT EXISTS answer_options (
  id BIGSERIAL PRIMARY KEY, question_id BIGINT NOT NULL REFERENCES questions(id) ON DELETE CASCADE, order_num INTEGER NOT NULL, text TEXT NOT NULL, raw_value VARCHAR(50), is_correct BOOLEAN, UNIQUE (question_id, order_num)
);
CREATE TABLE IF NOT EXISTS option_scores (
  id BIGSERIAL PRIMARY KEY, option_id BIGINT NOT NULL REFERENCES answer_options(id) ON DELETE CASCADE, scale_id BIGINT NOT NULL REFERENCES scales(id) ON DELETE CASCADE, score NUMERIC(10,2) NOT NULL DEFAULT 0, UNIQUE (option_id, scale_id)
);

-- ============================================================
-- ТЕСТ 1: Когнитивная рефлексия (CRT-13)
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES ('crt_13', 'Когнитивная рефлексия В. Олдрати', 'Методика CRT-13 (Oldrati et al, 2016)', true, 1)
    ON CONFLICT (code) DO UPDATE SET updated_at = now() RETURNING id
),
ins_version AS (
    INSERT INTO test_versions (test_id, version_no, instruction, is_published, created_by)
    SELECT id, 1, 'Выберите один правильный вариант ответа.', true, 1 FROM ins_test RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('crt_r', 'Когнитивная рефлексия (CRT-r)', 'Правильные ответы', 'sum', 0, 20, 'Максимум 20 баллов'),
        ('crt_i', 'Интуитивные ответы (CRT-i)', 'Интуитивные ответы', 'sum', 0, 10, 'Максимум 10 баллов')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'single_choice', true
    FROM ins_version v
    CROSS JOIN (VALUES
        (1, 'q1', 'С борта судна сброшена веревочная лестница так, что последняя перекладина лестницы касается поверхности воды...'),
        (2, 'q2', 'В дюжине 12 марок за 1 цент. Какое количество марок за 2 цента в дюжине?'),
        (3, 'q3', 'Фермер собрал 4 стога сена в одном углу поля и еще 5 стогов в другом углу...'),
        (4, 'q4', 'Вы участвуете в забеге. На последних метрах перед финишной линией Вы обгоняете второго бегуна...'),
        (5, 'q5', '25 солдат стоят в шеренге на расстоянии 3 м друг от друга. Какова протяженность всей шеренги?'),
        (6, 'q6', 'Утром улитка начинает ползти вверх по стене высотой 5 метров...'),
        (7, 'q7', 'Кирпич весит 1 кг плюс полкирпича. Сколько весит полкирпича?'),
        (8, 'q8', 'В шкафу у Франко 5 белых и 5 черных носков. В комнате Франко темно...'),
        (9, 'q9', 'Вы ложитесь спать в восемь часов. Вы ставите будильник на механических часах на девять часов...'),
        (10, 'q10', 'В одном месяце 28 дней. Сколько месяцев из 11 оставшихся месяцев содержат 30 дней?')
    ) AS q(order_num, code, text)
    RETURNING id, order_num
)
INSERT INTO answer_options (question_id, order_num, text, raw_value, is_correct)
SELECT q.id, a.order_num, a.text, a.raw_value, NULL
FROM ins_questions q
CROSS JOIN (VALUES
    (1, 'Вариант А', 'а'), (2, 'Вариант Б', 'б'), (3, 'Вариант В', 'в'), (4, 'Вариант Г', 'г')
) AS a(order_num, text, raw_value);

INSERT INTO option_scores (option_id, scale_id, score)
SELECT ao.id, sc.id, 1
FROM answer_options ao
JOIN questions q ON ao.question_id = q.id
JOIN test_versions v ON q.version_id = v.id
JOIN tests t ON v.test_id = t.id
JOIN scales sc ON sc.version_id = v.id
WHERE t.code = 'crt_13'
  AND (
    (sc.code = 'crt_r' AND ((q.order_num=1 AND ao.raw_value='г') OR (q.order_num=2 AND ao.raw_value='б') OR (q.order_num=3 AND ao.raw_value='а') OR (q.order_num=4 AND ao.raw_value='б') OR (q.order_num=5 AND ao.raw_value='б') OR (q.order_num=6 AND ao.raw_value='в') OR (q.order_num=7 AND ao.raw_value='б') OR (q.order_num=8 AND ao.raw_value='а') OR (q.order_num=9 AND ao.raw_value='а') OR (q.order_num=10 AND ao.raw_value='г')))
    OR
    (sc.code = 'crt_i' AND ((q.order_num=1 AND ao.raw_value='в') OR (q.order_num=2 AND ao.raw_value='а') OR (q.order_num=3 AND ao.raw_value='б') OR (q.order_num=4 AND ao.raw_value='а') OR (q.order_num=5 AND ao.raw_value='г') OR (q.order_num=6 AND ao.raw_value='г') OR (q.order_num=7 AND ao.raw_value='а') OR (q.order_num=8 AND ao.raw_value='г') OR (q.order_num=9 AND ao.raw_value='г') OR (q.order_num=10 AND ao.raw_value='а')))
  );

-- ============================================================
-- ТЕСТ 2: Когнитивные стили
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES ('cognitive_styles', 'Когнитивные стили', '24 утверждения, шкала 1–5.', true, 1)
    ON CONFLICT (code) DO UPDATE SET updated_at = now() RETURNING id
),
ins_version AS (
    INSERT INTO test_versions (test_id, version_no, instruction, is_published, created_by)
    SELECT id, 1, 'Укажите степень выраженности каждой из предъявляемых характеристик от 1 до 5.', true, 1 FROM ins_test RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('s1', 'Полезависимость', 'Пункты 1,10', 'sum', 2, 10, ''),
        ('s2', 'Поленезависимость', 'Пункты 3,7', 'sum', 2, 10, ''),
        ('s3', 'Узкий диапазон эквивалентности', 'Пункты 16,19', 'sum', 2, 10, ''),
        ('s4', 'Широкий диапазон эквивалентности', 'Пункты 4,8', 'sum', 2, 10, ''),
        ('s5', 'Гибкость познавательного контроля', 'Пункты 5,11', 'sum', 2, 10, ''),
        ('s6', 'Ригидность познавательного контроля', 'Пункты 12,20', 'sum', 2, 10, ''),
        ('s7', 'Импульсивность', 'Пункты 2,6', 'sum', 2, 10, ''),
        ('s8', 'Рефлективность', 'Пункты 14,22', 'sum', 2, 10, ''),
        ('s9', 'Конкретная концептуализация', 'Пункты 21,23', 'sum', 2, 10, ''),
        ('s10', 'Абстрактная концептуализация', 'Пункты 15,24', 'sum', 2, 10, ''),
        ('s11', 'Толерантность к нереалистическому опыту', 'Пункты 13,17', 'sum', 2, 10, ''),
        ('s12', 'Нетолерантность к нереалистическому опыту', 'Пункты 9,18', 'sum', 2, 10, '')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'scale', true
    FROM ins_version v
    CROSS JOIN (
        VALUES 
        (1, 'q1', 'Покупая одежду, я доверяю мнению продавца'), (2, 'q2', 'Я часто принимаю решения по первому впечатлению'), (3, 'q3', 'Я всегда принимаю решения самостоятельно'), (4, 'q4', 'Я не люблю вдаваться в подробности, пересказывая события'), (5, 'q5', 'Я одинаково хорошо запоминаю как картинки, так и тексты'), (6, 'q6', 'Я готов решать любую задачу не задумываясь'), (7, 'q7', 'Мой собственный опыт для меня важнее, чем мнение друзей'), (8, 'q8', 'Я легко делю людей на хороших и плохих'), (9, 'q9', 'Люди, которые думают не так, как я, меня сильно раздражают'), (10, 'q10', 'Я легко соглашаюсь с мнением друзей'), (11, 'q11', 'Мне легко одновременно беседовать и смотреть телевизор'), (12, 'q12', 'Мне трудно описывать словами абстрактные картинки'), (13, 'q13', 'Я люблю получать дополнительную информацию из справочников'), (14, 'q14', 'Я приступаю к решению задачи после ее тщательного обдумывания'), (15, 'q15', 'Я всегда открыт к новой информации и новому опыту'), (16, 'q16', 'Пересказывая содержание фильма, я люблю подробно его описать'), (17, 'q17', 'Меня привлекает оригинальность суждений других людей'), (18, 'q18', 'Я общаюсь только с теми людьми, мировоззрение которых совпадает с моим'), (19, 'q19', 'Я хорошо помню детали моего последнего дня рождения'), (20, 'q20', 'Мне трудно подобрать образ к абстрактным словам'), (21, 'q21', 'Я человек конкретный и не люблю рассуждать: «Вот если бы…»'), (22, 'q22', 'Перед принятием решения я тщательно перепроверяю все факты'), (23, 'q23', 'Я предпочитаю задания, к которым прилагаются четкие инструкции'), (24, 'q24', 'В сложных задачах я предлагаю несколько вариантов решений')
    ) AS q(order_num, code, text)
    RETURNING id
)
INSERT INTO answer_options (question_id, order_num, text, raw_value, is_correct)
SELECT q.id, a.order_num, a.text, a.raw_value, NULL
FROM ins_questions q
CROSS JOIN (VALUES (1, 'не выражена', '1'), (2, 'скорее не выражена', '2'), (3, 'выражена', '3'), (4, 'заметно выражена', '4'), (5, 'очень выражена', '5')) AS a(order_num, text, raw_value);

INSERT INTO option_scores (option_id, scale_id, score)
SELECT ao.id, sc.id, ao.raw_value::numeric
FROM answer_options ao
JOIN questions q ON ao.question_id = q.id
JOIN test_versions v ON q.version_id = v.id
JOIN tests t ON v.test_id = t.id
JOIN scales sc ON sc.version_id = v.id
WHERE t.code = 'cognitive_styles'
  AND (
    (sc.code = 's1' AND q.order_num IN (1,10)) OR (sc.code = 's2' AND q.order_num IN (3,7)) OR (sc.code = 's3' AND q.order_num IN (16,19)) OR (sc.code = 's4' AND q.order_num IN (4,8)) OR (sc.code = 's5' AND q.order_num IN (5,11)) OR (sc.code = 's6' AND q.order_num IN (12,20)) OR (sc.code = 's7' AND q.order_num IN (2,6)) OR (sc.code = 's8' AND q.order_num IN (14,22)) OR (sc.code = 's9' AND q.order_num IN (21,23)) OR (sc.code = 's10' AND q.order_num IN (15,24)) OR (sc.code = 's11' AND q.order_num IN (13,17)) OR (sc.code = 's12' AND q.order_num IN (9,18))
  );

-- ============================================================
-- ТЕСТ 3: COPE-30A
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES ('cope_30a', 'Копинг-стратегии (СОРЕ-30А)', 'Методика СОРЕ для подростков 12-18 лет.', true, 1)
    ON CONFLICT (code) DO UPDATE SET updated_at = now() RETURNING id
),
ins_version AS (
    INSERT INTO test_versions (test_id, version_no, instruction, is_published, created_by)
    SELECT id, 1, 'Каждый вариант продолжения фразы предлагается оценить по шкале.', true, 1 FROM ins_test RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('s1', 'Позитивное переформулирование', 'Пункты 11, 17', 'sum', 2, 8, ''),
        ('s2', 'Мысленный уход от проблемы', 'Пункты 4, 20', 'sum', 2, 8, ''),
        ('s3', 'Концентрация на эмоциях', 'Пункты 10, 22', 'sum', 2, 8, ''),
        ('s4', 'Инструментальная поддержка', 'Пункты 3, 12', 'sum', 2, 8, ''),
        ('s5', 'Активное совладание', 'Пункты 9, 23', 'sum', 2, 8, ''),
        ('s6', 'Отрицание', 'Пункты 18, 30', 'sum', 2, 8, ''),
        ('s7', 'Обращение к религии', 'Пункты 1, 5', 'sum', 2, 8, ''),
        ('s8', 'Юмор', 'Пункты 7, 15', 'sum', 2, 8, ''),
        ('s9', 'Поведенческий уход', 'Пункты 16, 24', 'sum', 2, 8, ''),
        ('s10', 'Сдерживание', 'Пункты 2, 19', 'sum', 2, 8, ''),
        ('s11', 'Эмоциональная поддержка', 'Пункты 8, 25', 'sum', 2, 8, ''),
        ('s12', 'Использование успокоительных', 'Пункты 14, 26', 'sum', 2, 8, ''),
        ('s13', 'Принятие', 'Пункты 21, 27', 'sum', 2, 8, ''),
        ('s14', 'Подавление конкурирующей деятельности', 'Пункты 13, 28', 'sum', 2, 8, ''),
        ('s15', 'Планирование', 'Пункты 6, 29', 'sum', 2, 8, '')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'scale', true
    FROM ins_version v
    CROSS JOIN (
        VALUES 
        (1,'q1','Я надеюсь на то, что Бог мне поможет'), (2,'q2','Я стараюсь удерживать себя от скоропалительных шагов'), (3,'q3','Я обсуждаю случившееся, чтобы лучше понять ситуацию'), (4,'q4','Я предаюсь фантазиям на другие темы, чтобы отвлечься'), (5,'q5','Я прошу помощи у Бога'), (6,'q6','Я стараюсь спланировать и обдумать свои дальнейшие действия'), (7,'q7','Я перевожу случившееся в шутку'), (8,'q8','Я стараюсь получить эмоциональную поддержку у друзей или родных'), (9,'q9','Я предпринимаю какие-то еще действия, стараясь преодолеть сложившуюся ситуацию'), (10,'q10','Я даю выход своим переживаниям'), (11,'q11','Я пытаюсь посмотреть на ситуацию с более позитивной стороны, в ином свете'), (12,'q12','Я говорю с кем-нибудь, кто мог бы конкретно помочь решить мою проблему'), (13,'q13','Я полностью концентрируюсь на решении этой проблемы и откладываю другие дела'), (14,'q14','Я принимаю лекарства, чтобы поменьше думать о проблеме'), (15,'q15','Я шучу по поводу случившегося'), (16,'q16','Я перестаю пытаться добиться своего (получить то, что я хочу)'), (17,'q17','Я ищу что-то хорошее в том, что произошло'), (18,'q18','Я делаю вид, что ничего не произошло'), (19,'q19','Я стараюсь не действовать слишком поспешно, чтобы не ухудшить ситуацию'), (20,'q20','Я смотрю телевизор или погружаюсь в интернет, чтобы меньше думать о проблеме'), (21,'q21','Я стараюсь принять ситуацию, сжиться с ней'), (22,'q22','Я переживаю и активно проявляю свои чувства'), (23,'q23','Я предпринимаю активные действия, чтобы справиться с проблемой'), (24,'q24','Я снижаю количество усилий, направленных на решение этой проблемы'), (25,'q25','Я обсуждаю свои переживания с кем-то из моих близких'), (26,'q26','Я принимаю успокоительные, потому что это помогает мне преодолеть проблему'), (27,'q27','Я учусь жить с этим'), (28,'q28','Я откладываю другие дела в сторону, чтобы сосредоточиться на решении проблемы'), (29,'q29','Я тщательно обдумываю шаги, которые буду предпринимать для решения проблемы'), (30,'q30','Я просто делаю вид, что ничего не случилось')
    ) AS q(order_num, code, text)
    RETURNING id
)
INSERT INTO answer_options (question_id, order_num, text, raw_value, is_correct)
SELECT q.id, a.order_num, a.text, a.raw_value, NULL
FROM ins_questions q
CROSS JOIN (VALUES (1, 'Нет', '1'), (2, 'Изредка', '2'), (3, 'Иногда', '3'), (4, 'Часто', '4')) AS a(order_num, text, raw_value);

INSERT INTO option_scores (option_id, scale_id, score)
SELECT ao.id, sc.id, ao.raw_value::numeric
FROM answer_options ao
JOIN questions q ON ao.question_id = q.id
JOIN test_versions v ON q.version_id = v.id
JOIN tests t ON v.test_id = t.id
JOIN scales sc ON sc.version_id = v.id
WHERE t.code = 'cope_30a'
  AND (
    (sc.code = 's1' AND q.order_num IN (11,17)) OR (sc.code = 's2' AND q.order_num IN (4,20)) OR (sc.code = 's3' AND q.order_num IN (10,22)) OR (sc.code = 's4' AND q.order_num IN (3,12)) OR (sc.code = 's5' AND q.order_num IN (9,23)) OR (sc.code = 's6' AND q.order_num IN (18,30)) OR (sc.code = 's7' AND q.order_num IN (1,5)) OR (sc.code = 's8' AND q.order_num IN (7,15)) OR (sc.code = 's9' AND q.order_num IN (16,24)) OR (sc.code = 's10' AND q.order_num IN (2,19)) OR (sc.code = 's11' AND q.order_num IN (8,25)) OR (sc.code = 's12' AND q.order_num IN (14,26)) OR (sc.code = 's13' AND q.order_num IN (21,27)) OR (sc.code = 's14' AND q.order_num IN (13,28)) OR (sc.code = 's15' AND q.order_num IN (6,29))
  );

-- ============================================================
-- ТЕСТ 4: Альтруистические установки
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES ('altruism', 'Методика измерения альтруистических установок', '23 вопроса. Оценка от -3 до 3.', true, 1)
    ON CONFLICT (code) DO UPDATE SET updated_at = now() RETURNING id
),
ins_version AS (
    INSERT INTO test_versions (test_id, version_no, instruction, is_published, created_by)
    SELECT id, 1, 'Оцените, насколько Вы согласны с данными утверждениями от -3 до 3.', true, 1 FROM ins_test RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('lie', 'Шкала лжи', 'Пункты 1, 2, 13, 14, 23', 'sum', 0, 30, ''),
        ('direct', 'Прямые баллы', 'Пункты 4, 6, 8, 10, 12, 16, 18, 20, 22', 'sum', 0, 54, ''),
        ('reverse', 'Обратные баллы', 'Пункты 3, 5, 7, 9, 11, 15, 17, 19, 21', 'sum', 0, 54, ''),
        ('total', 'Уровень альтруизма', 'Сумма прямых и обратных баллов', 'sum', 0, 108, '')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'scale', true
    FROM ins_version v
    CROSS JOIN (
        VALUES 
        (1,'q1','Я никогда не опаздываю на работу или очень важные встречи'), (2,'q2','Я не встречал людей, которые бы мне не нравились'), (3,'q3','Я не люблю помогать другим: пусть сами о себе позаботятся'), (4,'q4','Если я вижу физические страдания другого, я стараюсь чем-то помочь'), (5,'q5','Прежде чем взяться за какую-либо работу, я спрашиваю, сколько за это заплатят'), (6,'q6','Помогать другим людям — большая радость'), (7,'q7','Тратить свое время и силы на помощь другим не имеет смысла'), (8,'q8','Я придерживаюсь принципа: "помоги другому, и добро к тебе вернется"'), (9,'q9','Помогать другим людям, как правило, дело бесполезное'), (10,'q10','Мне бы хотелось иметь условия для того, чтобы приносить пользу людям'), (11,'q11','Я считаю, что всякая работа должна оплачиваться'), (12,'q12','В мире есть масса дел, которые стоит выполнять совершенно бескорыстно'), (13,'q13','Я считаю, что никогда не следует участвовать в развлекательных мероприятиях'), (14,'q14','Я думаю, нет такого понятия, как "искреннее заблуждение"'), (15,'q15','Если мне предлагают что-то сделать на общественных началах, я задумываюсь, а что мне это даст'), (16,'q16','Я хотел бы поучаствовать в волонтерской программе'), (17,'q17','Если мне предлагают что-то сделать на общественных началах, я обычно отказываюсь'), (18,'q18','Я часто сперва берусь выполнить работу и не спрашиваю, какую выгоду это принесет'), (19,'q19','Увидев на улице несчастный случай, я пройду мимо'), (20,'q20','Люди достаточно добры по своей природе'), (21,'q21','Я не верю в то, что в случае беды хоть кто-то придет мне на помощь'), (22,'q22','Я не могу спокойно смотреть на страдания других'), (23,'q23','Я никогда не обижал другого человека')
    ) AS q(order_num, code, text)
    RETURNING id
)
INSERT INTO answer_options (question_id, order_num, text, raw_value, is_correct)
SELECT q.id, a.order_num, a.text, a.raw_value, NULL
FROM ins_questions q
CROSS JOIN (VALUES (1, '-3', '-3'), (2, '-2', '-2'), (3, '-1', '-1'), (4, '0', '0'), (5, '1', '1'), (6, '2', '2'), (7, '3', '3')) AS a(order_num, text, raw_value);

INSERT INTO option_scores (option_id, scale_id, score)
SELECT ao.id, sc.id, 
  CASE 
    WHEN sc.code IN ('lie', 'direct') THEN (ao.raw_value::numeric + 3)
    WHEN sc.code = 'reverse' THEN (3 - ao.raw_value::numeric)
    WHEN sc.code = 'total' AND q.order_num IN (4, 6, 8, 10, 12, 16, 18, 20, 22) THEN (ao.raw_value::numeric + 3)
    WHEN sc.code = 'total' AND q.order_num IN (3, 5, 7, 9, 11, 15, 17, 19, 21) THEN (3 - ao.raw_value::numeric)
  END
FROM answer_options ao
JOIN questions q ON ao.question_id = q.id
JOIN test_versions v ON q.version_id = v.id
JOIN tests t ON v.test_id = t.id
JOIN scales sc ON sc.version_id = v.id
WHERE t.code = 'altruism'
  AND (
    (sc.code = 'lie' AND q.order_num IN (1, 2, 13, 14, 23)) OR
    (sc.code = 'direct' AND q.order_num IN (4, 6, 8, 10, 12, 16, 18, 20, 22)) OR
    (sc.code = 'reverse' AND q.order_num IN (3, 5, 7, 9, 11, 15, 17, 19, 21)) OR
    (sc.code = 'total' AND q.order_num IN (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 20, 21, 22))
  );

-- ============================================================
-- ТЕСТ 5: Метакогнитивная включенность в деятельность
-- ============================================================
WITH
ins_test AS (
    INSERT INTO tests (code, title, description, is_active, created_by)
    VALUES ('metacog', 'Метакогнитивная включенность в деятельность', '32 утверждения. Шкала 1-5.', true, 1)
    ON CONFLICT (code) DO UPDATE SET updated_at = now() RETURNING id
),
ins_version AS (
    INSERT INTO test_versions (test_id, version_no, instruction, is_published, created_by)
    SELECT id, 1, 'Оцените утверждения от 1 (совершенно не согласен) до 5 (совершенно согласен).', true, 1 FROM ins_test RETURNING id
),
ins_scales AS (
    INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
    SELECT v.id, s.code, s.name, s.description, s.scale_type, s.min_score, s.max_score, s.interpretation
    FROM ins_version v
    CROSS JOIN (VALUES
        ('s1', 'Метакогнитивные знания', 'Пункты 7,9,11,12,13,14,15,19,20,22,24,26', 'sum', 12, 60, ''),
        ('s2', 'Метакогнитивное регулирование', 'Пункты 1,2,3,4,5,6,8,10,16,17,18,21,23,25,27,28,29,30,31,32', 'sum', 20, 100, ''),
        ('s3', 'Декларируемые знания', 'Пункты 7,9,12,13,15', 'sum', 5, 25, ''),
        ('s4', 'Процедурные знания', 'Пункты 11,20,24', 'sum', 3, 15, ''),
        ('s5', 'Условные знания', 'Пункты 14,19,22,26', 'sum', 4, 20, ''),
        ('s6', 'Планирование', 'Пункты 3,4,6,16', 'sum', 4, 20, ''),
        ('s7', 'Стратегии управления информацией', 'Пункты 10,23', 'sum', 2, 10, ''),
        ('s8', 'Контроль компонентов', 'Пункты 1,2,8,21,25,30', 'sum', 6, 30, ''),
        ('s9', 'Структура исправления ошибок', 'Пункты 18,28,29,32', 'sum', 4, 20, ''),
        ('s10', 'Оценка', 'Пункты 5,17,31,27', 'sum', 4, 20, '')
    ) AS s(code, name, description, scale_type, min_score, max_score, interpretation)
),
ins_questions AS (
    INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
    SELECT v.id, q.order_num, q.code, q.text, 'scale', true
    FROM ins_version v
    CROSS JOIN (
        VALUES 
        (1,'q1','Периодически я спрашиваю себя, достигаю ли я своих жизненных целей'), (2,'q2','Я рассматриваю несколько альтернатив решения проблемы'), (3,'q3','Я выбираю такой темп решения задачи, чтобы иметь достаточно времени'), (4,'q4','Я думаю о том, какая информация мне понадобится'), (5,'q5','Я могу оценить, насколько хорошо я выполнил работу'), (6,'q6','Прежде чем начать выполнять ту или иную работу, я четко определяю ее цель'), (7,'q7','Я знаю, какая именно информация особенно важна в моей работе'), (8,'q8','Работая над проблемой, я время от времени спрашиваю себя, рассмотрел ли я все альтернативы'), (9,'q9','Я способен хорошо структурировать информацию'), (10,'q10','Я сознательно концентрируюсь на важной для меня информации'), (11,'q11','Я точно знаю, с какой целью использую различные стратегии'), (12,'q12','Я знаю, что ожидает от меня мой учитель'), (13,'q13','Я хорошо запоминаю новую информацию'), (14,'q14','Я использую разные стратегии в зависимости от ситуации'), (15,'q15','Я способен контролировать качество принимаемых мной решений'), (16,'q16','Я обдумываю несколько способов решения проблемы и выбираю самый оптимальный'), (17,'q17','Закончив работу, я подвожу итог тому, что я сделал'), (18,'q18','Когда я в чем-либо не могу разобраться, я обращаюсь за помощью'), (19,'q19','Я могу замотивировать себя учиться, когда мне это необходимо'), (20,'q20','Я сознаю, какие стратегии использую, когда принимаю решения'), (21,'q21','Принимая важное решение, я склонен анализировать эффективность стратегий'), (22,'q22','Я использую свои интеллектуальные преимущества для компенсации своих слабостей'), (23,'q23','Я концентрирую внимание на значении и практической ценности новой информации'), (24,'q24','Я автоматически применяю эффективные стратегии решения задач'), (25,'q25','Изучая что-то новое, я время от времени делаю паузу'), (26,'q26','Я знаю, в каком случае каждая из используемых мной стратегий будет наиболее эффективна'), (27,'q27','Когда решение задачи закончено, я спрашиваю себя, достигнуты ли все поставленные цели'), (28,'q28','Когда мне не удается что-либо понять, я изменяю способ работы с информацией'), (29,'q29','Я пересматриваю свои предположения, когда затрудняюсь в решении проблемы'), (30,'q30','Я склонен спрашивать себя, насколько успешно я продвигаюсь'), (31,'q31','Когда задача уже решена, я склонен спрашивать себя, научился ли я чему-либо полезному'), (32,'q32','Если новая информация недостаточно понятна, я склонен возвращаться к ней')
    ) AS q(order_num, code, text)
    RETURNING id
)
INSERT INTO answer_options (question_id, order_num, text, raw_value, is_correct)
SELECT q.id, a.order_num, a.text, a.raw_value, NULL
FROM ins_questions q
CROSS JOIN (VALUES (1, 'совершенно не согласен', '1'), (2, 'скорее не согласен', '2'), (3, 'не знаю', '3'), (4, 'скорее согласен', '4'), (5, 'совершенно согласен', '5')) AS a(order_num, text, raw_value);

INSERT INTO option_scores (option_id, scale_id, score)
SELECT ao.id, sc.id, ao.raw_value::numeric
FROM answer_options ao
JOIN questions q ON ao.question_id = q.id
JOIN test_versions v ON q.version_id = v.id
JOIN tests t ON v.test_id = t.id
JOIN scales sc ON sc.version_id = v.id
WHERE t.code = 'metacog'
  AND (
    (sc.code = 's1' AND q.order_num IN (7,9,11,12,13,14,15,19,20,22,24,26)) OR (sc.code = 's2' AND q.order_num IN (1,2,3,4,5,6,8,10,16,17,18,21,23,25,27,28,29,30,31,32)) OR (sc.code = 's3' AND q.order_num IN (7,9,12,13,15)) OR (sc.code = 's4' AND q.order_num IN (11,20,24)) OR (sc.code = 's5' AND q.order_num IN (14,19,22,26)) OR (sc.code = 's6' AND q.order_num IN (3,4,6,16)) OR (sc.code = 's7' AND q.order_num IN (10,23)) OR (sc.code = 's8' AND q.order_num IN (1,2,8,21,25,30)) OR (sc.code = 's9' AND q.order_num IN (18,28,29,32)) OR (sc.code = 's10' AND q.order_num IN (5,17,31,27))
  );