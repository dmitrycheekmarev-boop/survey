#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import jsonschema
import psycopg

HERE = Path(__file__).resolve().parent
DSN = os.environ.get("DATABASE_URL", "dbname=tests")
PLACEHOLDER_HASH = "!"

ROLE_NAMES = {"methodist": "Методист", "student": "Студент", "admin": "Администратор"}
LETTER_ALIASES = {"А": "A", "Б": "B", "В": "C", "Г": "D"}


def validate(data, schema_name):
    schema = json.loads((HERE / schema_name).read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        for e in errors[:20]:
            print("ОШИБКА СХЕМЫ:", "/".join(map(str, e.path)), "-", e.message)
        sys.exit(1)


def ensure_user(cur, login, full_name, email, role_code):
    cur.execute(
        "INSERT INTO roles (code, name) VALUES (%s, %s) ON CONFLICT (code) DO NOTHING",
        (role_code, ROLE_NAMES.get(role_code, role_code)),
    )
    cur.execute(
        """INSERT INTO users (login, password_hash, full_name, email)
           VALUES (%s, %s, %s, %s) ON CONFLICT (login) DO UPDATE SET login = EXCLUDED.login
           RETURNING id""",
        (login, PLACEHOLDER_HASH, full_name, email),
    )
    uid = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO user_roles (user_id, role_id)
           SELECT %s, id FROM roles WHERE code = %s ON CONFLICT DO NOTHING""",
        (uid, role_code),
    )
    return uid


def load_catalog(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(data, "catalog.schema.json")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        a = data["author"]
        author_id = ensure_user(cur, a["login"], a["full_name"], a.get("email"), a["role"])
        for t in data["tests"]:
            cur.execute(
                """INSERT INTO tests (code, title, description, is_active, created_by)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (code) DO UPDATE
                   SET title = EXCLUDED.title, description = EXCLUDED.description,
                       is_active = EXCLUDED.is_active, updated_at = now()
                   RETURNING id""",
                (t["code"], t["title"], t.get("description"), t.get("is_active", True), author_id),
            )
            test_id = cur.fetchone()[0]
            v = t["version"]
            cur.execute("SELECT 1 FROM test_versions WHERE test_id=%s AND version_no=%s", (test_id, v["version_no"]))
            if cur.fetchone():
                print(f'{t["code"]} v{v["version_no"]}: уже есть, пропущено')
                continue
            pub = v.get("is_published", False)
            cur.execute(
                """INSERT INTO test_versions (test_id, version_no, instruction, is_published, created_by, published_at)
                   VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() END) RETURNING id""",
                (test_id, v["version_no"], v.get("instruction"), pub, author_id, pub),
            )
            version_id = cur.fetchone()[0]

            scale_ids = {}
            for s in v["scales"]:
                cur.execute(
                    """INSERT INTO scales (version_id, code, name, description, scale_type, min_score, max_score, interpretation)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (version_id, s["code"], s["name"], s.get("description"), s["scale_type"],
                     s.get("min_score"), s.get("max_score"), s.get("interpretation")),
                )
                scale_ids[s["code"]] = cur.fetchone()[0]
            for d in v.get("scale_dependencies", []):
                cur.execute(
                    """INSERT INTO scale_dependencies (composite_scale_id, component_scale_id, coefficient, operation)
                       VALUES (%s,%s,%s,%s)""",
                    (scale_ids[d["composite_scale_code"]], scale_ids[d["component_scale_code"]],
                     d.get("coefficient", 1), d.get("operation", "+")),
                )
            n_opt = 0
            for q in v["questions"]:
                cur.execute(
                    """INSERT INTO questions (version_id, order_num, code, text, question_type, is_required)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (version_id, q["order_num"], q["code"], q["text"], q["question_type"], q.get("is_required", True)),
                )
                qid = cur.fetchone()[0]
                for o in q["options"]:
                    cur.execute(
                        """INSERT INTO answer_options (question_id, order_num, text, raw_value, is_correct)
                           VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                        (qid, o["order_num"], o["text"], o["raw_value"], o.get("is_correct")),
                    )
                    oid = cur.fetchone()[0]
                    n_opt += 1
                    for sc in o["scores"]:
                        cur.execute(
                            "INSERT INTO option_scores (option_id, scale_id, score) VALUES (%s,%s,%s)",
                            (oid, scale_ids[sc["scale_code"]], sc["score"]),
                        )
            print(f'{t["code"]} v{v["version_no"]}: вопросов {len(v["questions"])}, вариантов {n_opt}, шкал {len(scale_ids)}')


