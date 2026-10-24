"""Two independent authorization controls. Fixed official CLI only; no Cookie/Profile transfer."""
import datetime,hashlib,json,os,re,subprocess,threading,time,uuid
from pathlib import Path
from core import Problem,shape
from scientist_metrics import registry_cli,exact_map
from account_metrics import run_cli,PLATFORMS
CLI='data-scientist'
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def science_command(args):
 try:
  r=subprocess.run([CLI,*args],capture_output=True,timeout=25,env={**os.environ,'PATH':'/opt/homebrew/bin:/usr/bin:/bin'})
  if len(r.stdout)>4*1024*1024:raise ValueError()
  return r.returncode,json.loads(r.stdout)
 except subprocess.TimeoutExpired:raise Problem(504,'authorization_execution_unknown')
 except Exception:raise Problem(502,'scientist_response_invalid')
class AccountAuthorizations:
 def __init__(self,config,read_science=registry_cli,read_yxer=None,run=science_command,read_local=None,clock=time.time):
  self.config=config;self.read_science=read_science;self.read_yxer=read_yxer or(lambda:run_cli(['accounts','list','--all']));self.run=run;self.clock=clock;self.lock=threading.RLock()
  self.allowed={m['principal_id']for m in config.get('members',[])if m.get('handle')=='owner'}
  self.read_local=read_local or self.local
  self.root=Path(config['state_dir'])/'account-authorization-jobs';self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
 def permit(self,owner):
  if owner not in self.allowed:raise Problem(403,'account_authorization_permission_denied')
 def local(self):
  p=Path(self.config.get('workspace_state_dir',''))
  if p.resolve()!=Path.home()/'Library/Application Support/ERC Studio':raise Problem(503,'account_workspace_invalid')
  return json.loads((p/'accounts.json').read_text())['accounts']
 def binding(self,id):
  rows=[a for a in self.read_local()if a.get('id')==id]
  if len(rows)!=1:raise Problem(404,'account_not_found')
  local=rows[0]
  try:core=exact_map(self.read_science()).get((local['platform'],local.get('sourceKey')))
  except Exception:raise Problem(502,'scientist_registry_unavailable')
  if not core:raise Problem(409,'scientist_binding_missing')
  if not core.get('enabled'):raise Problem(409,'scientist_account_disabled')
  if not re.fullmatch(r'ac_[a-f0-9]{32}',core['id']):raise Problem(502,'scientist_binding_invalid')
  return local,core
 def catalog(self,owner):
  self.permit(owner);local=self.read_local();remote={};science={};errors=[]
  try:
   for r in self.read_yxer():
    k=(PLATFORMS.get(r.get('platformName')),hashlib.sha256(r['id'].encode()).hexdigest())
    if k in remote:raise ValueError()
    remote[k]=r
  except Exception:remote={};errors.append('publish_status_unavailable')
  try:science=exact_map(self.read_science())
  except Exception:errors.append('scientist_registry_unavailable')
  items=[]
  for a in local:
   key=(a.get('platform'),a.get('sourceKey'));r=remote.get(key);c=science.get(key);auth=(c or{}).get('auth')or{};identity=(c or{}).get('identity')or{}
   valid_until=auth.get('next_check_at');fresh=False
   try:fresh=datetime.datetime.fromisoformat(valid_until).timestamp()>self.clock()
   except Exception:pass
   collect='unknown'if 'scientist_registry_unavailable'in errors else'disabled'if c and not c.get('enabled')else'unbound'if not c else'expired'if auth.get('status')in('expired','login_required','unauthorized')else'healthy'if fresh and auth.get('status')=='healthy'and identity.get('status')=='matched'else'unknown'
   items.append({'accountId':a['id'],'platform':a['platform'],'name':a['name'],
    'publish':{'state':'healthy'if r and r.get('status')==1 else'expired'if r and r.get('status')in(0,2,3,4)else'unknown','rawStatus':r.get('status')if r else None,'sourceId':r.get('id')if r else None,'canOpen':bool(r),'url':'https://www.yixiaoer.cn/web/publish?router=account'},
    'collect':{'state':collect,'canLogin':bool(c and c.get('enabled')),'scienceId':c.get('id')if c else None,'checkedAt':auth.get('checked_at'),'identity':identity.get('status')or'unknown'}})
  operations=[]
  for p in self.root.glob('*.json'):
   try:operations.append(json.loads(p.read_text()))
   except Exception:pass
  for item in items:
   current=[v for v in operations if v.get('owner')==owner and v.get('accountId')==item['accountId'] and v.get('scienceId')==item['collect']['scienceId'] and v.get('state')in('submitting','accepted','running','execution_unknown','verification_required')]
   if current:item['collect']['operation']=self.dto(current[-1])
  return {'items':items,'checkedAt':stamp(),'errors':errors}
 def path(self,id):
  if not isinstance(id,str)or not re.fullmatch(r'[a-f0-9-]{36}',id):raise Problem(404,'not_found')
  return self.root/(id+'.json')
 def save(self,v):
  p=self.path(v['id']);t=p.with_suffix('.tmp');t.write_text(json.dumps(v));t.chmod(0o600)
  with t.open('rb')as f:os.fsync(f.fileno())
  t.replace(p)
 def own(self,owner,id):
  self.permit(owner)
  try:v=json.loads(self.path(id).read_text())
  except Problem:raise
  except Exception:raise Problem(404,'not_found')
  if v['owner']!=owner:raise Problem(404,'not_found')
  return v
 def dto(self,v):return{k:v.get(k)for k in ('id','accountId','name','platform','state','confirmation','expiresAt','jobId','error','checkedAt')}
 def preview(self,owner,body,key):
  self.permit(owner);shape(body,['accountId']);id=body['accountId']
  if not isinstance(key,str)or not re.fullmatch(r'[a-zA-Z0-9_-]{8,96}',key):raise Problem(400,'idempotency_key_required')
  with self.lock:
   for p in self.root.glob('*.json'):
    old=json.loads(p.read_text())
    if old['owner']==owner and old['key']==key:
     if old['accountId']!=id:raise Problem(409,'idempotency_conflict')
     return self.dto(old)
   a,c=self.binding(id);request_id=str(uuid.uuid4());code,packet=self.run(['accounts','login',c['id'],'--dry-run','--key','studio-auth-'+request_id])
   if code or packet.get('dry_run')is not True or packet.get('request',{}).get('account_ids')!=[c['id']]:raise Problem(502,'authorization_preview_failed')
   for p in self.root.glob('*.json'):
    prior=json.loads(p.read_text())
    if prior['scienceId']==c['id']and prior['state']in('submitting','accepted','running','execution_unknown'):raise Problem(409,'authorization_existing_job')
   v={'id':request_id,'owner':owner,'key':key,'accountId':id,'scienceId':c['id'],'scienceVersion':c.get('version'),'platformUid':c.get('platform_account_id'),'sourceKey':a.get('sourceKey'),'name':a['name'],'platform':a['platform'],'state':'preview','createdAt':self.clock(),'expiresAt':self.clock()+600,'jobId':None,'error':None}
   v['confirmation']=digest({k:v[k]for k in('id','owner','accountId','scienceId','scienceVersion','platformUid','sourceKey')});self.save(v);return self.dto(v)
 def submit(self,owner,id,body):
  self.permit(owner);shape(body,['approved','confirmation'])
  with self.lock:
   v=self.own(owner,id)
   if body['approved']is not True or body['confirmation']!=v['confirmation']:raise Problem(400,'authorization_confirmation_required')
   if v['state']!='preview':return self.dto(v)
   if v['expiresAt']<self.clock():raise Problem(409,'authorization_preview_expired')
   a,c=self.binding(v['accountId'])
   if(c['id'],c.get('version'),c.get('platform_account_id'),a.get('sourceKey'))!=(v['scienceId'],v['scienceVersion'],v['platformUid'],v['sourceKey']):raise Problem(409,'authorization_binding_changed')
   v['state']='submitting';self.save(v)
   try:code,packet=self.run(['accounts','login',v['scienceId'],'--confirm','--key','studio-auth-'+v['id']])
   except Problem:v['state']='execution_unknown';v['error']='authorization_execution_unknown';self.save(v);return self.dto(v)
   job=packet.get('job')or{};jid=packet.get('job_id')or job.get('id')
   if code or packet.get('http_status')not in(200,202):v.update(state='failed',error='scientist_login_not_accepted')
   elif not isinstance(jid,str)or not re.fullmatch(r'[a-zA-Z0-9_-]{1,96}',jid):v.update(state='execution_unknown',error='authorization_execution_unknown')
   else:v.update(state='accepted',jobId=jid,error=None)
   self.save(v);return self.dto(v)
 def status(self,owner,id):
  with self.lock:
   v=self.own(owner,id)
   if v['jobId']and v['state']in('accepted','running','execution_unknown'):
    code,packet=self.run(['accounts','jobs','show',v['jobId']]);job=packet.get('job')or{}
    if not code and job.get('account_ids')in(None,[v['scienceId']]):
     state=job.get('state');v['state']='running'if state in('queued','running')else'failed'if state in('failed','cancelled')else'execution_unknown'
     if state=='completed':
      _,c=self.binding(v['accountId']);auth=c.get('auth')or{};ident=c.get('identity')or{}
      fresh=False
      try:fresh=datetime.datetime.fromisoformat(auth.get('checked_at','')).timestamp()>=v.get('createdAt',v['expiresAt']-600) and datetime.datetime.fromisoformat(auth.get('next_check_at','')).timestamp()>self.clock()
      except Exception:pass
      v['state']='succeeded'if fresh and auth.get('status')=='healthy'and ident.get('status')=='matched'and c.get('platform_account_id')==v['platformUid']else'verification_required'
     v['checkedAt']=stamp();self.save(v)
   return self.dto(v)
