import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]/'src/workflow'))
import collector as c
import receipts as r

NOW=dt.datetime.now(dt.timezone.utc)

class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=pathlib.Path(self.tmp.name)
        self.spec={'path':'receipts.db','table':'jobs','column':'state','mode':'state','timeField':'updated_at','where':{}}
        self.db=self.base/'receipts.db'
        with sqlite3.connect(self.db) as connection:
            connection.execute('CREATE TABLE jobs(state TEXT, source TEXT, updated_at TEXT, payload TEXT)')
            connection.executemany('INSERT INTO jobs VALUES(?,?,?,?)',[
                ('succeeded','a',NOW.isoformat(),'PRIVATE_TEXT'),
                ('unknown','a',NOW.isoformat(),'SECRET_TOKEN'),
                ('running','b',NOW.isoformat(),'PRIVATE_PERSON')])

    def tearDown(self): self.tmp.cleanup()

    def read(self):return r.sql_receipt(self.base,self.spec,c.allowed_path,c.parse_time)

    def test_aggregate_receipt_read_only_no_payload(self):
        before=hashlib.sha256(self.db.read_bytes()).hexdigest()
        value=self.read()
        self.assertEqual(value['total'],3)
        self.assertEqual(value['activeCount'],1)
        self.assertEqual(value['attentionCount'],1)
        self.assertIn('lastRecordedSuccessAt',value)
        self.assertNotIn('PRIVATE',json.dumps(value))
        self.assertNotIn('SECRET',json.dumps(value))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),before)

    def test_scope_filter_is_exact_and_parameterized(self):
        self.spec['where']={'source':'a'}
        self.assertEqual(self.read()['counts'],{'succeeded':1,'unknown':1})
        self.spec['where']={'source':"a' OR 1=1 --"}
        self.assertEqual(self.read()['total'],0)

    def test_empty_success_is_not_unknown_zero(self):
        self.spec['where']={'source':'not-present'}
        value=self.read()
        self.assertEqual(value['state'],'read')
        self.assertEqual(value['total'],0)
        self.assertIsNone(value['lastRecordedSuccessAt'])

    def test_missing_and_schema_mismatch_do_not_report_zero(self):
        self.spec['path']='missing.db';self.assertIsNone(self.read()['total'])
        self.spec['path']='receipts.db';self.spec['timeField']='completed_at'
        self.assertEqual(self.read()['state'],'schema_mismatch')
        self.assertIsNone(self.read()['total'])

    def test_invalid_identifiers_and_sensitive_column_filters_denied(self):
        for key,value in [('table','jobs;DROP TABLE jobs'),('column','payload'),('timeField','payload'),('where',{'payload':'SECRET'})]:
            original=self.spec[key];self.spec[key]=value
            with self.assertRaises(ValueError):self.read()
            self.spec[key]=original

    def test_count_only_receipt_does_not_infer_state(self):
        self.spec['mode']='count';value=self.read()
        self.assertEqual(value['counts'],{'recorded':3})
        self.assertIsNone(value['lastRecordedSuccessAt'])

    def test_unknown_enum_values_never_echo(self):
        with sqlite3.connect(self.db) as connection:connection.execute('INSERT INTO jobs VALUES(?,?,?,?)',('MY_PRIVATE_STATE','a',NOW.isoformat(),'YOUR_TEST_SECRET'))
        value=self.read();self.assertEqual(value['counts']['other'],1);self.assertNotIn('MY_PRIVATE_STATE',json.dumps(value))

    def test_expired_failed_notification_is_historical_not_active(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute('CREATE TABLE notifications(state TEXT, expires_at TEXT, updated_at TEXT)')
            connection.executemany('INSERT INTO notifications VALUES(?,?,?)',[
                ('failed',(NOW-dt.timedelta(days=1)).isoformat(),NOW.isoformat()),
                ('failed',None,NOW.isoformat())])
        self.spec['table']='notifications';value=self.read()
        self.assertEqual(value['counts']['failed'],2)
        self.assertEqual(value['attentionCount'],1)
        self.assertEqual(value['historicalExpiredAttention'],1)

    def test_studio_json_reads_only_technical_state(self):
        root=self.base/'studio';root.mkdir()
        (root/'content-jobs.json').write_text(json.dumps([{'kind':'draft','state':'succeeded','finishedAt':NOW.isoformat(),'draft':'PRIVATE_DRAFT','actor':'PRIVATE_ACTOR'},{'kind':'review','state':'unknown'}]))
        value=r.json_receipt(self.base,{'mode':'studio-json','file':'content-jobs.json','kind':'draft'},c.allowed_path,c.parse_time,root)
        self.assertEqual(value['counts'],{'succeeded':1})
        self.assertNotIn('PRIVATE',json.dumps(value))

    def test_bar_json_does_not_echo_observations_or_actor(self):
        (self.base/'bar.json').write_text(json.dumps({'jobs':{'rec_PRIVATE':{'state':'ready','updated_at':NOW.isoformat(),'title':'PRIVATE_TITLE'}},'observations':{'PRIVATE':'ended'},'actor':'SECRET'}))
        value=r.json_receipt(self.base,{'mode':'bar-json','file':'bar.json'},c.allowed_path,c.parse_time,self.base)
        self.assertEqual(value['counts'],{'ready':1});self.assertNotIn('PRIVATE',json.dumps(value));self.assertNotIn('SECRET',json.dumps(value))

    def test_bad_json_preserved_and_adapter_failure_isolated(self):
        p=self.base/'broken.json';p.write_text('{broken')
        values=r.read_receipts(self.base,[{'mode':'bar-json','file':'broken.json'},self.spec],c.allowed_path,c.parse_time,self.base)
        self.assertEqual(values[0]['state'],'unavailable');self.assertEqual(values[1]['state'],'read');self.assertEqual(p.read_text(),'{broken')

    def test_external_health_contract_missing_counter_is_unknown(self):
        definition={'statusContract':'poll-counter-v1'}
        self.assertEqual(c.normalize_health(definition,{'last_poll_at':NOW.isoformat()})['status'],'unknown')
        self.assertEqual(c.normalize_health(definition,{'consecutive_failures':0})['status'],'ok')
        self.assertEqual(c.normalize_health(definition,{'consecutive_failures':1})['status'],'failed')

    def test_health_redirect_blocked(self):
        with self.assertRaisesRegex(ValueError,'redirect_denied'):c.NoRedirect().redirect_request(None,None,302,'redirect',{},'http://example.com')

    def test_numeric_job_timestamp_is_utc(self):
        self.assertEqual(r.timestamp(0,c.parse_time),'1970-01-01T00:00:00+00:00')
        self.assertIsNone(r.timestamp(float('nan'),c.parse_time))

if __name__=='__main__':unittest.main()
