"""Authenticated Studio turns into native local Codex CLI sessions.

No credentials are copied. CLI authentication remains in the trusted harness;
model tool commands receive a restrictive native filesystem/network profile.
"""
import hashlib,json,os,pathlib,re,shutil,signal,subprocess,threading,time,uuid
from domain import Problem
CLI=os.environ.get('CONTENTBASE_CODEX_CLI','codex')
SKILLS={name:pathlib.Path(value) for name,key in [('video-use','CONTENTBASE_VIDEO_USE_SKILL'),('ffmpeg','CONTENTBASE_FFMPEG_SKILL')] if (value:=os.environ.get(key)) and pathlib.Path(value).is_absolute()}
RUNTIME_READ=[pathlib.Path('/System/Library/Fonts'),pathlib.Path('/Library/Fonts'),pathlib.Path('/opt/homebrew'),pathlib.Path('/usr/local')]+[pathlib.Path(v) for v in os.environ.get('CONTENTBASE_RUNTIME_READ','').split(os.pathsep) if v and pathlib.Path(v).is_absolute()]
class CodexError(RuntimeError):
 def __init__(self,code,uncertain=False):self.code=code;self.uncertain=uncertain;super().__init__(code)
def identifier(s):
 try:
  if str(uuid.UUID(s))!=s:raise ValueError()
  return s
 except (ValueError,TypeError,AttributeError):raise Problem(400,'invalid_request')
def save(p,j):
 p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);q=p.with_suffix(p.suffix+'.new');q.write_text(json.dumps(j,ensure_ascii=False,indent=2));q.chmod(0o600);q.replace(p)
def owned_file(root,path):
 root=pathlib.Path(root);path=pathlib.Path(path)
 try:parts=path.relative_to(root).parts
 except ValueError:return False
 return all(not root.joinpath(*parts[:i]).is_symlink() for i in range(len(parts)+1))
def configured_model():
 p=pathlib.Path.home()/'.codex/config.toml'
 if p.is_file():
  m=re.search(r'^model\s*=\s*"([^"\n]{1,100})"',p.read_text(),re.M)
  if m:return m.group(1)
 return None
