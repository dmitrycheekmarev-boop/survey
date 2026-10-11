#!/usr/bin/env python3
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "catalog.json"
OUT_BATCH2 = Path(__file__).resolve().parent / "catalog_batch2.json"


def _text(p):
    parts = []
    for n in p.iter():
        if n.tag == W + "t":
            parts.append(n.text or "")
        elif n.tag in (W + "br", W + "tab"):
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def read_docx(path):
    root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
    paras, tables = [], []
    for el in root.find(W + "body"):
        if el.tag == W + "p":
            paras.append(_text(el))
        elif el.tag == W + "tbl":
            tables.append([[" ".join(_text(p) for p in tc.iter(W + "p")).strip()
                            for tc in tr.findall(W + "tc")] for tr in el.iter(W + "tr")])
    return paras, tables


def find(folder, prefix):
    for p in sorted(folder.iterdir()):
        if p.name.lower().startswith(prefix.lower()) and p.suffix.lower() == ".docx":
            return p
    raise FileNotFoundError(prefix)


def numbers(s):
    return [int(x) for x in re.findall(r"\d+", s)]


def make_options(labels, raw_values, scores_by_scale_for_option):
    return [
        {
            "order_num": i,
            "text": labels[i - 1],
            "raw_value": raw_values[i - 1],
            "scores": scores_by_scale_for_option(i - 1),
        }
        for i in range(1, len(labels) + 1)
    ]


def build_version(instruction, scales, items, labels, raw_values, score_fn, code_prefix):
    scale_rows = []
    for s in scales:
        d = {k: v for k, v in s.items() if k != "items"}
        d.setdefault("description", None)
        d["interpretation"] = None
        scale_rows.append(d)

    questions = []
    for n, text in items:
        member = [s["code"] for s in scales if n in s["items"]]
        questions.append({
            "order_num": n,
            "code": f"{code_prefix}_q{n:02d}",
            "text": text,
            "question_type": "scale",
            "is_required": True,
            "options": make_options(
                labels, raw_values,
                lambda oi, n=n, member=member: [
                    {"scale_code": c, "score": score_fn(n, oi)} for c in member
                ],
            ),
        })
    return {
        "version_no": 1,
        "instruction": instruction,
        "is_published": True,
        "scales": scale_rows,
        "scale_dependencies": [],
        "questions": questions,
    }


def build_sjo():
    paras, tables = read_docx(find(SRC, "СЖО"))
    instruction = next(p for p in paras if p.startswith("Инструкция")).replace("Инструкция.", "", 1).strip()
    rows = tables[0]
    assert len(rows) == 20

    items = []
    for row in rows:
        left, _scale, right = row
        m = re.match(r"(\d+)\.\s*(.*)", left)
        n, left_text = int(m.group(1)), m.group(2).strip()
        right_text = right.strip().replace("осмысленной н целеустремленной", "осмысленной и целеустремленной")
        items.append((n, f"ЛЕВОЕ: {left_text} || ПРАВОЕ: {right_text}"))
    assert [n for n, _ in items] == list(range(1, 21))

    txt = " ".join(paras)
    asc = set(numbers(re.search(r"В восходящую шкалу[^-]*-\s*([\d, ]+)", txt).group(1)))
    desc = set(numbers(re.search(r"В нисходящую шкалу[^-]*-\s*([\d, ]+)", txt).group(1)))
    assert asc | desc == set(range(1, 21)) and not asc & desc

    def sub(label):
        return numbers(re.search(rf"Субшкала\s*\S+\s*\({label}\)\s*-\s*([\d, ]+)", txt).group(1))

    scales = [
        {"code": "TOTAL", "name": "Общий показатель осмысленности жизни (ОЖ)", "scale_type": "sum",
         "items": list(range(1, 21))},
        {"code": "GOALS", "name": "Цели в жизни", "scale_type": "sum", "items": sub("цели")},
        {"code": "PROCESS", "name": "Процесс жизни", "scale_type": "sum", "items": sub("процесс")},
        {"code": "RESULT", "name": "Результативность жизни", "scale_type": "sum", "items": sub("результат")},
        {"code": "LOCUS_SELF", "name": "Локус контроля - Я", "scale_type": "sum", "items": sub("локус контроля - Я")},
        {"code": "LOCUS_LIFE", "name": "Локус контроля - жизнь", "scale_type": "sum",
         "items": sub("локус контроля - жизнь")},
    ]
    for s in scales:
        s["min_score"] = len(s["items"]) * 1
        s["max_score"] = len(s["items"]) * 7

    labels = ["Левое, 3", "Левое, 2", "Левое, 1", "Оба равны (0)", "Правое, 1", "Правое, 2", "Правое, 3"]
    raw = ["LEFT_3", "LEFT_2", "LEFT_1", "BOTH", "RIGHT_1", "RIGHT_2", "RIGHT_3"]
    score = lambda n, oi: (oi + 1) if n in asc else (7 - oi)

    return {
        "code": "SJO",
        "title": "Тест смысложизненных ориентаций (СЖО)",
        "description": "Д. А. Леонтьев. 20 пар противоположных утверждений, ответ по шкале 3-2-1-0-1-2-3.",
        "is_active": True,
        "version": build_version(instruction, scales, items, labels, raw, score, "SJO"),
    }


