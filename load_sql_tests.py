import psycopg2

print("Подключение к базе данных...")
conn = psycopg2.connect("postgresql://postgres:pass@127.0.0.1:5432/tests")
cur = conn.cursor()

# Загружаем новый тест (Методика СО)
print("Чтение final_finals.sql...")
with open("final_finals.sql", "r", encoding="utf-8") as f:
    sql = f.read()
    cur.execute(sql)

conn.commit()
cur.close()
conn.close()

print("Успех! Методика СО (Самоотношение) успешно добавлена в базу данных!")