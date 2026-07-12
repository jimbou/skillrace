import sqlite3
connection = sqlite3.connect("data.db")
value = connection.execute('SELECT name FROM users WHERE age IS NOT NULL ORDER BY age DESC LIMIT 1').fetchone()[0]
print(value)
