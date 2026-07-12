import sqlite3
connection = sqlite3.connect("data.db")
value = connection.execute('SELECT ROUND(AVG(age), 1) FROM users WHERE age IS NOT NULL').fetchone()[0]
print(value)