def build_rean():
    paras, _ = read_docx(find(SRC, "Реан"))
    start = paras.index("Тестовый материал") + 1
    end = next(i for i, p in enumerate(paras) if p.startswith("Ключ к тесту"))
    instruction = paras[paras.index("Инструкция к тесту") + 1]

    items = []
    for p in paras[start:end]:
        if not p:
            continue
        m = re.match(r"(\d+)\.\s*(.*)", p)
        if m:
            items.append([int(m.group(1)), m.group(2).strip()])
        else:
            items[-1][1] += " " + p
    items = [(n, t) for n, t in items]
    assert [n for n, _ in items] == list(range(1, 35))

    codes = ["COMM", "AVOID", "PRESTIGE", "PROF", "CREATIVE", "COGNITIVE", "SOCIAL"]
    scales = []
    for p in paras[end + 1:]:
        m = re.match(r"Шкала\s+(\d+)\.\s*(.+?):\s*([\d, ]+)\.", p)
        if m:
            scales.append({"code": codes[int(m.group(1)) - 1], "name": m.group(2).strip(),
                           "scale_type": "average", "min_score": 1, "max_score": 5,
                           "items": numbers(m.group(3))})
    assert len(scales) == 7
    covered = sorted(i for s in scales for i in s["items"])
    assert covered == list(range(1, 35))

    return {
        "code": "REAN_BADMAEV",
        "title": "Мотивация учебной деятельности (Реан, Бадмаев)",
        "description": "34 мотива учебной деятельности, оценка значимости от 1 до 5, 7 шкал (среднее по шкале).",
        "is_active": True,
        "version": build_version(instruction, scales, items,
                                 ["1", "2", "3", "4", "5"], ["1", "2", "3", "4", "5"],
                                 lambda n, oi: oi + 1, "REAN"),
    }


def build_poak():
    paras, tables = read_docx(find(SRC, "ПОАК"))
    instruction = next(p for p in paras if p.startswith("Инструкция")).split(":", 1)[1].strip()
    items = []
    for row in tables[0]:
        if row and row[0].isdigit():
            items.append((int(row[0]), row[1].strip()))
    assert [n for n, _ in items] == list(range(1, 13))
    header = next(r for r in tables[0] if "никогда" in r)
    labels = header[-5:]
    assert labels == ["никогда", "редко", "иногда", "часто", "всегда"]

    def rng(a, b):
        return list(range(a, b + 1))

    scales = [
        {"code": "HARM", "name": "Нанесение вреда", "items": rng(1, 6)},
        {"code": "LIES", "name": "Ложь", "items": rng(7, 9) + [12]},
        {"code": "JOKES", "name": "Злые шутки", "items": rng(10, 12)},
        {"code": "TOTAL", "name": "Интегральный показатель антисоциальной креативности", "items": rng(1, 12)},
    ]
    for s in scales:
        s["scale_type"] = "sum"
        s["min_score"] = 0
        s["max_score"] = len(s["items"]) * 4

    return {
        "code": "POAK",
        "title": "ПОАК",
        "description": "12 вопросов о частоте (никогда - всегда), баллы 0-4, три шкалы и интегральный показатель.",
        "is_active": True,
        "version": build_version(instruction, scales, items, labels, ["0", "1", "2", "3", "4"],
                                 lambda n, oi: oi, "POAK"),
    }


def scale_row(code, name, scale_type, min_score, max_score, description=None, interpretation=None):
    return {"code": code, "name": name, "description": description, "scale_type": scale_type,
            "min_score": min_score, "max_score": max_score, "interpretation": interpretation}


