import importlib.util,json,pathlib,sys,tempfile,types,unittest,uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from production_backend import Production
from core import Problem
SCRIPT=pathlib.Path(__file__).resolve().parents[2]/'scripts/link-production-result.py'
spec=importlib.util.spec_from_file_location('result_link',SCRIPT);linker=importlib.util.module_from_spec(spec);spec.loader.exec_module(linker)
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name).resolve();self.owner=str(uuid.uuid4());self.other=str(uuid.uuid4())
  self.config={'feishu_profile':'cli_exampleWorkspace','state_dir':str(self.root),'workspace_state_dir':str(self.root),'workspace_links':{'content':'https://TENANT.feishu.cn/base/BASE?table=tblContent123'},'members':[{'handle':'owner','principal_id':self.owner}]};self.calls=[]
  self.files={x:self.root/(x+'.'+('md' if x=='review' else 'mp4')) for x in ('source','main','nobgm','review')}
  for name,p in self.files.items():p.write_bytes(name.encode())
 def fake_run(self,args,**kw):
  self.calls.append(args)
  if args[0].endswith('lark-cli'):out=json.dumps({'ok':True,'data':{'record_id_list':['rec12345678'],'fields':['视频主题','所属账号','视频类型'],'data':[['真实口播',['示例内容账号'],'口播类']]}})
  elif args[0]==linker.FFPROBE:out=json.dumps({'streams':[{'codec_type':'video','width':1080,'height':1920,'avg_frame_rate':'30/1','codec_name':'h264','nb_read_frames':'30'},{'codec_type':'audio','codec_name':'aac'}],'format':{'duration':'1'}})
  else:out='same-picture-md5' if 'framemd5' in args else ''
  return types.SimpleNamespace(returncode=0,stdout=out,stderr='')
 def link(self,**kw):return linker.link(self.config,'rec12345678','真实口播','示例内容账号',**self.files,key='known-result-key',audit=self.root/'audit',run=kw.get('run',self.fake_run))
 def test_persist_reopen_both_variants_provenance_and_member_isolation(self):
  result=self.link();p=Production(self.config);v=p.get(self.owner,result['id']);self.assertEqual(v['sourceKind'],'linked_local_result');self.assertEqual(v['qualityStatus'],'needs_review');self.assertIsNone(v['remotionVersion']);self.assertIsNone(v['finishedAt'])
  self.assertEqual(p.video(self.owner,result['id']).read_bytes(),b'main');self.assertEqual(p.video(self.owner,result['id'],'nobgm').read_bytes(),b'nobgm');self.assertEqual(p.list(self.owner,'rec12345678')['items'][0]['id'],result['id']);self.assertEqual(p.list(self.other,'rec12345678')['items'],[])
  for action in (lambda:p.get(self.other,result['id']),lambda:p.video(self.other,result['id'])):
   with self.assertRaises(Problem) as e:action()
   self.assertEqual(e.exception.status,404)
  self.assertNotIn('provenance',v);self.assertNotIn('owner',v)
 def test_retry_no_copy_or_render_and_changed_payload_conflicts(self):
  a=self.link();count=len(self.calls);b=self.link();self.assertEqual(a['id'],b['id']);self.assertTrue(b['reused']);self.assertEqual(len(self.calls),count+1)
  self.files['main'].write_bytes(b'changed')
  with self.assertRaisesRegex(RuntimeError,'idempotency_conflict'):self.link()
 def test_register_does_not_instantiate_restart_recovery_or_mutate_active_job(self):
  d=self.root/'production-runs'/str(uuid.uuid4());d.mkdir(parents=True);f=d/'status.json';f.write_text(json.dumps({'id':d.name,'state':'planning'}));before=f.read_bytes();self.link();self.assertEqual(f.read_bytes(),before)
 def test_fixed_cli_user_profile_and_no_business_write(self):
  self.link();args=self.calls[0];self.assertEqual(args[0],'lark-cli');self.assertIn('+record-get',args);self.assertEqual(args[args.index('--as')+1],'user');self.assertEqual(args[args.index('--profile')+1],'cli_exampleWorkspace');self.assertFalse(any('render' in x or '+record-update' in x for command in self.calls for x in command))
 def test_reject_wrong_business_record_without_visible_result(self):
  def wrong(args,**kw):return types.SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'data':{'record_id_list':['recOTHER123'],'fields':[],'data':[[]]}}),stderr='')
  with self.assertRaises(Problem):self.link(run=wrong)
  self.assertFalse(list(self.root.glob('production-runs/*/status.json')))
 def test_failed_decode_never_visible_success_and_originals_unchanged(self):
  before={k:p.read_bytes() for k,p in self.files.items()}
  def broken(args,**kw):
   if '-xerror' in args:return types.SimpleNamespace(returncode=1,stdout='',stderr='decode_failed')
   return self.fake_run(args,**kw)
  with self.assertRaisesRegex(RuntimeError,'media_verification_failed'):self.link(run=broken)
  self.assertFalse(list(self.root.glob('production-runs/*/status.json')));self.assertEqual(before,{k:p.read_bytes() for k,p in self.files.items()})
 def test_different_variant_pictures_never_visible(self):
  def mismatch(args,**kw):
   p=self.fake_run(args,**kw)
   if 'framemd5' in args and 'output-nobgm.mp4' in ' '.join(args):p.stdout='different-picture'
   return p
  with self.assertRaisesRegex(RuntimeError,'variant_picture_mismatch'):self.link(run=mismatch)
  self.assertFalse(list(self.root.glob('production-runs/*/status.json')))
 def test_symlink_or_changed_registered_artifact_rejected(self):
  original=self.files['main'];self.files['main']=self.root/'alias.mp4';self.files['main'].symlink_to(original)
  with self.assertRaisesRegex(RuntimeError,'invalid_local_result_file'):self.link()
  self.files['main']=original;r=self.link();(self.root/'production-runs'/r['id']/'output.mp4').write_bytes(b'tampered')
  with self.assertRaisesRegex(RuntimeError,'linked_artifact_changed'):self.link()
if __name__=='__main__':unittest.main()
