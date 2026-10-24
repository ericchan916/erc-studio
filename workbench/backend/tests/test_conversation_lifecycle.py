# -*- coding: utf-8 -*-
import hashlib, http.client, json, sqlite3, sys, tempfile, threading, time, unittest, uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import Service,Problem
from server import handler_for,ThreadingHTTPServer
from test_gateway import Sender,Model

class LifecycleTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.model=Model();self.sender=Sender();self.a=str(uuid.uuid4());self.b=str(uuid.uuid4());self.config={'state_dir':self.temp.name,'auth_pepper':'fixture-v2-'+uuid.uuid4().hex,'feishu_profile':'fixture','members':[{'handle':'a','principal_id':self.a,'name':'A','tenant_key':'tenant','open_id':'ou_a'},{'handle':'b','principal_id':self.b,'name':'B','tenant_key':'tenant','open_id':'ou_b'}]};self.s=Service(self.config,self.model,self.sender)
 def tearDown(self):self.model.release.set();self.s.close();self.temp.cleanup()
 def create(self,owner,title='test'):return self.s.create_conversation(owner,{'title':title},str(uuid.uuid4()))
 def mutate(self,owner,cid,action,**fields):
  v=self.s.conversation_store.get(owner,cid,True)['version'];return self.s.update_conversation(owner,cid,action,{'expected_version':v,**fields},str(uuid.uuid4()))
 def submit(self,cid,text='private text'):
  return self.s.submit(self.a,cid,{'text':text,'client_message_id':str(uuid.uuid4())},str(uuid.uuid4()))
 def wait(self,jid):
  for _ in range(200):
   row=self.s.one('SELECT * FROM jobs WHERE id=?',(jid,))
   if row['state'] in ('succeeded','failed','cancelled'):return row
   time.sleep(.01)
  self.fail('job_timeout')
 def test_first_message_atomic_creation_long_key_and_replay(self):
  body={'title':'first','text':'raw first text','client_message_id':str(uuid.uuid4())};key='k'*100
  first=self.s.start_message(self.a,body,key);again=self.s.start_message(self.a,body,key)
  self.assertEqual(first,again);self.wait(first['job_id']);self.assertEqual(len(self.s.conversations(self.a)['items']),1);self.assertEqual(len(self.s.messages(self.a,first['conversation_id'])['items']),2)
  self.mutate(self.a,first['conversation_id'],'rename',title='now current')
  self.assertEqual(self.s.start_message(self.a,body,key)['conversation']['title'],'now current')
  self.mutate(self.a,first['conversation_id'],'trash')
  with self.assertRaises(Problem):self.s.start_message(self.a,body,key)
 def test_first_message_busy_failure_rolls_back_empty_conversation_and_idempotency(self):
  self.model.release.clear();cid=self.create(self.a)['conversation_id'];job=self.submit(cid);before=self.s.one('SELECT count(*) FROM conversations')[0]
  body={'title':'must rollback','text':'first','client_message_id':str(uuid.uuid4())};key=str(uuid.uuid4())
  with self.assertRaises(Problem) as e:self.s.start_message(self.a,body,key)
  self.assertEqual(e.exception.code,'member_busy');self.assertEqual(self.s.one('SELECT count(*) FROM conversations')[0],before);self.assertIsNone(self.s.one('SELECT * FROM idempotency WHERE owner=? AND scope=? AND key=?',(self.a,'start-message',key)))
  self.model.release.set();self.wait(job['job_id']);accepted=self.s.start_message(self.a,body,key);self.wait(accepted['job_id']);self.assertEqual(self.s.one('SELECT count(*) FROM conversations')[0],before+1)
 def test_first_message_validation_and_same_key_are_independent_per_member(self):
  key=str(uuid.uuid4());body={'title':'A','text':'first','client_message_id':str(uuid.uuid4())}
  with self.assertRaises(Problem):self.s.start_message(self.a,{**body,'owner':self.b},key)
  with self.assertRaises(Problem):self.s.start_message(self.a,{**body,'title':''},key)
  a=self.s.start_message(self.a,body,key);b=self.s.start_message(self.b,{**body,'title':'B'},key);self.assertNotEqual(a['conversation_id'],b['conversation_id']);self.wait(a['job_id']);self.wait(b['job_id'])
 def test_connection_read_waits_for_shared_lock_in_another_thread(self):
  started=threading.Event();done=threading.Event();result=[]
  def read():
   started.set();result.append(self.s.one('PRAGMA integrity_check')[0]);done.set()
  with self.s.lock:
   t=threading.Thread(target=read);t.start();self.assertTrue(started.wait(1));blocked=not done.wait(.05)
  t.join(1);self.assertTrue(blocked,'all shared SQLite reads must use the same lock');self.assertTrue(done.is_set());self.assertEqual(result,['ok'])
 def test_full_lifecycle_each_owner_has_equal_functions(self):
  for owner in (self.a,self.b):
   c=self.create(owner)['conversation_id'];self.mutate(owner,c,'rename',title='renamed');self.mutate(owner,c,'pin',pinned=True);self.mutate(owner,c,'archive',archived=True)
   self.assertEqual(self.s.conversations(owner)['items'],[]);self.assertEqual(self.s.conversations(owner,scope='archived')['items'][0]['title'],'renamed')
   self.mutate(owner,c,'trash');self.assertEqual(self.s.conversations(owner,scope='trash')['items'][0]['conversation_id'],c)
   with self.assertRaises(Problem):self.s.messages(owner,c)
   self.mutate(owner,c,'restore');self.assertEqual(self.s.conversations(owner,scope='archived')['items'][0]['pinned'],True);self.mutate(owner,c,'archive',archived=False);self.assertEqual(len(self.s.conversations(owner)['items']),1)
 def test_all_mutations_and_cursor_reject_other_member(self):
  c=self.create(self.a)['conversation_id']
  for action,fields in [('rename',{'title':'stolen'}),('pin',{'pinned':True}),('archive',{'archived':True}),('trash',{}),('restore',{})]:
   with self.assertRaises(Problem) as e:self.s.update_conversation(self.b,c,action,{'expected_version':0,**fields},str(uuid.uuid4()))
   self.assertEqual(e.exception.status,404)
  for _ in range(3):self.create(self.a)
  cursor=self.s.conversations(self.a,limit=1)['next_cursor']
  with self.assertRaises(Problem) as e:self.s.conversations(self.b,limit=1,cursor=cursor)
  self.assertEqual(e.exception.code,'invalid_cursor')
 def test_version_conflict_and_idempotency_do_not_overwrite_new_title(self):
  c=self.create(self.a);cid=c['conversation_id'];body={'title':'first','expected_version':0};key=str(uuid.uuid4());r=self.s.update_conversation(self.a,cid,'rename',body,key)
  self.assertEqual(self.s.update_conversation(self.a,cid,'rename',body,key),r)
  with self.assertRaises(Problem) as e:self.s.update_conversation(self.a,cid,'rename',{'title':'late','expected_version':0},str(uuid.uuid4()))
  self.assertEqual(e.exception.code,'conversation_conflict');self.assertEqual(self.s.conversation_store.get(self.a,cid)['title'],'first')
 def test_literal_search_title_and_messages_owner_scope(self):
  c=self.create(self.a,'100%_target');other=self.create(self.a,'100xyz');self.create(self.b,'100%_target');self.assertEqual(len(self.s.conversations(self.a,search='%_')['items']),1)
  j=self.submit(other['conversation_id'],'独有正文词');self.wait(j['job_id']);self.assertEqual(self.s.conversations(self.a,search='独有正文词')['items'][0]['conversation_id'],other['conversation_id']);self.assertEqual(self.s.conversations(self.b,search='独有正文词')['items'],[])
 def test_more_than_100_conversations_paged_without_duplicates_and_pinned_first(self):
  ids=[]
  for i in range(137):ids.append(self.create(self.a,'item'+str(i))['conversation_id'])
  self.mutate(self.a,ids[0],'pin',pinned=True);seen=[];cursor=None
  while True:
   page=self.s.conversations(self.a,limit=17,cursor=cursor);seen.extend(x['conversation_id'] for x in page['items']);cursor=page['next_cursor']
   if not cursor:break
  self.assertEqual(seen[0],ids[0]);self.assertEqual(len(seen),137);self.assertEqual(set(seen),set(ids))
 def test_cursor_rejects_stale_query_or_scope(self):
  for i in range(3):self.create(self.a)
  cursor=self.s.conversations(self.a,limit=1)['next_cursor'];self.create(self.a)
  with self.assertRaises(Problem) as e:self.s.conversations(self.a,limit=1,cursor=cursor)
  self.assertEqual(e.exception.code,'list_changed')
  with self.assertRaises(Problem):self.s.conversations(self.a,limit=1,scope='trash',cursor=cursor)
 def test_message_paging_over_100_keeps_order_and_isolation(self):
  cid=self.create(self.a)['conversation_id']
  with self.s.transaction():
   for i in range(235):self.s.db.execute('INSERT INTO messages(id,conversation,owner,role,text,created) VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),cid,self.a,'user',str(i),'fixture'))
  page=self.s.messages(self.a,cid,limit=40,offset=-1);seen=list(page['items'])
  while page['older_cursor']:
   page=self.s.messages(self.a,cid,limit=40,offset=-1,before=int(page['older_cursor']));seen=page['items']+seen
  self.assertEqual([m['text'] for m in seen],[str(i) for i in range(235)])
  with self.assertRaises(Problem):self.s.messages(self.b,cid,limit=40,offset=-1,before=200)
 def test_soft_delete_retains_content_restart_restore_keeps_original_data(self):
  cid=self.create(self.a)['conversation_id'];j=self.submit(cid,'saved original');self.wait(j['job_id']);before=[m['text'] for m in self.s.messages(self.a,cid)['items']];self.mutate(self.a,cid,'trash');self.s.close();self.s=Service(self.config,self.model,self.sender)
  self.assertEqual(self.s.conversations(self.a)['items'],[]);self.mutate(self.a,cid,'restore');self.assertEqual([m['text'] for m in self.s.messages(self.a,cid)['items']],before)
 def test_running_delete_cancels_and_late_answer_never_resurrects_even_if_restored(self):
  self.model.release.clear();cid=self.create(self.a)['conversation_id'];j=self.submit(cid)
  for _ in range(100):
   if self.s.one('SELECT state FROM jobs WHERE id=?',(j['job_id'],))['state']=='running':break
   time.sleep(.01)
  self.mutate(self.a,cid,'trash');self.mutate(self.a,cid,'restore');self.model.release.set();row=self.wait(j['job_id']);self.assertEqual(row['state'],'cancelled');self.assertEqual(len(self.s.messages(self.a,cid)['items']),1)
 def test_archive_allows_existing_job_result_but_blocks_new_send(self):
  self.model.release.clear();cid=self.create(self.a)['conversation_id'];j=self.submit(cid);self.mutate(self.a,cid,'archive',archived=True);self.model.release.set();self.wait(j['job_id']);self.assertEqual(len(self.s.messages(self.a,cid)['items']),2)
  with self.assertRaises(Problem) as e:self.submit(cid)
  self.assertEqual(e.exception.code,'conversation_archived')
 def test_failed_retry_does_not_duplicate_user_message_or_model_context(self):
  cid=self.create(self.a)['conversation_id'];j=self.submit(cid,'retry body');self.wait(j['job_id']);self.s.db.execute('DELETE FROM messages WHERE conversation=? AND role=?',(cid,'assistant'));self.s.db.execute("UPDATE jobs SET state='failed' WHERE id=?",(j['job_id'],));version=self.s.conversation_store.get(self.a,cid)['version'];body={'client_message_id':str(uuid.uuid4()),'expected_version':version};key=str(uuid.uuid4());r=self.s.retry_job(self.a,j['job_id'],body,key);self.assertEqual(self.s.retry_job(self.a,j['job_id'],body,key),r);self.wait(r['job_id']);messages=self.s.messages(self.a,cid)['items'];self.assertEqual([m['role'] for m in messages],['user','assistant']);self.assertEqual(messages[0]['job_id'],r['job_id'])
  with self.assertRaises(Problem):self.s.retry_job(self.b,j['job_id'],body,str(uuid.uuid4()))
 def test_unknown_retry_requires_explicit_confirmation_and_unblocks_conversation(self):
  cid=self.create(self.a)['conversation_id'];j=self.submit(cid);self.wait(j['job_id']);self.s.db.execute("UPDATE jobs SET state='execution_unknown' WHERE id=?",(j['job_id'],));version=self.s.conversation_store.get(self.a,cid)['version'];body={'client_message_id':str(uuid.uuid4()),'expected_version':version}
  with self.assertRaises(Problem) as e:self.s.retry_job(self.a,j['job_id'],body,str(uuid.uuid4()))
  self.assertEqual(e.exception.code,'retry_confirmation_required');r=self.s.retry_job(self.a,j['job_id'],{**body,'acknowledge_unknown':True},str(uuid.uuid4()));self.wait(r['job_id']);new=self.submit(cid,'next');self.wait(new['job_id'])
 def test_metadata_validation_rejects_caller_identity_and_wrong_types(self):
  cid=self.create(self.a)['conversation_id']
  for action,body in [('pin',{'pinned':'true','expected_version':0}),('rename',{'title':'','expected_version':0}),('trash',{'expected_version':True}),('archive',{'archived':True,'expected_version':0,'owner':self.b})]:
   with self.assertRaises(Problem):self.s.update_conversation(self.a,cid,action,body,str(uuid.uuid4()))
 def test_schema_expand_is_idempotent_and_legacy_job_insert_still_works(self):
  self.s.conversation_store.migrate();self.s.conversation_store.migrate();self.assertEqual(self.s.one('PRAGMA user_version')[0],2);self.assertEqual(len(list(self.s.db.execute('PRAGMA table_info(jobs)'))),9);self.assertEqual(self.s.one('PRAGMA integrity_check')[0],'ok')
 def test_http_lifecycle_rejects_unknown_user_actor_and_supports_restore(self):
  self.config['allowed_hosts']=['127.0.0.1:0'];self.config['allowed_origins']=[];server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.s));self.config['allowed_hosts']=['127.0.0.1:'+str(server.server_port)];threading.Thread(target=server.serve_forever,daemon=True).start();token=self.s.grant_oauth({'open_id':'ou_a','tenant_key':'tenant'})['session_credential'];btoken=self.s.grant_oauth({'open_id':'ou_b','tenant_key':'tenant'})['session_credential'];cid=self.create(self.a)['conversation_id']
  def req(route,body=None,t=token):
   con=http.client.HTTPConnection('127.0.0.1',server.server_port);con.request('POST' if body is not None else 'GET',route,json.dumps(body) if body is not None else None,headers={'Authorization':'Bearer '+t,'Idempotency-Key':str(uuid.uuid4()),'Content-Type':'application/json'});r=con.getresponse();status,data=r.status,json.loads(r.read());con.close();return status,data
  try:
   self.assertEqual(req('/v1/conversations/'+cid+'/trash',{'expected_version':0},btoken)[0],404);self.assertEqual(req('/v1/conversations/'+cid+'/trash',{'expected_version':0})[0],200);self.assertEqual(req('/v1/conversations?scope=trash')[1]['items'][0]['conversation_id'],cid);self.assertEqual(req('/v1/conversations/'+cid+'/messages')[0],404);self.assertEqual(req('/v1/conversations/'+cid+'/restore',{'expected_version':1})[0],200);self.assertEqual(req('/v1/conversations?owner=other')[0],400)
  finally:server.shutdown();server.server_close()
 def test_http_two_member_operation_search_and_large_pagination_matrix(self):
  server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.s));self.config['allowed_hosts']=['127.0.0.1:'+str(server.server_port)];self.config['allowed_origins']=[];thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  tokens={owner:self.s.grant_oauth({'open_id':'ou_a' if owner==self.a else 'ou_b','tenant_key':'tenant'})['session_credential'] for owner in (self.a,self.b)}
  def req(owner,route,body=None,key=None):
   c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5);c.request('POST' if body is not None else 'GET',route,json.dumps(body) if body is not None else None,headers={'Authorization':'Bearer '+tokens[owner],'Content-Type':'application/json','Idempotency-Key':key or str(uuid.uuid4())});r=c.getresponse();result=r.status,json.loads(r.read());c.close();return result
  try:
   for owner,other in ((self.a,self.b),(self.b,self.a)):
    status,first=req(owner,'/v1/conversations/start-message',{'title':'owner first','text':'private marker '+owner,'client_message_id':str(uuid.uuid4())});self.assertEqual(status,202);cid=first['conversation_id'];jid=first['job_id'];self.wait(jid)
    for route,body in [('/v1/conversations/'+cid,None),('/v1/conversations/'+cid+'/messages',None),('/v1/conversations/'+cid+'/messages',{'text':'stolen','client_message_id':str(uuid.uuid4())}),('/v1/jobs/'+jid,None),('/v1/jobs/'+jid+'/events',None),('/v1/jobs/'+jid+'/cancel',{}),('/v1/jobs/'+jid+'/retry',{'client_message_id':str(uuid.uuid4()),'expected_version':0})]+[('/v1/conversations/'+cid+'/'+action,{'expected_version':0,**fields}) for action,fields in [('rename',{'title':'stolen'}),('pin',{'pinned':True}),('archive',{'archived':True}),('trash',{}),('restore',{})]]:
     self.assertEqual(req(other,route,body)[0],404,route)
    def mutate(action,**fields):
     version=req(owner,'/v1/conversations/'+cid)[1]['version'];r=req(owner,'/v1/conversations/'+cid+'/'+action,{'expected_version':version,**fields});self.assertEqual(r[0],200);return r[1]
    mutate('rename',title='visible '+owner);mutate('pin',pinned=True);mutate('archive',archived=True)
    self.assertTrue(any(x['conversation_id']==cid for x in req(owner,'/v1/conversations?scope=archived')[1]['items']));self.assertEqual(req(other,'/v1/conversations?q='+owner)[1]['items'],[])
    mutate('trash');self.assertEqual(req(owner,'/v1/conversations/'+cid+'/messages')[0],404);mutate('restore');mutate('archive',archived=False);mutate('pin',pinned=False)
    ids={cid}
    for i in range(123):
     status,new=req(owner,'/v1/conversations',{'title':'page '+str(i)});self.assertEqual(status,201);ids.add(new['conversation_id'])
    seen=[];cursor=None
    while True:
     route='/v1/conversations?limit=19'+('&cursor='+cursor if cursor else '');status,page=req(owner,route);self.assertEqual(status,200);seen.extend(x['conversation_id'] for x in page['items']);cursor=page['next_cursor']
     if not cursor:break
     self.assertEqual(req(other,'/v1/conversations?limit=19&cursor='+cursor)[0],400)
    self.assertEqual(len(seen),len(ids));self.assertEqual(set(seen),ids)
    with self.s.transaction():
     for i in range(205):self.s.db.execute('INSERT INTO messages(id,conversation,owner,role,text,created) VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),cid,owner,'assistant','ordered '+str(i),'fixture'))
    all_messages=[];before=None
    while True:
     status,page=req(owner,'/v1/conversations/'+cid+'/messages?limit=37'+('&before='+before if before else ''));self.assertEqual(status,200);all_messages=page['items']+all_messages;before=page['older_cursor']
     if not before:break
    self.assertEqual(len(all_messages),207);self.assertEqual([x['text'] for x in all_messages[2:]],['ordered '+str(i) for i in range(205)]);self.assertEqual(req(owner,'/v1/conversations?owner='+other)[0],400)
  finally:server.shutdown();server.server_close();thread.join()
 def test_v1_database_expands_without_losing_messages_sessions_or_member_ownership(self):
  legacy=Path(self.temp.name)/'legacy';legacy.mkdir();db=sqlite3.connect(legacy/'chat.sqlite3')
  db.executescript("CREATE TABLE conversations(id TEXT PRIMARY KEY,owner TEXT NOT NULL,title TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 0); CREATE TABLE messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,conversation TEXT NOT NULL REFERENCES conversations(id),owner TEXT NOT NULL,role TEXT NOT NULL,text TEXT NOT NULL,created TEXT NOT NULL); CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,owner TEXT NOT NULL,expires REAL NOT NULL,last_seen REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);")
  cid=str(uuid.uuid4());db.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?)',(cid,self.a,'legacy','2026-01-01','2026-01-02',7));db.execute('INSERT INTO messages(id,conversation,owner,role,text,created) VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),cid,self.a,'user','unchanged legacy text','fixture'));token='legacy-token-'+uuid.uuid4().hex;db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),self.a,time.time()+3600,time.time(),0));db.commit();db.close()
  migrated=Service({**self.config,'state_dir':str(legacy)},self.model,self.sender)
  try:
   self.assertEqual(migrated.authenticate(token),self.a);self.assertEqual(migrated.messages(self.a,cid)['items'][0]['text'],'unchanged legacy text');self.assertEqual(migrated.conversations(self.a)['items'][0]['version'],7);self.assertEqual(migrated.conversations(self.b)['items'],[]);self.assertEqual(migrated.one('PRAGMA integrity_check')[0],'ok')
  finally:migrated.close()
 def test_replayed_old_mutation_reports_current_trash_instead_of_resurrecting_metadata(self):
  cid=self.create(self.a)['conversation_id'];body={'title':'renamed','expected_version':0};key=str(uuid.uuid4());self.s.update_conversation(self.a,cid,'rename',body,key);self.mutate(self.a,cid,'trash');replayed=self.s.update_conversation(self.a,cid,'rename',body,key);self.assertIsNotNone(replayed['deleted_at']);self.assertEqual(self.s.conversations(self.a)['items'],[])
if __name__=='__main__':unittest.main()
