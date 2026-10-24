import copy, hashlib, json, pathlib, sys, tempfile, time, unittest, uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from publishing import Publishing, CLIError, digest, redact
from core import Problem

OWNER='00000000-0000-4000-8000-000000000001'
IMAGE=b'\x89PNG\r\n\x1a\n'+b'0'*24
VIDEO=b'\x00\x00\x00\x18ftypmp42'+b'0'*20

class FakeCLI:
    def __init__(self):self.calls=[];self.online=True;self.fail=None;self.meta=True
    def __call__(self,args,timeout=45):
        self.calls.append(list(args))
        if self.fail and args[0]==self.fail[0] and ('--dry-run' not in args or self.fail[0]!='publish'):raise self.fail[1]
        if args[0]=='doctor':return {'publishChannelReadiness':{'cloud':{'configured':True},'local':{'configured':True}}}
        if args[:2]==['accounts','list']:return [{'id':p,'platformName':n,'platformAccountName':n+'测试账号','status':1 if self.online else 2} for p,n in [('a','小红书'),('d','抖音'),('b','哔哩哔哩')]]
        if args[0]=='prepare':return {'form':{},'categories':None}
        if args[:2]==['schema','get']:
            f={'formType':{'const':'task','required':True},'title':{'type':'string','maxLength':20},'description':{'type':'string','minLength':1,'maxLength':1000,'required':True},'visibleType':{'type':'number','default':0,'enum':[0,1,2],'required':True}}
            if args[3]=='imageText':f['images']={'type':'array','maxItems':18,'required':True}
            if args[2]=='抖音':f['music']={'type':'object'}
            if args[2]=='哔哩哔哩':f.update(tags={'type':'array','required':True},category={'type':'array','required':True},createType={'type':'number','default':1,'enum':[1,2],'required':True})
            return {'businessFields':f}
        if args[0]=='query' and args[1]in ('music','categories'):return {'dataList':[{'yixiaoerId':'query-1','yixiaoerName':'真实查询选项','raw':{'id':42}}]}
        if args[0]=='upload':
            value={'key':'vendor/'+pathlib.Path(args[1]).name,'width':720,'height':1280,'size':32,'format':'png'}
            if args[args.index('--usage')+1]=='video':value.update(duration=1,format='mp4')
            if not self.meta:value.pop('width')
            return value
        if args[0]=='validate':return {'prepared':True}
        if args[0]=='publish' and '--dry-run'in args:return {'meta':{'effectivePublishChannel':args[args.index('--publish-channel')+1],'requestHash':'vendor-hash'}}
        if args[0]=='publish':return {'taskSetId':'task-1'}
        if args[:2]==['query','details']:return {'taskSetStatus':'pending','apiKey':'YOUR_TEST_SECRET','tasks':[{'status':'waiting'}]}
        raise AssertionError(args)

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.config={'state_dir':self.tmp.name,'members':[{'principal_id':OWNER,'handle':'owner'},{'principal_id':'other','handle':'member'}]};self.cli=FakeCLI();self.p=Publishing(self.config,self.cli)
    def body(self,kind='imageText',account='a',fields=None):
        fs=[{'name':'photo.png','size':len(IMAGE),'role':'image'}] if kind=='imageText'else[{'name':'video.mp4','size':len(VIDEO),'role':'video'},{'name':'cover.png','size':len(IMAGE),'role':'cover'}]
        return {'kind':kind,'channel':'cloud','title':'测试标题','description':'保持完整原文。','targets':[{'accountId':account,'fields':fields or {}}],'files':fs}
    def created(self,kind='imageText',account='a',fields=None):return self.p.create(OWNER,self.body(kind,account,fields),'unique-key-1')
    def loaded(self,kind='imageText'):
        j=self.created(kind)
        for f in j['files']:
            data=VIDEO if f['role']=='video'else IMAGE;self.p.upload(OWNER,j['id'],f['id'],0,data,hashlib.sha256(data).hexdigest())
        return j
    def settled(self,id):
        until=time.monotonic()+3
        while time.monotonic()<until:
            j=self.p.get(OWNER,id)
            if j['state'] not in ('preparing','submitting'):return j
            time.sleep(.01)
        self.fail('worker did not settle')
    def ready(self,kind='imageText'):
        j=self.loaded(kind);self.p.prepare(OWNER,j['id']);j=self.settled(j['id']);self.assertEqual(j['state'],'ready',j);return j
    def assert_code(self,code,fn):
        with self.assertRaises(Problem) as e:fn()
        self.assertEqual(e.exception.code,code)
    def test_catalog_probes_imageText_contract_despite_catalog_omission(self):
        c=self.p.catalog(OWNER);self.assertEqual(c['accounts'][0]['types'],['imageText','video']);self.assertNotIn('clientId',str(c))
    def test_imageText_preparation_maps_real_uploaded_resources_and_preserves_body(self):
        j=self.ready();t=self.p.own(OWNER,j['id'])['targets'][0];payload=json.loads((self.p.root/j['id']/'a-payload.json').read_text());af=payload['publishArgs']['accountForms'][0]
        self.assertEqual(af['images'],af['contentPublishForm']['images']);self.assertEqual(af['coverKey'],af['images'][0]['key']);self.assertEqual(af['contentPublishForm']['description'],'保持完整原文。');self.assertEqual(t['requestHash'],'vendor-hash');self.assertFalse(any(c[0]=='publish'and'--dry-run'not in c for c in self.cli.calls))
    def test_video_uses_video_duration_and_separately_uploaded_cover(self):
        j=self.ready('video');af=json.loads((self.p.root/j['id']/'a-payload.json').read_text())['publishArgs']['accountForms'][0]
        self.assertEqual(af['video']['duration'],1);self.assertNotEqual(af['coverKey'],af['video']['key']);self.assertEqual(len([c for c in self.cli.calls if c[0]=='upload']),2)
    def test_same_key_returns_original_without_duplicate_jobs(self):
        j=self.created();self.assertEqual(self.created()['id'],j['id']);self.assertEqual(len(list(self.p.root.glob('*/status.json'))),1)
        b=self.body();b['title']='修改';self.assert_code('idempotency_conflict',lambda:self.p.create(OWNER,b,'unique-key-1'))
    def test_submit_requires_explicit_approval_and_identical_digest(self):
        j=self.ready();self.assert_code('publish_confirmation_required',lambda:self.p.submit(OWNER,j['id'],{'confirmation':j['confirmation'],'approved':False}));self.assert_code('publish_confirmation_changed',lambda:self.p.submit(OWNER,j['id'],{'confirmation':'wrong','approved':True}))
    def test_repeated_submit_runs_cli_publish_once_and_reports_not_public_visible(self):
        j=self.ready();body={'confirmation':j['confirmation'],'approved':True};self.p.submit(OWNER,j['id'],body);self.p.submit(OWNER,j['id'],body);j=self.settled(j['id']);self.assertEqual(j['state'],'submitted');self.assertEqual(j['targets'][0]['taskSetId'],'task-1');self.assertIsNone(j['targets'][0]['receipt']['publicVisible']);self.assertEqual(len([c for c in self.cli.calls if c[0]=='publish'and'--dry-run'not in c]),1)
        j=self.p.verify(OWNER,j['id']);self.assertNotIn('apiKey',str(j));self.assertIsNone(j['targets'][0]['verification']['publicVisible'])
    def test_member_cannot_use_owner_vendor_credentials(self):
        self.assert_code('publish_permission_denied',lambda:self.p.catalog('other'))
        self.p.allowed.add('other');j=self.created();self.assert_code('not_found',lambda:self.p.get('other',j['id']));self.assertEqual(self.p.listing('other')['items'],[])
    def test_account_expiry_is_rechecked_before_submit(self):
        j=self.ready();self.cli.online=False;self.assert_code('publish_account_unavailable',lambda:self.p.submit(OWNER,j['id'],{'confirmation':j['confirmation'],'approved':True}))
    def test_douyin_music_must_come_from_same_owner_and_account_query(self):
        self.assert_code('publish_music_required',lambda:self.created(account='d'))
        opts=self.p.field_options(OWNER,{'accountId':'d','kind':'imageText','field':'music','query':'音乐'});key=opts['options'][0]['id'];j=self.created(account='d',fields={'music':key});self.assertEqual(self.p.own(OWNER,j['id'])['targets'][0]['form']['music']['raw'],{'id':42})
    def test_bilibili_required_tags_and_category(self):
        self.assert_code('publish_required_field:tags',lambda:self.created('video','b'))
        opts=self.p.field_options(OWNER,{'accountId':'b','kind':'video','field':'category'});j=self.created('video','b',{'tags':['标签'],'category':opts['options'][0]['id']});self.assertEqual(j['kind'],'video')
    def test_resources_and_commands_are_not_client_supplied(self):
        b=self.body();b['targets'][0]['fields']={'images':[{'key':'fake'}]};self.assert_code('invalid_request',lambda:self.p.create(OWNER,b,'unique-key'))
        b=self.body();b['files'][0]['name']='../photo.png';self.assert_code('invalid_request',lambda:self.p.create(OWNER,b,'unique-key'))
    def test_video_without_cover_and_oversize_input_rejected(self):
        b=self.body('video');b['files'].pop();self.assert_code('publish_video_cover_required',lambda:self.p.create(OWNER,b,'unique-key'))
        b=self.body();b['files'][0]['size']=20*1024**2+1;self.assert_code('invalid_request',lambda:self.p.create(OWNER,b,'unique-key'))
    def test_chunk_replay_compares_actual_bytes(self):
        j=self.created();f=j['files'][0];h=hashlib.sha256(IMAGE).hexdigest();self.p.upload(OWNER,j['id'],f['id'],0,IMAGE,h);self.p.upload(OWNER,j['id'],f['id'],0,IMAGE,h)
        other=b'1'*len(IMAGE);self.assert_code('publish_upload_conflict',lambda:self.p.upload(OWNER,j['id'],f['id'],0,other,hashlib.sha256(other).hexdigest()))
    def test_partial_upload_never_prepares(self):
        j=self.created();self.assert_code('publish_stage_conflict',lambda:self.p.prepare(OWNER,j['id']))
    def test_resume_after_fsync_crash_drops_only_uncommitted_tail(self):
        j=self.created();f=j['files'][0];p=self.p.root/j['id']/(f['id']+'.png');p.write_bytes(IMAGE)
        j=self.p.upload(OWNER,j['id'],f['id'],0,IMAGE,hashlib.sha256(IMAGE).hexdigest())
        self.assertEqual(p.read_bytes(),IMAGE);self.assertEqual(j['files'][0]['sha256'],hashlib.sha256(IMAGE).hexdigest())
    def test_missing_committed_bytes_stop_resume(self):
        j=self.loaded();f=j['files'][0];p=self.p.root/j['id']/(f['id']+'.png');p.write_bytes(b'broken')
        self.assert_code('publish_upload_conflict',lambda:self.p.upload(OWNER,j['id'],f['id'],0,IMAGE,hashlib.sha256(IMAGE).hexdigest()))
    def test_upload_metadata_failure_blocks_publish(self):
        self.cli.meta=False;j=self.loaded();self.p.prepare(OWNER,j['id']);j=self.settled(j['id']);self.assertEqual(j['state'],'failed');self.assertFalse(any(c[0]=='publish' for c in self.cli.calls))
    def test_validation_error_keeps_literal_cli_reason_and_blocks_publish(self):
        self.cli.fail=('validate',CLIError('validate','bad','正文过长'));j=self.loaded();self.p.prepare(OWNER,j['id']);j=self.settled(j['id']);self.assertEqual(j['error'],'正文过长');self.assertFalse(any(c[0]=='publish'for c in self.cli.calls))
    def test_payload_tamper_invalidates_confirmation(self):
        j=self.ready();path=self.p.root/j['id']/'a-payload.json';path.write_text('{}');self.assert_code('publish_confirmation_changed',lambda:self.p.submit(OWNER,j['id'],{'confirmation':j['confirmation'],'approved':True}))
    def test_timeout_is_unknown_and_does_not_retry(self):
        j=self.ready();self.cli.fail=('publish',CLIError('publish','timeout','连接中断',True));body={'confirmation':j['confirmation'],'approved':True};self.p.submit(OWNER,j['id'],body);j=self.settled(j['id']);self.assertEqual(j['state'],'execution_unknown');self.p.submit(OWNER,j['id'],body);self.assertEqual(len([c for c in self.cli.calls if c[0]=='publish'and'--dry-run'not in c]),1)
    def test_restart_preserves_accepted_receipt_and_marks_interrupted_submission_unknown(self):
        j=self.created();v=self.p.own(OWNER,j['id']);v['state']='submitting';v['targets'][0]['state']='submitting';self.p.write(v);p=Publishing(self.config,self.cli);self.assertEqual(p.get(OWNER,j['id'])['state'],'execution_unknown');self.assertFalse(any(c[0]=='publish'for c in self.cli.calls))
    def test_cli_failure_sanitizer_filters_sensitive_fields(self):self.assertEqual(redact({'Cookie':'x','data':[{'token':'t','status':'ok'}]}),{'data':[{'status':'ok'}]})
    def test_cancel_retains_media_and_never_cancels_accepted_or_unknown_publish(self):
        j=self.loaded();j=self.p.cancel(OWNER,j['id']);self.assertEqual(j['state'],'cancelled');self.assertTrue((self.p.root/j['id']/'status.json').is_file());self.assert_code('publish_stage_conflict',lambda:self.p.prepare(OWNER,j['id']))
    def test_expired_confirmation_and_same_local_channel(self):
        b=self.body();b['channel']='local';j=self.p.create(OWNER,b,'local-key-1');f=j['files'][0];self.p.upload(OWNER,j['id'],f['id'],0,IMAGE,hashlib.sha256(IMAGE).hexdigest());self.p.prepare(OWNER,j['id']);j=self.settled(j['id']);self.assertEqual(j['state'],'ready');self.assertTrue(all(c[c.index('--publish-channel')+1]=='local'for c in self.cli.calls if '--publish-channel'in c));v=self.p.own(OWNER,j['id']);v['preparedAt']=0;self.p.write(v);self.assert_code('publish_preflight_expired',lambda:self.p.submit(OWNER,j['id'],{'approved':True,'confirmation':j['confirmation']}))

if __name__=='__main__':unittest.main()
