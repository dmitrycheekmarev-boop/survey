import psycopg2

print("Подключение к базе данных...")
conn = psycopg2.connect("postgresql://postgres:pass@127.0.0.1:5432/tests")
cur = conn.cursor()

# Загружаем первый файл (3 теста)
print("Чтение insert_tests (2).sql...")
with open("insert_tests (2).sql", "r", encoding="utf-8") as f:
    sql1 = f.read()
    # Убираем блок SELECT в конце, чтобы psycopg2 не выдавал ошибку возврата данных
    if "-- ПРОВЕРКА" in sql1:
        sql1 = sql1.split("-- ПРОВЕРКА")[0]
    cur.execute(sql1)

# Загружаем второй файл (5 тестов)
print("Чтение finals.sql...")
with open("finals.sql", "r", encoding="utf-8") as f:
    sql2 = f.read()
    cur.execute(sql2)

conn.commit()
cur.close()
conn.close()

print("Успех! Все 8 новых тестов добавлены в базу данных!")