import json,sqlite3,sys,tempfile,unittest,uuid,hashlib
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import Service
from domain import Problem
from conversation_history_import import read_personal_events,read_native_states


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.owner=str(uuid.uuid4());self.other=str(uuid.uuid4())
        self.model=mock.Mock();self.notify=mock.Mock()
        self.member={'principal_id':self.owner,'handle':'a','name':'A','tenant_key':'fixture','open_id':'ou_A','chat_id':'oc_A'}
        self.config={'state_dir':self.tmp.name,'auth_pepper':'fixture','feishu_profile':'fixture','unified_conversations':{'enabled':True},
            'members':[self.member,{**self.member,'principal_id':self.other,'handle':'b','open_id':'ou_B','chat_id':'oc_B'}]}
        self.s=Service(self.config,self.model,self.notify,start_worker=False);self.importer=self.s.unified.importer

    def tearDown(self):self.s.close();self.tmp.cleanup()

    def row(self,**extra):return {'source_id':'old-event','sender_id':'ou_A','chat_id':'oc_A','text':'  原始\n提问  ','answer':'原最终回复',
        'created_at':'2026-09-30T03:04:05Z','state':'done',**extra}

    def test_import_preserves_originals_timestamps_and_has_no_job_or_active_binding(self):
        row=self.row();result=self.importer.apply(self.owner,'oc_A:root',[row]);cid=result['conversation_id']
        self.assertEqual([r['text'] for r in self.s.messages(self.owner,cid)['items']],[row['text'],row['answer']])
        self.assertEqual({r['created_at'] for r in self.s.messages(self.owner,cid)['items']},{row['created_at']})
        self.assertEqual(self.s.unified.active(self.owner)['conversation_id'],None)
        self.assertEqual(self.s.one('SELECT count(*) FROM jobs')[0],0)
        self.assertEqual(self.s.messages(self.owner,cid)['items'][0]['source_refs'][0]['channel'],'feishu')
        self.model.assert_not_called();self.model.complete.assert_not_called();self.notify.ensure_active.assert_not_called()

    def test_repeat_and_restart_are_idempotent_without_clearing_user_lifecycle(self):
        result=self.importer.apply(self.owner,'root',[self.row()]);cid=result['conversation_id']
        meta=self.s.conversation_store.dto(self.s.check_conv(self.owner,cid))
        self.s.update_conversation(self.owner,cid,'trash',{'expected_version':meta['version']},str(uuid.uuid4()))
        self.s.close();self.s=Service(self.config,self.model,self.notify,start_worker=False);self.importer=self.s.unified.importer
        repeated=self.importer.apply(self.owner,'root',[self.row()])
        self.assertEqual((repeated['inserted'],repeated['existing']),(0,1));self.assertEqual(repeated['conversation_id'],cid)
        self.assertTrue(self.s.conversation_store.get(self.owner,cid,include_deleted=True)['deleted_at'])
        self.assertEqual(self.s.one('SELECT count(*) FROM messages')[0],2)

    def test_wrong_actor_or_chat_is_rejected_before_mutation_and_other_owner_cannot_read(self):
        for extra in ({'sender_id':'ou_B'},{'chat_id':'oc_B'}):
            with self.assertRaises(Problem):self.importer.apply(self.owner,'root',[self.row(**extra)])
        self.assertEqual(self.s.one('SELECT count(*) FROM conversations')[0],0)
        result=self.importer.apply(self.owner,'root',[self.row()])
        with self.assertRaises(Problem):self.s.messages(self.other,result['conversation_id'])

    def test_source_change_or_reassignment_rolls_back_the_whole_new_batch(self):
        self.importer.apply(self.owner,'root',[self.row()])
        for group,changed in [('root',self.row(answer='改过的回复')),('other',self.row())]:
            with self.assertRaises(Problem):self.importer.apply(self.owner,group,[self.row(source_id='new'),changed])
        self.assertEqual(self.s.one('SELECT count(*) FROM conversations')[0],1)
        self.assertEqual(self.s.one('SELECT count(*) FROM messages')[0],2)

    def test_unknown_history_is_viewable_but_blocked_until_original_receipt_resolves(self):
        first=self.row(answer=None,state='execution_unknown');later=self.row(source_id='old-later',text='后续问题')
        result=self.importer.apply(self.owner,'root',[first,later]);cid=result['conversation_id']
        self.assertTrue(result['pending']);self.assertEqual(self.s.conversation_store.dto(self.s.check_conv(self.owner,cid))['readonly_reason'],'imported_history_pending')
        with self.assertRaises(Problem):self.s.unified.bind(self.owner,cid)
        with self.assertRaises(Problem):self.s.unified.submit(self.owner,'web','new','do not replay',cid)
        self.assertEqual(self.s.one('SELECT count(*) FROM jobs')[0],0)
        resolved=self.importer.apply(self.owner,'root',[self.row(),later]);self.assertFalse(resolved['pending'])
        self.assertEqual([m['text'] for m in self.s.messages(self.owner,cid)['items']],['  原始\n提问  ','原最终回复','后续问题','原最终回复'])
        self.s.unified.bind(self.owner,cid)

    def test_offline_repository_start_does_not_recover_or_execute_existing_running_job(self):
        result=self.importer.apply(self.owner,'root',[self.row()]);cid=result['conversation_id']
        mid=self.s.one('SELECT id FROM messages WHERE role=?',('user',))[0];jid=str(uuid.uuid4())
        self.s.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',(jid,cid,self.owner,mid,str(uuid.uuid4()),'running','','old','old'))
        self.s.close();self.s=Service(self.config,self.model,self.notify,start_worker=False)
        self.assertEqual(self.s.check_job(self.owner,jid)['state'],'running');self.assertIsNone(self.s.worker)
        self.model.complete.assert_not_called()

    def test_old_receipt_appends_to_original_conversation_without_job_or_binding_change(self):
        old=self.importer.apply(self.owner,'old',[self.row(state='legacy_pending')]);cid=old['conversation_id']
        new=self.s.create_conversation(self.owner,{'title':'current'},str(uuid.uuid4()))['conversation_id'];self.s.unified.bind(self.owner,new)
        body={'source_id':'old-card-event','parent_source_id':'old-event','text':'确认 original','answer':'原工具最终回执','created_at':'2026-10-05T01:00:00Z','settled':True}
        result=self.importer.receipt(self.owner,body);self.assertTrue(result['imported']);self.assertFalse(result['duplicate'])
        self.assertTrue(self.importer.receipt(self.owner,body)['duplicate'])
        self.assertEqual([m['text'] for m in self.s.messages(self.owner,cid)['items']],['  原始\n提问  ','原最终回复','确认 original','原工具最终回执'])
        self.assertFalse(self.importer.pending(self.owner,cid));self.assertEqual(self.s.unified.active(self.owner)['conversation_id'],new)
        self.assertEqual(self.s.one('SELECT count(*) FROM jobs')[0],0);self.model.complete.assert_not_called()

    def test_old_receipt_cannot_rebind_owner_or_change_a_frozen_outcome(self):
        self.importer.apply(self.owner,'old',[self.row(state='legacy_pending')])
        body={'source_id':'receipt','parent_source_id':'old-event','text':'','answer':'original receipt','created_at':'old','settled':True}
        self.assertFalse(self.importer.receipt(self.other,body)['imported'])
        self.importer.receipt(self.owner,body)
        with self.assertRaises(Problem):self.importer.receipt(self.owner,{**body,'answer':'changed'})

    def test_native_pending_snapshot_marks_done_transport_event_readonly_until_original_task_finishes(self):
        path=Path(self.tmp.name)/'native.sqlite3';db=sqlite3.connect(path)
        db.executescript('CREATE TABLE native_tasks(id TEXT,event_id TEXT,chat_id TEXT,state TEXT,payload TEXT); CREATE TABLE native_task_events(event_id TEXT,task_id TEXT);')
        task={'user':'ou_A','chat_id':'oc_A','event_id':'original','state':'waiting_confirmation'}
        db.execute('INSERT INTO native_tasks VALUES(?,?,?,?,?)',('task','original','oc_A','waiting_confirmation',json.dumps(task)))
        db.execute('INSERT INTO native_task_events VALUES(?,?)',('original','task'));db.commit();db.close()
        self.assertEqual(read_native_states(path,self.member),{'original':'waiting_confirmation'})

    def test_snapshot_reader_filters_exact_private_pair_and_preserves_new_boundaries(self):
        path=Path(self.tmp.name)/'bridge-snapshot.sqlite3';db=sqlite3.connect(path)
        db.execute('CREATE TABLE events(event_id TEXT,payload_json TEXT,sender_id TEXT,chat_id TEXT,status TEXT,response_text TEXT,created_at TEXT)')
        for i,extra in enumerate([{}, {'content':'/new'}, {'chat_type':'group'}, {'sender_id':'ou_B'}, {'origin':'local_pipeline_guard'}, {'content':'next'}]):
            event={'sender_id':'ou_A','chat_id':'oc_A','chat_type':'p2p','content':'first',**extra}
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',('event-'+str(i),json.dumps(event),event['sender_id'],event['chat_id'],'done','answer','old-'+str(i)))
        db.commit();db.close();before=hashlib.sha256(path.read_bytes()).hexdigest()
        groups,skipped=read_personal_events(path,self.member)
        self.assertEqual(set(groups),{'oc_A:root','oc_A:event-1'})
        self.assertEqual([r['text'] for rs in groups.values() for r in rs],['first','/new','next'])
        self.assertEqual(skipped,{'non_personal':2});self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)

    def test_settled_original_receipt_is_not_downgraded_by_stale_native_snapshot(self):
        result=self.importer.apply(self.owner,'old',[self.row(state='legacy_pending')]);cid=result['conversation_id']
        self.importer.receipt(self.owner,{'source_id':'final','parent_source_id':'old-event','text':'','answer':'final receipt','created_at':'old','settled':True})
        self.importer.apply(self.owner,'old',[self.row(state='legacy_pending')])
        self.assertFalse(self.importer.pending(self.owner,cid))
        self.assertEqual(self.s.one('SELECT count(*) FROM jobs')[0],0)
