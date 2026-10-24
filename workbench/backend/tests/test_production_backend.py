import hashlib,json,pathlib,sys,tempfile,unittest,uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from production_backend import Production,CHUNK
from core import Problem
ROOT=pathlib.Path(__file__).resolve().parents[2]
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.owner=str(uuid.uuid4());self.other=str(uuid.uuid4());self.config={'production_accounts':['示例内容账号'],'state_dir':self.tmp.name,'workspace_state_dir':self.tmp.name,'workspace_links':{'content':'https://TENANT.feishu.cn/base/BASE?table=tblContent123'},'members':[{'handle':'owner','principal_id':self.owner},{'handle':'other','principal_id':self.other}]}
  self.p=Production(self.config,template=ROOT/'production-remotion');self.p._project=lambda *_:None
  self.data={'projectId':'rec12345678','title':'一条口播','account':'示例内容账号','requirement':'保留观点','size':6,'fileName':'source.mp4'}
 def test_same_key_reuses_job_and_changed_payload_conflicts(self):
  a=self.p.start(self.owner,self.data,'valid-key-123');self.assertEqual(a,self.p.start(self.owner,self.data,'valid-key-123'))
  with self.assertRaises(Problem) as e:self.p.start(self.owner,{**self.data,'requirement':'另一需求'},'valid-key-123')
  self.assertEqual(e.exception.code,'idempotency_conflict')
 def test_cross_member_never_sees_or_uploads(self):
  a=self.p.start(self.owner,self.data,'valid-key-123');self.assertEqual(self.p.list(self.other,self.data['projectId'])['items'],[])
  for op in [lambda:self.p.get(self.other,a['id']),lambda:self.p.upload(self.other,a['id'],0,b'123',hashlib.sha256(b'123').hexdigest()),lambda:self.p.submit(self.other,a['id']),lambda:self.p.video(self.other,a['id'])]:
   with self.assertRaises(Problem) as e:op()
   self.assertEqual(e.exception.status,404)
 def test_secondary_member_cannot_start_owner_job(self):
  with self.assertRaises(Problem) as e:self.p.start(self.other,self.data,'valid-key-123')
  self.assertEqual(e.exception.status,403)
 def test_offset_checksum_replay_and_no_duplicate_bytes(self):
  a=self.p.start(self.owner,self.data,'valid-key-123');data=b'123';digest=hashlib.sha256(data).hexdigest()
  first=self.p.upload(self.owner,a['id'],0,data,digest);self.assertEqual(first['received'],3);self.assertEqual(first['sourceFirstChunkSha256'],digest)
  first=self.p.upload(self.owner,a['id'],0,data,digest);self.assertEqual(first['received'],3);self.assertEqual(first['sourceFirstChunkSha256'],digest)
  with self.assertRaises(Problem):self.p.upload(self.owner,a['id'],0,b'456',hashlib.sha256(b'456').hexdigest())
  self.assertEqual(self.p.upload(self.owner,a['id'],3,b'456',hashlib.sha256(b'456').hexdigest())['received'],6)
  self.assertEqual((self.p._dir(a['id'])/'source.upload').read_bytes(),b'123456')
 def test_incomplete_upload_never_starts(self):
  a=self.p.start(self.owner,self.data,'valid-key-123')
  with self.assertRaises(Problem):self.p.submit(self.owner,a['id'])
  self.assertEqual(self.p.get(self.owner,a['id'])['state'],'uploading')
 def test_overlarge_or_unauthenticated_bytes_rejected(self):
  a=self.p.start(self.owner,self.data,'valid-key-123')
  for chunk,checksum in [(b'x'*(CHUNK+1),hashlib.sha256(b'x'*(CHUNK+1)).hexdigest()),(b'abc','0'*64)]:
   with self.assertRaises(Problem):self.p.upload(self.owner,a['id'],0,chunk,checksum)
  self.assertEqual(self.p.get(self.owner,a['id'])['received'],0)
 def test_non_talking_head_or_unsupported_type_rejected(self):
  for variant in [{**self.data,'account':'示例账号'},{**self.data,'fileName':'source.html'},{**self.data,'size':0},{**self.data,'requirement':'x'*4001}]:
   with self.assertRaises(Problem):self.p.start(self.owner,variant,str(uuid.uuid4()))
 def test_restart_marks_running_unknown_without_repeating(self):
  a=self.p.start(self.owner,self.data,'valid-key-123');v=self.p._read(a['id']);self.p._update(v,state='planning')
  later=Production(self.config,template=ROOT/'production-remotion');self.assertEqual(later.get(self.owner,a['id'])['state'],'execution_unknown')
  self.assertEqual(later.start(self.owner,self.data,'valid-key-123')['id'],a['id'])
 def test_video_requires_success_not_just_file_existence(self):
  a=self.p.start(self.owner,self.data,'valid-key-123');(self.p._dir(a['id'])/'output.mp4').write_bytes(b'synthetic')
  with self.assertRaises(Problem):self.p.video(self.owner,a['id'])
  v=self.p._read(a['id']);self.p._update(v,state='succeeded');self.assertEqual(self.p.video(self.owner,a['id']).read_bytes(),b'synthetic')
 def test_codex_quota_error_remains_distinct_from_render_or_auth(self):
  class Result:returncode=1;stdout='';stderr='insufficient_quota'
  self.p.run=lambda *args,**kwargs:Result()
  from production_backend import CLI
  with self.assertRaisesRegex(RuntimeError,'codex_quota_or_balance_insufficient'):self.p._run([CLI,'exec'],self.p.root,2,input='fixture')
 def test_engine_status_and_job_receipt_are_real_backend_fields(self):
  a=self.p.start(self.owner,self.data,'valid-key-123');listing=self.p.list(self.owner,self.data['projectId'])
  self.assertIn('engine',listing);self.assertIn('checkedAt',listing['engine']);self.assertEqual(listing['items'][0]['state'],'uploading')
  self.assertIsNone(listing['items'][0]['remotionVersion'])
  v=self.p._read(a['id']);self.p._update(v,state='rendering',renderStartedAt=123.0)
  in_progress=self.p.get(self.owner,a['id']);self.assertEqual(in_progress['renderStartedAt'],123.0);self.assertIsNone(in_progress['outputBytes'])
  self.p._update(v,state='succeeded',outputBytes=2048,runtime={'remotionVersion':'4.0.531','codex':'PRIVATE_PATH'})
  receipt=self.p.get(self.owner,a['id']);self.assertEqual(receipt['remotionVersion'],'4.0.531');self.assertEqual(receipt['outputBytes'],2048);self.assertNotIn('runtime',receipt)