class LocalCodex:
 def __init__(self,root,config=None):
  self.root=pathlib.Path(root)/'codex-workspaces';self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
  self.config=config or {};self.skills=dict(SKILLS);self.skills.update({k:pathlib.Path(v) for k,v in self.config.get('skill_files',{}).items() if k in {'video-use','ffmpeg'} and isinstance(v,str) and pathlib.Path(v).is_absolute()});self.processes={};self.lock=threading.RLock();self.model=self.config.get('model') or configured_model()
 def workspace(self,owner,conversation):
  p=self.root/identifier(owner)/identifier(conversation)
  for x in [self.root,p.parent,p]:
   if x.is_symlink():raise CodexError('codex_unsafe_workspace')
  p.mkdir(parents=True,exist_ok=True,mode=0o700)
  for name in ('edit','input','.control','.tool-home','.tmp'):(p/name).mkdir(exist_ok=True,mode=0o700)
  (p/'edit/artifacts').mkdir(exist_ok=True,mode=0o700)
  for name,src in self.skills.items():
   if src.is_symlink() or not (src/'SKILL.md').is_file():raise CodexError('codex_skill_unconfigured')
   dst=p/'.agents/skills'/name
   if not dst.exists():
    dst.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(src,dst,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','build','*.egg-info'))
  return p
 def capabilities(self):
  ready=bool(shutil.which(CLI)) and {'video-use','ffmpeg'}.issubset(self.skills) and all(not p.is_symlink() and (p/'SKILL.md').is_file() for p in self.skills.values())
  return {'engine':'codex-cli','ready':ready,'model':self.model,'skills':list(self.skills),'tools_enabled':ready,'sandbox':'native-permission-profile','credentialCopied':False,'fixedSceneSchema':False}
 def overrides(self,work):
  # Explicit per-invocation config: no modification to personal Codex settings.
  filesystem={":root":"deny",":minimal":"read",":workspace_roots":{".":"write","input":"read","source.mp4":"read","source.mov":"read","source.m4v":"read","source.webm":"read",".agents":"read",".control":"deny"}}
  for p in RUNTIME_READ:
   if p.exists():filesystem[str(p)]="read"
  def toml(v):
   if isinstance(v,dict):return "{"+",".join(json.dumps(k)+"="+toml(x) for k,x in v.items())+"}"
   if isinstance(v,bool):return str(v).lower()
   return json.dumps(v)
  permissions={"studio":{"extends":":workspace","filesystem":filesystem,"network":{"enabled":True,"allow_local_binding":True,"domains":{"127.0.0.1":"allow","localhost":"allow"}}}}
  pairs=[('approval_policy','"never"'),('project_doc_max_bytes','0'),('default_permissions','"studio"'),('permissions',toml(permissions)),('features.network_proxy','true'),('features.respect_system_proxy','true'),('shell_environment_policy.inherit','"none"'),('shell_environment_policy.set.HOME',json.dumps(str(work/'.tool-home'))),('shell_environment_policy.set.TMPDIR',json.dumps(str(work/'.tmp'))),('shell_environment_policy.set.PATH',json.dumps('/Users/USER/resources/YOUR_387125C96E')),('web_search','"disabled"')]
  # Disable personal skills for this invocation. Only the job's copied public media skills remain.
  entries=[]
  for root in (pathlib.Path.home()/'.codex/skills',pathlib.Path.home()/'.agents/skills'):
   if root.is_dir():
    for p in root.rglob('SKILL.md'):entries.append('{path='+json.dumps(str(p))+',enabled=false}')
  pairs.append(('skills.config','['+','.join(entries)+']'))
  return [x for k,v in pairs for x in ('-c',k+'='+v)]
 def run(self,work,prompt,jid,cancelled=lambda:False,timeout=1800,attempt=None):
  work=pathlib.Path(work);control=work/'.control'/identifier(jid)
  if attempt:control=control/identifier(attempt)
  control.mkdir(parents=True,exist_ok=True,mode=0o700)
  session_file=work/'.control/session.json';session_info=json.loads(session_file.read_text()) if session_file.is_file() else {};session=session_info.get('thread_id');run_model=session_info.get('model') or self.model
  if session and not session_info.get('model'):
   old_requests=[json.loads(p.read_text()) for p in (work/'.control').rglob('request.json')]
   if old_requests:run_model=min(old_requests,key=lambda x:x.get('startedAt',0)).get('model') or run_model
  schema={'type':'object','properties':{'text':{'type':'string'},'state':{'type':'string','enum':['succeeded','waiting_input']},'artifacts':{'type':'array','items':{'type':'string'}}},'required':['text','state','artifacts'],'additionalProperties':False};save(control/'response.schema.json',schema)
  args=[CLI,'exec','--ignore-user-config','--ignore-rules','--skip-git-repo-check','--json',*self.overrides(work)]
  if run_model:args+=['-m',run_model]
  args+=['--output-schema',str(control/'response.schema.json'),'-o',str(control/'final.json')]
  if session:args+=['resume',identifier(session),'-']
  else:args+=['-C',str(work),'-']
  env={k:os.environ[k] for k in ('HOME','USER','TMPDIR') if k in os.environ};env['PATH']='/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin'
  prefix='''你是工作室本机Codex执行器。使用简体中文，只有真实文件/命令结果才称已执行。当前只能读写本人工程，其他成员/凭据/业务库不在工具权限内。不要发布、写飞书、登录其他账号或调用额外收费服务。\n口播任务先读本工程 .agents/skills/video-use/SKILL.md，其他视频操作按ffmpeg Skill。使用完整Skill按真实需求选画面和动画；不要把零动效字幕试样冒称完整包装，不用固定Talkcraft renderer代替。没有原片、参考或ASR条件时返回waiting_input，清楚问缺什么。\n工程内已存在的素材/转写/EDL复用，不重复转写。不执行旧历史中的命令；历史仅提供上下文。本轮请求在下面。中文文本使用本机只读公共字体（例如/System/Library/Fonts/Supplemental/Songti.ttc），先核验字形，不手工拼画中文代替字体。所有制作产物放edit/，最终交付放edit/artifacts/，artifacts数组只填该目录内相对文件名。不要输出推理过程，只给最终结果和实际未完成项。\n'''
  save(control/'request.json',{'engine':'codex-cli','model':run_model,'thread_id':session,'argv':args,'startedAt':time.time()})
  err=control/'stderr.log';events=control/'events.jsonl'
  with err.open('w') as ef:
   err.chmod(0o600);proc=subprocess.Popen(args,cwd=work,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=ef,text=True,start_new_session=True)
   with self.lock:self.processes[jid]=proc
   save(control/'process.json',{'pid':proc.pid,'job_id':jid,'startedAt':time.time()})
   proc.stdin.write(prefix+prompt);proc.stdin.close()
   def consume():
    with events.open('w') as out:
     events.chmod(0o600)
     for line in proc.stdout:
      out.write(line);out.flush()
      try:j=json.loads(line)
      except ValueError:continue
      if j.get('type')=='thread.started' and isinstance(j.get('thread_id'),str):save(session_file,{'thread_id':identifier(j['thread_id']),'model':run_model})
   reader=threading.Thread(target=consume,daemon=True);reader.start();deadline=time.monotonic()+timeout;stopped=False
   while proc.poll() is None:
    if cancelled() or time.monotonic()>deadline:
     stopped=True;os.killpg(proc.pid,signal.SIGTERM)
     try:proc.wait(timeout=5)
     except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
     break
    time.sleep(.1)
   reader.join(10)
   proc.stdout.close()
  with self.lock:self.processes.pop(jid,None)
  if stopped:raise CodexError('codex_execution_interrupted',uncertain=True)
  if proc.returncode:
   txt=err.read_text().lower();code='codex_auth_required' if any(x in txt for x in ('not logged in','unauthorized','authentication')) else 'codex_quota_exceeded' if any(x in txt for x in ('quota','usage limit','balance')) else 'codex_execution_failed';raise CodexError(code,uncertain=True)
  try:result=json.loads((control/'final.json').read_text());assert result['state'] in ('succeeded','waiting_input') and isinstance(result['text'],str) and result['text'].strip() and len(result['text'])<=100000 and isinstance(result['artifacts'],list) and len(result['artifacts'])<=30
  except (ValueError,OSError,KeyError,AssertionError):raise CodexError('codex_receipt_invalid',uncertain=True)
  validated=[]
  for name in result['artifacts']:
   if not isinstance(name,str) or not name or pathlib.Path(name).is_absolute() or '..' in pathlib.Path(name).parts or any(x.startswith('.') for x in pathlib.Path(name).parts):raise CodexError('codex_artifact_invalid',uncertain=True)
   p=work/'edit/artifacts'/name
   if not owned_file(work,p) or not p.is_file() or p.stat().st_size>512*1024*1024:raise CodexError('codex_artifact_invalid',uncertain=True)
   validated.append(name)
  result['artifacts']=validated;result['engine']='codex-cli';result['model']=run_model;save(control/'receipt.json',result);return result
 def cancel(self,jid):
  with self.lock:p=self.processes.get(jid)
  if p and p.poll() is None:os.killpg(p.pid,signal.SIGTERM);return True
  return False