def load_packs(path):
    packs = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(packs, "response_pack.schema.json")
    ok = bad = dup = 0
    with psycopg.connect(DSN) as conn:
        cache = {}
        for i, pk in enumerate(packs, 1):
            with conn.transaction(), conn.cursor() as cur:
                label = f'#{i} {pk["respondent"]}/{pk["test_code"]}'
                key = (pk["test_code"], pk.get("version"))
                if key not in cache:
                    cur.execute(
                        """SELECT tv.id FROM test_versions tv JOIN tests t ON t.id = tv.test_id
                           WHERE t.code = %s AND tv.is_published AND (%s::int IS NULL OR tv.version_no = %s)
                           ORDER BY tv.version_no DESC LIMIT 1""",
                        (pk["test_code"], pk.get("version"), pk.get("version")),
                    )
                    row = cur.fetchone()
                    if not row:
                        cache[key] = None
                    else:
                        vid = row[0]
                        cur.execute("SELECT id, code, is_required, question_type FROM questions WHERE version_id = %s", (vid,))
                        qs = {c: (qid, req, qt) for qid, c, req, qt in cur.fetchall()}
                        cur.execute(
                            """SELECT q.code, ao.id, ao.raw_value, ao.text FROM answer_options ao
                               JOIN questions q ON q.id = ao.question_id
                               WHERE q.version_id = %s AND q.question_type <> 'open_text'""", (vid,))
                        opts = {}
                        for qc, oid, raw, txt in cur.fetchall():
                            opts.setdefault(qc, {"raw": {}, "text": {}})
                            opts[qc]["raw"][raw] = oid
                            opts[qc]["text"][txt.strip().lower()] = oid
                        cache[key] = (vid, qs, opts)
                if cache[key] is None:
                    print(f"{label}: тест/версия не найдены"); bad += 1; continue
                vid, qs, opts = cache[key]

                errs, chosen, seen = [], [], set()
                for a in pk["answers"]:
                    qc, val = a["question_code"], a["value"]
                    if qc not in qs:
                        errs.append(f"неизвестный вопрос {qc}"); continue
                    if qc in seen:
                        errs.append(f"повтор ответа на {qc}"); continue
                    if qs[qc][2] == "open_text":
                        if not val.strip():
                            continue
                        seen.add(qc)
                        chosen.append((qc, None, val.strip()))
                        continue
                    seen.add(qc)
                    o = (opts[qc]["raw"].get(val) or opts[qc]["raw"].get(LETTER_ALIASES.get(val.strip()))
                         or opts[qc]["text"].get(val.strip().lower()))
                    if o is None:
                        errs.append(f"{qc}: неизвестное значение {val!r}"); continue
                    chosen.append((qc, o, None))
                errs += [f"нет ответа на обязательный {c}" for c, (_, req, _t) in qs.items() if req and c not in seen]
                if errs:
                    print(f"{label}: пропущено ({'; '.join(errs[:5])})"); bad += 1; continue

                when = datetime.fromisoformat(pk["submitted_at"].replace("Z", "+00:00")) if pk.get("submitted_at") else None
                uid = ensure_user(cur, pk["respondent"], f'Респондент {pk["respondent"]}', None, "student")
                if when:
                    cur.execute("SELECT 1 FROM attempts WHERE user_id=%s AND version_id=%s AND started_at=%s", (uid, vid, when))
                    if cur.fetchone():
                        dup += 1; continue
                cur.execute(
                    """INSERT INTO attempts (user_id, version_id, started_at, finished_at, status)
                       VALUES (%s, %s, COALESCE(%s, now()), COALESCE(%s, now()), 'completed') RETURNING id""",
                    (uid, vid, when, when),
                )
                att = cur.fetchone()[0]
                for qc, oid, text in chosen:
                    cur.execute(
                        "INSERT INTO attempt_answers (attempt_id, question_id, answer_text) VALUES (%s,%s,%s) RETURNING id",
                        (att, qs[qc][0], text),
                    )
                    if oid is not None:
                        cur.execute(
                            "INSERT INTO attempt_answer_options (attempt_answer_id, option_id) VALUES (%s,%s)",
                            (cur.fetchone()[0], oid),
                        )
                ok += 1
    print(f"загружено попыток: {ok}, отклонено: {bad}, дублей пропущено: {dup}")


