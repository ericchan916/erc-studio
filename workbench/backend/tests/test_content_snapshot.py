# -*- coding: utf-8 -*-
import json,pathlib,subprocess,sys,tempfile,threading,time,unittest
from types import SimpleNamespace
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from content_snapshot import ContentRefresher,ContentRefreshError
from server import readonly_content

def snapshot():return {'phase':'ok','stale':False,'refreshing':False,'readAt':'2026-10-06T00:00:00Z','topics':[],'projects':[{'id':'recFixture1234','title':'新视频'}],'jobs':[],'forms':[],'stages':[],'accounts':[],'platforms':{},'actor':{'name':'原身份','canWrite':True,'canApprove':True}}

class Tests(unittest.TestCase):
 def config(self,root):return {'workspace_state_dir':str(root)}
 def test_live_snapshot_is_forced_readonly_without_becoming_stale(self):
  value=readonly_content(snapshot(),live=True);self.assertEqual(value['phase'],'ok');self.assertFalse(value['stale']);self.assertFalse(value['actor']['canWrite']);self.assertEqual(value['message'],'已实时读取飞书原表')
 def test_cached_snapshot_remains_explicitly_stale(self):
  value=readonly_content(snapshot(),live=False);self.assertEqual(value['phase'],'cached');self.assertTrue(value['stale'])
 def test_refresh_contract_invokes_fixed_node_script_and_validates_payload(self):
  with tempfile.TemporaryDirectory() as t:
   root=pathlib.Path(t);script=root/'content_refresh.cjs';script.write_text('fixture');calls=[]
   def run(argv,**kwargs):calls.append((argv,kwargs));return SimpleNamespace(returncode=0,stdout=json.dumps(snapshot()),stderr='')
   value=ContentRefresher(self.config(root),run=run,script=script).refresh();self.assertEqual(value['projects'][0]['title'],'新视频');self.assertEqual(calls[0][0],[ '/usr/local/bin/node',str(script.resolve()),str(root.resolve())]);self.assertEqual(calls[0][1]['stdin'],subprocess.DEVNULL);self.assertNotIn('SHELL',calls[0][1]['env'])
 def test_concurrent_refresh_is_deduplicated(self):
  with tempfile.TemporaryDirectory() as t:
   root=pathlib.Path(t);script=root/'content_refresh.cjs';script.write_text('fixture');release=threading.Event();calls=[]
   def run(*args,**kwargs):calls.append(1);release.wait(2);return SimpleNamespace(returncode=0,stdout=json.dumps(snapshot()),stderr='')
   refresh=ContentRefresher(self.config(root),run=run,script=script);values=[]
   threads=[threading.Thread(target=lambda:values.append(refresh.refresh())) for _ in range(2)]
   for thread in threads:thread.start()
   for _ in range(50):
    if calls:break
    time.sleep(.01)
   release.set()
   for thread in threads:thread.join(2)
   self.assertEqual(len(calls),1);self.assertEqual(len(values),2)
 def test_failure_and_invalid_shape_do_not_claim_live_success(self):
  with tempfile.TemporaryDirectory() as t:
   root=pathlib.Path(t);script=root/'content_refresh.cjs';script.write_text('fixture')
   for response in [SimpleNamespace(returncode=1,stdout='',stderr='{"ok":false,"error":"content_authorization_required"}'),SimpleNamespace(returncode=0,stdout='{}',stderr='')]:
    with self.assertRaises(ContentRefreshError):ContentRefresher(self.config(root),run=lambda *a,**k:response,script=script).refresh()

if __name__=='__main__':unittest.main()
