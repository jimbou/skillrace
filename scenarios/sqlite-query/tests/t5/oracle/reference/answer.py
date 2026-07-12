import sqlite3
connection = sqlite3.connect("data.db")
value = connection.execute('SELECT SUM(qty * price) FROM sales').fetchone()[0]
print(value)