if __name__=='__main__':unittest.main()

class ConfirmationTests(unittest.TestCase):
 setUp=Tests.setUp
 def test_human_confirmation_requires_owner_success_and_unchanged_artifact(self):
  a=self.p.start(self.owner,self.data,'confirm-key-123');id=a['id']
  with self.assertRaises(Problem):self.p.confirm(self.owner,id)
  f=self.p._dir(id)/'output.mp4';f.write_bytes(b'verified-output');v=self.p._read(id);self.p._update(v,state='succeeded',outputSha256=hashlib.sha256(f.read_bytes()).hexdigest(),qualityStatus='needs_review')
  with self.assertRaises(Problem):self.p.confirm(self.other,id)
  first=self.p.confirm(self.owner,id);self.assertEqual(first['qualityStatus'],'approved');self.assertEqual(first['reviewedAt'],self.p.confirm(self.owner,id)['reviewedAt'])
  f.write_bytes(b'changed-output')
  with self.assertRaises(Problem):self.p.confirm(self.owner,id)
 def test_nobgm_is_not_exposed_from_file_existence_or_wrong_owner(self):
  a=self.p.start(self.owner,self.data,'variant-key-123');id=a['id'];f=self.p._dir(id)/'output-nobgm.mp4';f.write_bytes(b'verified')
  v=self.p._read(id);self.p._update(v,state='succeeded')
  with self.assertRaises(Problem):self.p.video(self.owner,id,'nobgm')
  self.p._update(v,variants=['main','nobgm']);self.assertEqual(self.p.video(self.owner,id,'nobgm'),f)
  with self.assertRaises(Problem):self.p.video(self.other,id,'nobgm')

class UnknownGuardTests(unittest.TestCase):
 setUp=Tests.setUp
 def test_unknown_cannot_be_bypassed_with_new_key(self):
  a=self.p.start(self.owner,self.data,'unknown-key-123');v=self.p._read(a['id']);self.p._update(v,state='execution_unknown')
  with self.assertRaises(Problem) as error:self.p.start(self.owner,self.data,'unknown-key-456')
  self.assertEqual(error.exception.code,'production_result_unknown');self.assertEqual(len(list(self.p.root.glob('*/status.json'))),1)
