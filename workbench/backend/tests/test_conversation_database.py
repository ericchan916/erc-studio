import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from conversation_database import ConversationDatabase,Result


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.lock=threading.RLock()
        self.db=ConversationDatabase(Path(self.tmp.name)/'test.sqlite3',self.lock)
    def tearDown(self):self.db.close();self.tmp.cleanup()

    def test_query_returns_materialized_rows_not_a_cross_thread_sqlite_cursor(self):
        self.db.execute('CREATE TABLE items(id INTEGER PRIMARY KEY,text TEXT)')
        first=self.db.execute('INSERT INTO items(text) VALUES(?)',('original',))
        self.assertEqual(first.rowcount,1);self.assertEqual(first.lastrowid,1)
        result=self.db.execute('SELECT * FROM items');self.assertIsInstance(result,Result)
        self.db.execute('UPDATE items SET text=?',('changed',))
        self.assertEqual(result.fetchone()['text'],'original');self.assertIsNone(result.fetchone())

    def test_even_direct_connection_api_uses_the_existing_service_lock(self):
        started=threading.Event();finished=threading.Event()
        def query():started.set();self.db.execute('SELECT 1').fetchall();finished.set()
        with self.lock:
            thread=threading.Thread(target=query);thread.start();self.assertTrue(started.wait(1));self.assertFalse(finished.wait(.05))
        thread.join(2);self.assertTrue(finished.is_set())

    def test_three_threads_serialize_prepare_execute_fetch_and_cursor_close(self):
        active=0;peak=0;calls=0;errors=[];guard=threading.Lock()
        def probe():
            nonlocal active,peak,calls
            with guard:active+=1;peak=max(peak,active);calls+=1
            time.sleep(.001)
            with guard:active-=1
            return 7
        with self.lock:self.db.connection.create_function('probe',0,probe)
        def work():
            try:
                for _ in range(100):self.assertEqual(self.db.execute('SELECT probe() value').fetchone()['value'],7)
            except Exception as error:errors.append(str(error))
        threads=[threading.Thread(target=work) for _ in range(3)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5)
        self.assertFalse(any(t.is_alive() for t in threads));self.assertEqual(errors,[]);self.assertEqual(calls,300);self.assertEqual(peak,1)

    def test_transactions_and_row_iteration_preserve_original_contract(self):
        self.db.executescript('CREATE TABLE items(id INTEGER);INSERT INTO items VALUES(1);INSERT INTO items VALUES(2);')
        self.db.execute('BEGIN IMMEDIATE');self.assertTrue(self.db.in_transaction)
        self.db.execute('INSERT INTO items VALUES(3)');self.db.execute('ROLLBACK');self.assertFalse(self.db.in_transaction)
        self.assertEqual([r['id'] for r in self.db.execute('SELECT * FROM items ORDER BY id')],[1,2])

if __name__=='__main__':unittest.main()
