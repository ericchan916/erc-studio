"""Serialize connection AND cursor lifetimes on the studio's shared lock.

macOS SQLite here is compiled THREADSAFE=2 (multi-thread, not serialized).
Returning a raw cursor lets callers/GC finalize statements outside the service
lock. Materialize query results and close that cursor while still holding it.
No global SQLite configuration, replacement driver or migration is involved.
"""
import sqlite3


class Result:
    def __init__(self, rows, rowcount, lastrowid, description):
        self.rows=rows;self.offset=0;self.rowcount=rowcount;self.lastrowid=lastrowid;self.description=description

    def fetchone(self):
        if self.offset>=len(self.rows):return None
        row=self.rows[self.offset];self.offset+=1;return row

    def fetchall(self):
        rows=self.rows[self.offset:];self.offset=len(self.rows);return rows

    def fetchmany(self,size=1):
        rows=self.rows[self.offset:self.offset+size];self.offset+=len(rows);return rows

    def __iter__(self):
        while self.offset<len(self.rows):yield self.fetchone()

    def close(self):
        self.rows=[];self.offset=0


class ConversationDatabase:
    def __init__(self, filename, lock):
        self.lock=lock
        self.connection=sqlite3.connect(filename,check_same_thread=False,isolation_level=None)
        self.connection.row_factory=sqlite3.Row

    @property
    def in_transaction(self):
        with self.lock:return self.connection.in_transaction

    def execute(self,sql,args=()):
        with self.lock:
            cursor=self.connection.execute(sql,args)
            try:
                rows=cursor.fetchall() if cursor.description is not None else []
                return Result(rows,cursor.rowcount,cursor.lastrowid,cursor.description)
            finally:cursor.close()

    def executescript(self,sql):
        with self.lock:
            cursor=self.connection.executescript(sql)
            try:return Result([],cursor.rowcount,cursor.lastrowid,cursor.description)
            finally:cursor.close()

    def close(self):
        with self.lock:self.connection.close()
