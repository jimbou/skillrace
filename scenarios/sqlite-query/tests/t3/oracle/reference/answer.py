import sqlite3
connection = sqlite3.connect("data.db")
value = connection.execute("SELECT COUNT(*) FROM sales WHERE region = 'N'").fetchone()[0]
print(value)