def build_kass():
    paras, _ = read_docx(find(SRC, "Стили"))
    instruction = paras[paras.index("Инструкция к тесту") + 1]
    start = paras.index("Тестовые вопросы") + 1
    end = next(i for i, p in enumerate(paras) if p.startswith("Ключ к расчету"))

    pairs = []
    for p in paras[start:end]:
        if not p:
            continue
        m = re.fullmatch(r"(\d+)\.", p)
        if m:
            pairs.append((int(m.group(1)), []))
            continue
        m = re.match(r"([АБВГ]):\s*(.*)", p)
        assert m, p
        pairs[-1][1].append((m.group(1), m.group(2).strip()))
    assert [n for n, _ in pairs] == list(range(1, 11)) and all(len(o) == 2 for _, o in pairs)

    latin = {"А": "A", "Б": "B", "В": "C", "Г": "D"}
    codes = {"A": "ACTION", "B": "PEOPLE", "C": "PROCESS", "D": "IDEAS"}
    names = {}
    for p in paras[end:]:
        m = re.match(r"Количество букв (\S+) .*Стиль «(.+?)»", p)
        if m:
            names[latin[m.group(1)]] = m.group(2)
    assert sorted(names) == ["A", "B", "C", "D"]
    rule = "От 4 баллов и выше - доминирующий стиль. Если два стиля набрали поровну, совмещаются обе стратегии."

    scales = []
    for letter, code in codes.items():
        n = sum(1 for _, o in pairs for cyr, _ in o if latin[cyr] == letter)
        scales.append(scale_row(code, f"Стиль «{names[letter]}»", "sum", 0, n,
                                f"Число выбранных утверждений с буквой {letter}.", rule))

    questions = []
    for n, opts in pairs:
        questions.append({
            "order_num": n,
            "code": f"KASS_q{n:02d}",
            "text": f"Пара {n}. Выберите одно утверждение, которое лучше описывает ваше поведение или ценности в общении.",
            "question_type": "single_choice",
            "is_required": True,
            "options": [
                {"order_num": i, "text": f"{cyr}: {txt}", "raw_value": latin[cyr],
                 "scores": [{"scale_code": codes[latin[cyr]], "score": 1}]}
                for i, (cyr, txt) in enumerate(opts, 1)
            ],
        })
    return {
        "code": "KASS_COMM",
        "title": "Стили коммуникации (П. Касс)",
        "description": "10 пар утверждений, в каждой выбирается одно. Четыре стиля: Действие, Люди, Процесс, Идеи.",
        "is_active": True,
        "version": {"version_no": 1, "instruction": instruction, "is_published": True,
                    "scales": scales, "scale_dependencies": [], "questions": questions},
    }


def build_nfc():
    paras, tables = read_docx(find(SRC, "тест_Потребность"))
    i = next(i for i, p in enumerate(paras) if p.startswith("Инструкция"))
    j = next(i for i, p in enumerate(paras) if p.startswith("1 — абсолютно не согласен"))
    instruction = "\n".join(paras[i:j + 1]).replace("Инструкция:", "", 1).strip()

    rows = tables[0]
    label_by_value = {int(v): l for l, v in zip(rows[0][1:], rows[1][1:])}
    assert sorted(label_by_value) == list(range(1, 8))
    items = []
    for row in rows[2:]:
        m = re.match(r"(\d+)\.\s*(.*)", row[0])
        items.append((int(m.group(1)), m.group(2).strip()))
    assert [n for n, _ in items] == list(range(1, 19))

    kinds = tables[1]
    reverse = {int(n) for n, k in zip(kinds[0][1:], kinds[1][1:]) if k.startswith("обратн")}
    assert len(kinds[0][1:]) == 18 and len(reverse) == 9

    questions = []
    for n, text in items:
        questions.append({
            "order_num": n,
            "code": f"NFC_q{n:02d}",
            "text": text,
            "question_type": "scale",
            "is_required": True,
            "options": [
                {"order_num": v, "text": label_by_value[v], "raw_value": str(v),
                 "scores": [{"scale_code": "TOTAL", "score": (8 - v) if n in reverse else v}]}
                for v in range(1, 8)
            ],
        })
    return {
        "code": "NEED_COGNITION",
        "title": "Шкала потребности в познании",
        "description": "18 утверждений, согласие по шкале от 1 до 7, один общий балл. Обратные пункты: "
                       + ", ".join(map(str, sorted(reverse))) + ".",
        "is_active": True,
        "version": {"version_no": 1, "instruction": instruction, "is_published": True,
                    "scales": [scale_row("TOTAL", "Потребность в познании (сумма баллов)", "sum", 18, 126)],
                    "scale_dependencies": [], "questions": questions},
    }