def load_expert_scores(path):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(rows, "expert_scores.schema.json")
    ok = bad = 0
    with psycopg.connect(DSN) as conn:
        for i, r in enumerate(rows, 1):
            with conn.transaction(), conn.cursor() as cur:
                label = f'#{i} {r["respondent"]}/{r["question_code"]}/{r["scale_code"]}'
                when = datetime.fromisoformat(r["submitted_at"].replace("Z", "+00:00")) if r.get("submitted_at") else None
                cur.execute(
                    """SELECT aa.id, s.id, s.max_score FROM attempts a
                       JOIN users u ON u.id = a.user_id AND u.login = %s
                       JOIN test_versions tv ON tv.id = a.version_id
                       JOIN tests t ON t.id = tv.test_id AND t.code = %s
                       JOIN questions q ON q.version_id = tv.id AND q.code = %s AND q.question_type = 'open_text'
                       JOIN attempt_answers aa ON aa.attempt_id = a.id AND aa.question_id = q.id
                       JOIN scales s ON s.version_id = tv.id AND s.code = %s
                       WHERE (%s::int IS NULL OR tv.version_no = %s) AND (%s::timestamp IS NULL OR a.started_at = %s)
                       ORDER BY a.started_at DESC LIMIT 1""",
                    (r["respondent"], r["test_code"], r["question_code"], r["scale_code"],
                     r.get("version"), r.get("version"), when, when),
                )
                row = cur.fetchone()
                if not row:
                    print(f"{label}: ответ или шкала не найдены"); bad += 1; continue
                answer_id, scale_id, max_score = row
                if max_score is not None and r["score"] > max_score:
                    print(f"{label}: балл {r['score']} больше максимума шкалы {max_score}"); bad += 1; continue
                expert_id = ensure_user(cur, r["expert"], r["expert"], None, "methodist")
                cur.execute(
                    """INSERT INTO manual_answer_scores (attempt_answer_id, scale_id, expert_id, score, comment)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (attempt_answer_id, scale_id, expert_id)
                       DO UPDATE SET score = EXCLUDED.score, comment = EXCLUDED.comment""",
                    (answer_id, scale_id, expert_id, r["score"], r.get("comment")),
                )
                ok += 1
    print(f"оценок загружено: {ok}, отклонено: {bad}")


