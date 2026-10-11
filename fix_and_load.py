import psycopg2
import re

with open("insert_tests_2.sql", "r", encoding="utf-8") as f:
    sql = f.read()

# 1. Удаляем лишний столбец с коротким именем (например, 'ВП', 'ЭРМ'), 
# чтобы значений стало ровно 7 и они совпали с колонками таблицы
sql = re.sub(r"\('([a-z_]+)',\s*'[А-ЯЁ]{2,4}',\s*('[^']+')", r"('\1', \2", sql)

# 2. Явно приводим числовые колонки к типу numeric
sql = sql.replace("s.min_score, s.max_score", "s.min_score::numeric, s.max_score::numeric")

# 3. Отрезаем проверочные SELECT в самом конце файла
if "-- ПРОВЕРКА" in sql:
    sql = sql.split("-- ПРОВЕРКА")[0]

print("Подключение к PostgreSQL...")
conn = psycopg2.connect("postgresql://postgres:pass@127.0.0.1:5432/tests")
cur = conn.cursor()

print("Выполнение SQL-запроса...")
cur.execute(sql)
conn.commit()

cur.close()
conn.close()

print("Успех! Лишние колонки удалены, все 4 теста загружены в базу!")