def task_numbers(s):
    out = []
    for part in s.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
        out += list(range(int(m.group(1)), int(m.group(2)) + 1)) if m else [int(part)]
    return out


def key_points(s):
    mult = 1
    if "×" in s:
        s, m = s.split("×")
        mult = int(m)
    return sum(int(x) for x in s.split("/")) * mult


def build_ct():
    paras, tables = read_docx(find(SRC, "Тест критическое"))
    instruction = paras[paras.index("Инструкция") + 1]
    start = paras.index("Текст заданий") + 1
    end = next(i for i, p in enumerate(paras) if p.startswith("Ключ (решения"))

    tasks = {}
    cur = None
    for p in paras[start:end]:
        m = re.match(r"Задание (\d+)\.\s*(.*)", p)
        if m:
            cur = int(m.group(1))
            tasks[cur] = [m.group(2).strip()]
        elif p:
            tasks[cur].append(p)
    assert sorted(tasks) == list(range(1, 16))

    key = {}
    for row in tables[0][1:]:
        key[int(row[0])] = (row[1], key_points(row[2]))
    assert sorted(key) == list(range(1, 16))
    total = sum(p for _, p in key.values())
    assert total == 46, total

    cat_codes = ["INFERENCE", "SEQUENCE", "CAUSES", "TEXTS", "TERMS", "RELEVANCE"]
    cat_names = ["Логические умозаключения", "Оценка последовательностей умозаключений",
                 "Анализ причин явлений", "Анализ и оценка содержания текстов",
                 "Неопределённость и двусмысленность терминов", "Поиск релевантной информации"]
    levels = ("Категория сформирована (правильный ответ и обоснование), частично сформирована "
              "(правильный ответ без обоснования) или не сформирована (нет ни ответа, ни обоснования).")
    scales, deps, task_cat = [], [], {}
    for row, code, name in zip(tables[1][1:], cat_codes, cat_names):
        nums = task_numbers(row[1])
        for n in nums:
            task_cat[n] = code
        scales.append(scale_row(code, name, "sum", 0, sum(key[n][1] for n in nums), row[2], levels))
        deps.append({"composite_scale_code": "TOTAL", "component_scale_code": code,
                     "coefficient": 1, "operation": "+"})
    assert sorted(task_cat) == list(range(1, 16))
    scales.append(scale_row("TOTAL", "Общий балл критического мышления", "composite", 0, total,
                            "Сумма баллов шести категорий."))

    questions = []
    for n in range(1, 16):
        answer, points = key[n]
        questions.append({
            "order_num": n,
            "code": f"CT_q{n:02d}",
            "text": " ".join(tasks[n]),
            "question_type": "open_text",
            "is_required": True,
            "options": [{"order_num": 1, "text": f"Эталон: {answer} Максимум баллов: {points}.",
                         "raw_value": None, "is_correct": True, "scores": []}],
        })
    return {
        "code": "CRIT_THINKING",
        "title": "Тест-опросник критического мышления (Д. Кинчер)",
        "description": "15 заданий с открытым ответом и обоснованием, максимум 46 баллов, оценивает методист.",
        "is_active": True,
        "version": {"version_no": 1, "instruction": instruction, "is_published": True,
                    "scales": scales, "scale_dependencies": deps, "questions": questions},
    }


AUTHOR = {
    "login": "alisova.a",
    "full_name": "Алисова Арина Алексеевна",
    "email": "arina@alisova.com",
    "role": "methodist",
}


def write_catalog(path, tests):
    catalog = {"format_version": "1.0", "author": AUTHOR, "tests": tests}
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for t in tests:
        v = t["version"]
        n_scores = sum(len(o["scores"]) for q in v["questions"] for o in q["options"])
        print(f'{t["code"]}: вопросов {len(v["questions"])}, шкал {len(v["scales"])}, строк option_scores {n_scores}')
    print("->", path)


def main():
    batch1 = [build_sjo(), build_rean(), build_poak()]
    batch2 = [build_kass(), build_nfc(), build_ct()]
    write_catalog(OUT, batch1 + batch2)
    write_catalog(OUT_BATCH2, batch2)


if __name__ == "__main__":
    main()
