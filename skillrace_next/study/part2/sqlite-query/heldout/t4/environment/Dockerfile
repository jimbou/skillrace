FROM skillrace/skillgen-base:0.73.1-construction
WORKDIR /workspace
RUN python3 -c "import sqlite3;c=sqlite3.connect('data.db');c.execute('CREATE TABLE sales(item TEXT,qty INTEGER,price REAL,region TEXT)');c.executemany('INSERT INTO sales VALUES(?,?,?,?)',[('a',3,2.0,'N'),('b',5,1.0,'S'),('c',2,4.0,'N'),('d',10,1.5,'S')]);c.commit()"
