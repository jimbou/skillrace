import sqlite3
connection = sqlite3.connect("data.db")
value = connection.execute("SELECT SUM(qty) FROM sales WHERE region = 'S'").fetchone()[0]
print(value)