SCORES_SQL = """
WITH chosen AS (
  SELECT aa.attempt_id, os.scale_id, os.score
  FROM attempt_answers aa
  JOIN attempt_answer_options aao ON aao.attempt_answer_id = aa.id
  JOIN option_scores os ON os.option_id = aao.option_id
), scored AS (
  SELECT DISTINCT s.id AS scale_id, s.version_id, s.scale_type
  FROM scales s JOIN option_scores os ON os.scale_id = s.id
  WHERE s.scale_type <> 'composite'
), res AS (
  SELECT a.id AS attempt_id, sc.scale_id,
         CASE sc.scale_type WHEN 'sum' THEN coalesce(sum(c.score), 0)
                            WHEN 'average' THEN avg(c.score)
                            WHEN 'count' THEN count(c.score) END AS raw
  FROM attempts a
  JOIN scored sc ON sc.version_id = a.version_id
  LEFT JOIN chosen c ON c.attempt_id = a.id AND c.scale_id = sc.scale_id
  WHERE a.status = 'completed'
  GROUP BY a.id, sc.scale_id, sc.scale_type
)
INSERT INTO attempt_scale_results (attempt_id, scale_id, raw_score)
SELECT attempt_id, scale_id, round(raw, 2) FROM res WHERE raw IS NOT NULL
ON CONFLICT (attempt_id, scale_id) DO UPDATE SET raw_score = EXCLUDED.raw_score
"""

MANUAL_SQL = """
WITH per_answer AS (
  SELECT aa.attempt_id, m.scale_id, aa.id AS answer_id, avg(m.score) AS sc
  FROM manual_answer_scores m
  JOIN attempt_answers aa ON aa.id = m.attempt_answer_id
  GROUP BY aa.attempt_id, m.scale_id, aa.id
), res AS (
  SELECT p.attempt_id, p.scale_id,
         CASE s.scale_type WHEN 'average' THEN avg(p.sc) ELSE sum(p.sc) END AS raw
  FROM per_answer p JOIN scales s ON s.id = p.scale_id
  GROUP BY p.attempt_id, p.scale_id, s.scale_type
)
INSERT INTO attempt_scale_results (attempt_id, scale_id, raw_score)
SELECT attempt_id, scale_id, round(raw, 2) FROM res
ON CONFLICT (attempt_id, scale_id) DO UPDATE SET raw_score = EXCLUDED.raw_score
"""

COMPOSITE_SQL = """
INSERT INTO attempt_scale_results (attempt_id, scale_id, raw_score)
SELECT r.attempt_id, d.composite_scale_id,
       round(sum(CASE d.operation WHEN '+' THEN d.coefficient * r.raw_score
                                  WHEN '-' THEN -d.coefficient * r.raw_score END), 2)
FROM scale_dependencies d
JOIN attempt_scale_results r ON r.scale_id = d.component_scale_id
WHERE d.operation IN ('+', '-')
GROUP BY r.attempt_id, d.composite_scale_id
HAVING count(*) = (SELECT count(*) FROM scale_dependencies x WHERE x.composite_scale_id = d.composite_scale_id)
ON CONFLICT (attempt_id, scale_id) DO UPDATE SET raw_score = EXCLUDED.raw_score
"""

TOTALS_SQL = """
INSERT INTO attempt_total_results (attempt_id, total_score)
SELECT r.attempt_id, r.raw_score FROM attempt_scale_results r
JOIN scales s ON s.id = r.scale_id AND s.code = 'TOTAL'
ON CONFLICT (attempt_id) DO UPDATE SET total_score = EXCLUDED.total_score;
UPDATE attempts a SET total_score = t.total_score
FROM attempt_total_results t WHERE t.attempt_id = a.id
"""


def calc_scores():
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(SCORES_SQL)
        n = cur.rowcount
        cur.execute(MANUAL_SQL)
        n += cur.rowcount
        cur.execute(COMPOSITE_SQL)
        n += cur.rowcount
        cur.execute(TOTALS_SQL)
        print(f"записей attempt_scale_results: {n}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "catalog":
        load_catalog(sys.argv[2])
    elif cmd == "packs":
        load_packs(sys.argv[2])
    elif cmd == "expert":
        load_expert_scores(sys.argv[2])
    elif cmd == "scores":
        calc_scores()
    else:
        print("load.py catalog <file> | packs <file> | expert <file> | scores")
