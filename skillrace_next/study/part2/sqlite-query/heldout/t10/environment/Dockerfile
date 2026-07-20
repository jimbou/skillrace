FROM skillrace/skillgen-base:0.73.1-construction
WORKDIR /workspace
RUN python3 -c "import sqlite3;c=sqlite3.connect('data.db');c.execute('CREATE TABLE users(name TEXT,age INTEGER,city TEXT)');c.executemany('INSERT INTO users VALUES(?,?,?)',[('a',20,'X'),('b',40,'Y'),('c',None,'X'),('d',30,'Y')]);c.commit()"
