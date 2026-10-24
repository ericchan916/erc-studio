"""Existing analytics service exposed as live reads and owner-bound sync receipts.

Only technical job state is stored here. All analytics facts remain in the
existing Feishu tables; no browser-supplied paths, identities or credentials.
"""
import copy, hashlib, json, os, re, signal, subprocess, threading, time, uuid
from pathlib import Path
from core import Problem, shape

NODE='/usr/local/bin/node'
BUSY={'accepted','running'}
def days_value(value):
 if type(value) is not int or value not in (7,14,30,90):raise Problem(400,'invalid_request')
 return value
def save(path,value):
 tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False));tmp.chmod(0o600);tmp.replace(path)
def public_snapshot(value,can_sync=False):
 if not isinstance(value,dict) or value.get('phase')!='ok' or value.get('stale') is not False or value.get('refreshing') is not False or not isinstance(value.get('readAt'),str) or not isinstance(value.get('layers'),list):raise Problem(503,'analytics_protocol_invalid')
 value=copy.deepcopy(value)
 for key in ('_actorId','_cacheScope','viewKey'):value.pop(key,None)
 value['canSync']=can_sync;value['message']='已实时读取飞书统计原表；RUM采集时间见各指标';return value

class AnalyticsGateway:
 def __init__(self,config,run=None):
  self.root=Path(config['workspace_state_dir']).resolve();self.jobs=Path(config['state_dir'])/'analytics-runs'
  if self.jobs.is_symlink():raise ValueError('analytics_state_symlink')
  self.jobs.mkdir(parents=True,exist_ok=True,mode=0o700);self.jobs.chmod(0o700)
  self.script=Path(__file__).with_name('analytics_refresh.cjs');self.run=run or self.command;self.lock=threading.RLock();self.reads={};self.threads={}
  self.members={m['principal_id']:m for m in config['members']};self.allowed={p for p,m in self.members.items() if m.get('handle')=='owner' and m.get('active',True)}
  for path in self.jobs.glob('*.json'):
   value=json.loads(path.read_text())
   if value['state'] in BUSY:
    value.update(state='execution_unknown',error='analytics_restart_unknown');save(path,value)
 def command(self,args,timeout):
  env={k:os.environ[k] for k in ('HOME','USER','TMPDIR') if k in os.environ};env.update(PATH='/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin',LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1',LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
  process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,start_new_session=True)
  try:out,err=process.communicate(timeout=timeout)
  except subprocess.TimeoutExpired:
   try:os.killpg(process.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   process.communicate();raise
  return subprocess.CompletedProcess(args,process.returncode,out,err)
 def invoke(self,action,days,actor=''):
  try:result=self.run([NODE,str(self.script),str(self.root),action,str(days),actor],timeout=900 if action=='sync' else 100)
  except (OSError,subprocess.TimeoutExpired):return {'ok':False,'error':'analytics_transport_failed','mayHaveWritten':action=='sync'}
  try:value=json.loads(result.stdout)
  except (ValueError,TypeError):return {'ok':False,'error':'analytics_protocol_invalid','mayHaveWritten':action=='sync'}
  if not isinstance(value,dict) or value.get('ok') not in (True,False):return {'ok':False,'error':'analytics_protocol_invalid','mayHaveWritten':action=='sync'}
  if result.returncode==0 and value.get('ok') is True:
   try:
    value['snapshot']=public_snapshot(value['snapshot'])
    if value['snapshot'].get('days')!=days:raise Problem(503,'analytics_protocol_invalid')
   except (KeyError,Problem):return {'ok':False,'error':'analytics_protocol_invalid','mayHaveWritten':action=='sync'}
  else:
   code=value.get('error','');value['ok']=False;value['error']=code if re.fullmatch(r'analytics_[A-Za-z0-9._-]{1,128}',str(code)) else 'analytics_operation_failed'
  return value
 def permit(self,owner):
  if owner not in self.allowed:raise Problem(403,'analytics_sync_permission_denied')
 def read(self,owner,days):
  days_value(days)
  with self.lock:
   if any(v['state'] in BUSY for v in self.values()):raise Problem(409,'analytics_sync_busy')
   flight=self.reads.get(days)
   if flight is None:flight={'event':threading.Event()};self.reads[days]=flight;first=True
   else:first=False
  if first:
   try:flight['result']=self.invoke('read',days)
   finally:
    with self.lock:self.reads.pop(days,None);flight['event'].set()
  else:flight['event'].wait(110)
  result=flight.get('result')
  if not result:raise Problem(503,'analytics_transport_failed')
  if not result['ok']:raise Problem(503,result['error'])
  value=copy.deepcopy(result['snapshot']);value['canSync']=owner in self.allowed;return value
 def values(self):return [json.loads(p.read_text()) for p in self.jobs.glob('*.json')]
 def dto(self,value):
  value=copy.deepcopy(value)
  for key in ('owner','key','actor'):value.pop(key,None)
  return value
 def state(self,owner):
  with self.lock:
   active=next((v for v in self.values() if v['owner']==owner and v['state'] in BUSY|{'execution_unknown'}),None)
   return {'canSync':owner in self.allowed,'activeJob':self.dto(active) if active else None}
 def get(self,owner,jid):
  try:uuid.UUID(jid);path=self.jobs/(jid+'.json');value=json.loads(path.read_text())
  except (ValueError,OSError):raise Problem(404,'not_found')
  if value['owner']!=owner:raise Problem(404,'not_found')
  return self.dto(value)
 def start(self,owner,body,key):
  self.permit(owner);shape(body,['days'],['days']);days=days_value(body['days'])
  if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',key):raise Problem(400,'invalid_request')
  key=hashlib.sha256(key.encode()).hexdigest()
  with self.lock:
   values=self.values();existing=next((v for v in values if v['owner']==owner and v['key']==key),None)
   if existing:
    if existing['days']!=days:raise Problem(409,'idempotency_conflict')
    return self.dto(existing)
   if any(v['state']=='execution_unknown' for v in values):raise Problem(409,'analytics_sync_unknown')
   if any(v['state'] in BUSY for v in values):raise Problem(409,'analytics_sync_busy')
   if self.reads:raise Problem(409,'analytics_sync_busy')
   jid=str(uuid.uuid4());value={'id':jid,'owner':owner,'actor':self.members[owner]['open_id'],'key':key,'days':days,'state':'accepted','createdAt':time.time(),'error':None}
   save(self.jobs/(jid+'.json'),value);thread=threading.Thread(target=self.work,args=(value,),daemon=True);self.threads[jid]=thread;thread.start();return self.dto(value)
 def work(self,value):
  path=self.jobs/(value['id']+'.json');value=copy.deepcopy(value);value['state']='running'
  with self.lock:save(path,value)
  try:result=self.invoke('sync',value['days'],value['actor'])
  except Exception:result={'ok':False,'mayHaveWritten':True,'error':'analytics_operation_failed'}
  with self.lock:
   value.update(state='succeeded' if result['ok'] else 'execution_unknown' if result.get('mayHaveWritten') else 'failed',error=result.get('error'),finishedAt=time.time())
   if result['ok']:
    value['snapshot']=result['snapshot'];value['snapshot'].update(canSync=True,message='已采集腾讯云 RUM、写回原表并读回核验')
   elif isinstance(result.get('expected'),dict):value['expected']=result['expected']
   save(path,value);self.threads.pop(value['id'],None)
 def verify(self,owner,jid):
  self.permit(owner);value=self.get(owner,jid)
  if value['state']!='execution_unknown':return value
  snapshot=self.read(owner,value['days']);expected=value.get('expected');layer=next((v for v in snapshot['layers'] if v.get('id')=='rum'),{})
  if not expected or expected.get('days')!=value['days'] or any(layer.get(key)!=expected.get(key) for key in ('updatedAt','current','pages','channels','goals')):raise Problem(409,'analytics_sync_unknown')
  with self.lock:
   path=self.jobs/(jid+'.json');stored=json.loads(path.read_text());stored.update(state='succeeded',error=None,snapshot=snapshot,verifiedAt=time.time());save(path,stored);return self.dto(stored)
