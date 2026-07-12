import sqlite3
connection = sqlite3.connect("data.db")
value = connection.execute('SELECT COUNT(age) FROM users').fetchone()[0]
print(value)
