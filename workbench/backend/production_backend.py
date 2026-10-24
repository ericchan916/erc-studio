"""Authenticated, record-bound native Skill production; original versioned renderers remain for existing jobs."""
import hashlib,json,os,pathlib,re,shutil,subprocess,threading,time,uuid,urllib.parse
from core import Problem
from production_director import VERSION,SKILLS,MUSIC,SFX,prompt as director_prompt,build as build_director
from production_talkcraft import VERSION as TALKCRAFT_VERSION,verify_bundle
from production_skill import VERSION as SKILL_VERSION
from local_codex import LocalCodex,CodexError

CLI=os.environ.get('CONTENTBASE_CODEX_CLI','codex');FFMPEG=os.environ.get('CONTENTBASE_FFMPEG','ffmpeg');FFPROBE=os.environ.get('CONTENTBASE_FFPROBE','ffprobe');WHISPER=os.environ.get('CONTENTBASE_WHISPER','whisper-cli');MODEL=pathlib.Path(os.environ.get('CONTENTBASE_RESOURCE_D39B437A',str(pathlib.Path.home()/'.local/share/content-company-workbench/unconfigured-resources'/'CONTENTBASE_RESOURCE_D39B437A')));MODULES=pathlib.Path(os.environ.get('CONTENTBASE_RESOURCE_FC08F77C',str(pathlib.Path.home()/'.local/share/content-company-workbench/unconfigured-resources'/'CONTENTBASE_RESOURCE_FC08F77C')));BROWSER=pathlib.Path.home()/'.remotion/chrome-headless-shell/chrome-headless-shell';CHUNK=512*1024;MAX_BYTES=512*1024*1024
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(path,value):
 tmp=path.with_name(path.name+'.new');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.chmod(0o600);os.replace(tmp,path)
def clean(value,maxlen=160):return isinstance(value,str) and bool(value.strip()) and len(value)<=maxlen and '\x00' not in value
def slot(value):
 try:
  u=uuid.UUID(value);assert str(u)==value;return value
 except Exception:raise Problem(400,'invalid_request')
def verify_project(config,run,id,account,title):
 if not re.fullmatch('rec[A-Za-z0-9]{8,48}',id):raise Problem(400,'invalid_request')
 u=urllib.parse.urlsplit(config.get('workspace_links',{}).get('content',''));base=u.path.split('/base/')[-1].split('/')[0];table=urllib.parse.parse_qs(u.query).get('table',[''])[0]
 if not base or not re.fullmatch('tbl[A-Za-z0-9]{8,48}',table):raise Problem(503,'not_found')
 args=['lark-cli','base','+record-get','--base-token',base,'--table-id',table,'--record-id',id,'--field-id','视频主题','--field-id','所属账号','--field-id','视频类型','--format','json','--as','user','--profile',config['feishu_profile']]
 try:
  p=run(args,capture_output=True,text=True,timeout=35,env={**os.environ,'LARKSUITE_CLI_NO_UPDATE_NOTIFIER':'1','LARKSUITE_CLI_NO_SKILLS_NOTIFIER':'1'});j=json.loads(p.stdout)
  if p.returncode or j.get('ok') is not True:raise ValueError()
  d=j['data'];assert d['record_id_list']==[id] and len(d['data'])==1;f=dict(zip(d['fields'],d['data'][0]));
  names=f.get('所属账号');names=[x.get('text') or x.get('name') or x.get('title') if isinstance(x,dict) else x for x in names] if isinstance(names,list) else [names]
  if f.get('视频主题')!=title or account not in names or '口播' not in str(f.get('视频类型') or ''):raise ValueError()
 except Exception:raise Problem(409,'content_changed_refresh_required')
class Production:
 def __init__(self,config,template=None,run=None):
  self.root=pathlib.Path(config['state_dir'])/'production-runs';self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700)
  if self.root.is_symlink():raise RuntimeError('production_state_symlink')
  bundled=pathlib.Path(__file__).parent/'production-remotion';source=pathlib.Path(__file__).parent.parent/'production-remotion'
  self.template=pathlib.Path(template or (bundled if bundled.is_dir() else source));self.content_root=pathlib.Path(config['workspace_state_dir']);self.config=config;self.creator_owner=next((m['principal_id'] for m in config.get('members',[]) if m.get('handle')=='owner'),None);self.lock=threading.RLock();self.active=False;self.run=run or subprocess.run;self.codex=LocalCodex(config['state_dir'],config.get('local_codex'));self.skill_route=bool(config.get('local_codex',{}).get('enabled'))
  for p in self.root.glob('*/status.json'):
   try:
    v=json.loads(p.read_text());
    if v['state'] in ('accepted','transcribing','planning','rendering'):v['state']='execution_unknown';v['error']='服务中断；保留现场，未重跑';save(p,v)
   except (ValueError,KeyError,OSError):pass
 def _dir(self,id):return self.root/slot(id)
 def _read(self,id):
  p=self._dir(id)/'status.json'
  if not p.is_file() or p.is_symlink():raise Problem(404,'not_found')
  try:return json.loads(p.read_text())
  except (ValueError,OSError):raise Problem(503,'not_found')
 def _own(self,owner,id):
  v=self._read(id)
  if v['owner']!=owner:raise Problem(404,'not_found')
  return v
 def dto(self,v):
  result={k:v.get(k) for k in ('id','projectId','title','account','requirement','fileName','sourceFirstChunkSha256','state','received','size','error','summary','unmet','createdAt','updatedAt','renderStartedAt','finishedAt','failedStage','outputBytes','workflowVersion','phase','qualityStatus','variants','reviewedAt','sourceKind','outputFileName')}
  result['remotionVersion']=(v.get('runtime') or {}).get('remotionVersion') if v['state']=='succeeded' else None
  return result
 def _update(self,v,**kw):v.update(kw);v['updatedAt']=time.time();save(self._dir(v['id'])/'status.json',v)
 def engine(self):
  if not self.skill_route:return self._legacy_engine()
  value=self.codex.capabilities()
  return {**value,'name':'codex-cli + video-use','workflowVersion':SKILL_VERSION,'version':'local Codex CLI','expectedVersion':'local Codex CLI','sourceDurationLimitSeconds':600,'checkedAt':time.time(),'defaultSkill':'video-use','codeGeneration':True,'fixedSceneSchema':False}
 def _legacy_engine(self):
  try:
   pins=json.loads((self.template/'package.json').read_text())['dependencies'];expected=pins['remotion']
   version=json.loads((MODULES/'remotion/package.json').read_text())['version']
   ready=all(json.loads((MODULES/name/'package.json').read_text())['version']==pins[name] for name in ('remotion','@remotion/cli','react','react-dom')) and (MODULES/'.bin/remotion').is_file() and BROWSER.is_file()
   verify_bundle(self.template)
   ready=ready and (self.template/'talkcraft-media.swift').is_file() and pathlib.Path('/usr/bin/swift').is_file() and all(pathlib.Path(x).is_file() for x in (CLI,FFMPEG,FFPROBE,WHISPER,str(MODEL)))
   return {'ready':ready,'version':version,'expectedVersion':expected,'checkedAt':time.time(),'name':'video-talkcraft','workflowVersion':TALKCRAFT_VERSION,'sourceDurationLimitSeconds':600,'commercialAuthorization':'user-confirmed-2026-10-05'}
  except (OSError,ValueError,KeyError,RuntimeError):return {'ready':False,'version':None,'expectedVersion':None,'checkedAt':time.time(),'name':'video-talkcraft','workflowVersion':TALKCRAFT_VERSION}
 def _project(self,id,account,title):return verify_project(self.config,self.run,id,account,title)
 def start(self,owner,body,key):
  if not isinstance(body,dict) or set(body)!={'projectId','title','account','requirement','size','fileName'}:raise Problem(400,'invalid_request')
  pid,title,account,req=body['projectId'],body['title'],body['account'],body['requirement'];size=body['size'];name=body['fileName']
  if not all(clean(x,4000 if x==req else 160) for x in (pid,title,account,req,name)) or len(req)>4000 or account not in self.config.get('production_accounts',[]) or not isinstance(size,int) or isinstance(size,bool) or not 0<size<=MAX_BYTES or not re.search(r'\.(mp4|mov|m4v|webm)$',name,re.I):raise Problem(400,'invalid_request')
  if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{8,100}',key):raise Problem(400,'invalid_request')
  if self.creator_owner and owner!=self.creator_owner:raise Problem(403,'production_permission_denied')
  if shutil.disk_usage(self.root).free<max(3*1024**3,size*4+1024**3):raise Problem(507,'production_disk_capacity_low')
  signature=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
  with self.lock:
   for path in self.root.glob('*/status.json'):
    try:
     v=json.loads(path.read_text())
     if v['owner']==owner and v['key']==key:
      if v['signature']!=signature:raise Problem(409,'idempotency_conflict')
      return self.dto(v)
    except (ValueError,KeyError,OSError):continue
   for path in self.root.glob('*/status.json'):
    previous=json.loads(path.read_text())
    if previous.get('owner')==owner and previous.get('projectId')==pid and previous.get('state')=='execution_unknown':raise Problem(409,'production_result_unknown')
   if sum(1 for p in self.root.glob('*/status.json') if json.loads(p.read_text()).get('owner')==owner and json.loads(p.read_text()).get('state') not in ('succeeded','failed','execution_unknown'))>=2:raise Problem(409,'conversation_busy_or_unknown')
   self._project(pid,account,title)
   id=str(uuid.uuid4());d=self._dir(id);d.mkdir(mode=0o700)
   now=time.time();v={'id':id,'owner':owner,'projectId':pid,'title':title,'account':account,'requirement':req,'size':size,'fileName':name,'key':key,'signature':signature,'received':0,'state':'uploading','workflowVersion':SKILL_VERSION if self.skill_route else TALKCRAFT_VERSION,'phase':'upload','createdAt':now,'updatedAt':now,'finishedAt':None,'error':None,'summary':None,'unmet':[]};save(d/'status.json',v);return self.dto(v)
 def upload(self,owner,id,offset,chunk,checksum):
  if not chunk or len(chunk)>CHUNK or not re.fullmatch('[a-f0-9]{64}',checksum) or hashlib.sha256(chunk).hexdigest()!=checksum:raise Problem(400,'invalid_request')
  with self.lock:
   v=self._own(owner,id);p=self._dir(id)/'source.upload'
   if v['state']!='uploading':raise Problem(409,'content_stage_conflict')
   if not isinstance(offset,int) or offset<0 or offset>v['size']:raise Problem(400,'invalid_request')
   if offset<v['received']:
    if offset+len(chunk)<=v['received'] and p.is_file():
     with p.open('rb') as f:f.seek(offset);old=f.read(len(chunk))
     if old==chunk:return self.dto(v)
    raise Problem(409,'idempotency_conflict')
   if offset!=v['received'] or offset+len(chunk)>v['size']:raise Problem(409,'content_changed_refresh_required')
   if p.exists() and p.is_symlink():raise Problem(409,'content_changed_refresh_required')
   with p.open('ab') as f:f.write(chunk);f.flush();os.fsync(f.fileno())
   p.chmod(0o600)
   self._update(v,received=offset+len(chunk),**({'sourceFirstChunkSha256':checksum} if offset==0 else {}));return self.dto(v)
 def submit(self,owner,id):
  with self.lock:
   v=self._own(owner,id)
   if v['state']!='uploading' or v['received']!=v['size']:raise Problem(409,'content_stage_conflict')
   for path in self.root.glob('*/status.json'):
    prior=json.loads(path.read_text())
    if prior.get('owner')==owner and prior.get('projectId')==v['projectId'] and prior.get('state')=='execution_unknown':raise Problem(409,'production_result_unknown')
   if self.active:raise Problem(409,'conversation_busy_or_unknown')
   self.active=True;self._update(v,state='accepted');threading.Thread(target=self._work,args=(id,),daemon=True,name='studio-production').start();return self.dto(v)
 def respond(self,owner,id,body,key):
  if not isinstance(body,dict) or set(body)!={'text'} or not clean(body['text'],4000) or not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{8,100}',key):raise Problem(400,'invalid_request')
  with self.lock:
   v=self._own(owner,id)
   for c in v.get('controls',[]):
    if c['key']==key:
     if c['text']!=body['text']:raise Problem(409,'idempotency_conflict')
     return self.dto(v)
   if v['state']!='waiting_input' or v.get('workflowVersion')!=SKILL_VERSION or self.active:raise Problem(409,'content_stage_conflict')
   v.setdefault('controls',[]).append({'key':key,'text':body['text']});self._update(v,state='accepted',phase='resume_codex');self.active=True
   threading.Thread(target=self._work,args=(id,),daemon=True).start();return self.dto(v)
 def get(self,owner,id):return self.dto(self._own(owner,id))
 def list(self,owner,project):
  if not re.fullmatch('rec[A-Za-z0-9]{8,48}',project):raise Problem(400,'invalid_request')
  rows=[]
  for p in self.root.glob('*/status.json'):
   try:
    v=json.loads(p.read_text())
    if v['owner']==owner and v['projectId']==project:rows.append(self.dto(v))
   except (ValueError,KeyError,OSError):pass
  return {'items':sorted(rows,key=lambda x:x['createdAt'],reverse=True)[:20],'engine':self.engine()}
 def video(self,owner,id,variant='main'):
  v=self._own(owner,id)
  if v['state']!='succeeded':raise Problem(404,'not_found')
  if variant not in ('main','nobgm') or variant=='nobgm' and 'nobgm' not in (v.get('variants') or []):raise Problem(404,'not_found')
  p=self._dir(id)/('output-nobgm.mp4' if variant=='nobgm' else 'output.mp4')
  if not p.is_file() or p.is_symlink():raise Problem(404,'not_found')
  return p
 def confirm(self,owner,id):
  with self.lock:
   v=self._own(owner,id)
   if v['state']!='succeeded':raise Problem(409,'content_stage_conflict')
   output=self.video(owner,id)
   if sha(output)!=v.get('outputSha256'):raise Problem(409,'content_changed_refresh_required')
   if v.get('qualityStatus')!='approved':self._update(v,qualityStatus='approved',phase='reviewed',reviewedAt=time.time(),approvedOutputSha256=v['outputSha256'])
   return self.dto(v)
 def _run(self,args,cwd,timeout,input=None,env=None):
  p=self.run(args,cwd=cwd,timeout=timeout,input=input,capture_output=True,text=True,env=env)
  if pathlib.Path(cwd).is_relative_to(self.root) if hasattr(pathlib.Path(cwd),'is_relative_to') else str(pathlib.Path(cwd)).startswith(str(self.root)+os.sep):
   redact=lambda text:re.sub(r'(?i)(bearer\s+|api[_-]?key[=:]\s*|token[=:]\s*)[^\s\"\']+',r'\1[REDACTED]',text)
   with (pathlib.Path(cwd)/'command-records.jsonl').open('a') as record:record.write(json.dumps({'args':args,'cwd':str(cwd),'inputSha256':hashlib.sha256(input.encode()).hexdigest() if isinstance(input,str) else None,'stdout':redact(p.stdout[-12000:]),'stderr':redact(p.stderr[-12000:]),'exit':p.returncode},ensure_ascii=False)+'\n')
  if p.returncode:
   reason=(p.stderr+' '+p.stdout).lower()
   if args[0]==CLI and any(x in reason for x in ('insufficient_quota','credit balance','quota exceeded','model_http_402')):raise RuntimeError('codex_quota_or_balance_insufficient')
   if args[0]==CLI and any(x in reason for x in ('not logged in','authentication required','unauthorized')):raise RuntimeError('codex_auth_required')
   raise RuntimeError('step_failed:'+pathlib.Path(args[0]).name+':exit_'+str(p.returncode))
  return p.stdout
 def _work(self,id):
  try:self._produce(id)
  except Exception as error:
   with self.lock:
    v=self._read(id);self._update(v,state='execution_unknown' if isinstance(error,CodexError) and error.uncertain else 'failed',failedStage=v['state'],error=str(error)[:180],finishedAt=time.time())
  finally:
   with self.lock:self.active=False
 def _produce(self,id):
  if self._read(id).get('workflowVersion')==SKILL_VERSION:
   from production_skill import execute
   return execute(self,id)
  if self._read(id).get('workflowVersion')==TALKCRAFT_VERSION:
   from production_talkcraft_executor import execute
   return execute(self,id)
  d=self._dir(id);v=self._read(id);env={k:os.environ[k] for k in ('HOME','PATH','USER','TMPDIR') if k in os.environ};env['PATH']=os.environ.get('PATH','');src=d/'source.upload';self._update(v,state='transcribing',phase='transcript',workflowVersion=VERSION)
  probe=json.loads(self._run([FFPROBE,'-v','error','-show_streams','-show_format','-of','json',str(src)],d,25,env=env));streams=probe.get('streams',[]);video=next((x for x in streams if x.get('codec_type')=='video'),None);audio=next((x for x in streams if x.get('codec_type')=='audio'),None)
  if not video or not audio:raise RuntimeError('source_requires_video_and_speech_audio')
  duration=float(probe['format']['duration'])
  if not 0.5<=duration<=300:raise RuntimeError('source_duration_unsupported')
  pins=json.loads((self.template/'package.json').read_text())['dependencies']
  for name in ('remotion','@remotion/cli','react','react-dom'):
   if json.loads((MODULES/name/'package.json').read_text())['version']!=pins[name]:raise RuntimeError('remotion_dependency_version_changed')
  work=d/'work';work.mkdir(mode=0o700);(work/'public').mkdir();(work/'src').mkdir();
  for name in ['package.json','director.schema.json','WORKFLOW.md']:shutil.copy2(self.template/name,work/name)
  for name in ['index.ts','Root.tsx','DirectedFilm.tsx']:shutil.copy2(self.template/'src'/name,work/'src'/name)
  (work/'node_modules').symlink_to(MODULES,target_is_directory=True)
  self._run([FFMPEG,'-hide_banner','-loglevel','error','-y','-i',str(src),'-map','0:v:0','-map','0:a:0','-vf','scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2','-r','30','-c:v','libx264','-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-ar','48000','-movflags','+faststart',str(work/'public/source.mp4')],d,900,env=env)
  wav=work/'audio.wav';self._run([FFMPEG,'-hide_banner','-loglevel','error','-y','-i',str(work/'public/source.mp4'),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)],d,90,env=env)
  asr=work/'asr';self._run([WHISPER,'-m',str(MODEL),'-f',str(wav),'-l','zh','-oj','-of',str(asr),'-np'],d,900,env=env)
  raw=json.loads((work/'asr.json').read_text());segments=[]
  for item in raw.get('transcription',[]):
   offsets=item.get('offsets',{});start=offsets.get('from');end=offsets.get('to');text=item.get('text','').strip()
   if isinstance(start,int) and isinstance(end,int) and 0<=start<end<=duration*1000+1000 and text:segments.append({'startMs':start,'endMs':min(end,round(duration*1000)),'text':text[:240]})
  if not segments:raise RuntimeError('transcription_empty_no_cut_plan')
  (work/'transcript.json').write_text(json.dumps(segments,ensure_ascii=False,indent=2));self._update(v,state='planning')
  if not all(x.is_file() for x in SKILLS):raise RuntimeError('production_skill_missing')
  request=director_prompt(self.template,v['title'],v['requirement'],segments,duration)
  (work/'director-request.txt').write_text(request);self._update(v,state='planning',phase='autonomous_directing')
  out=work/'plan.json';self._run([CLI,'exec','--ignore-user-config','--ephemeral','--skip-git-repo-check','-c','model_reasoning_effort="high"','-C',str(work),'-s','read-only','--output-schema',str(work/'director.schema.json'),'-o',str(out),'-'],work,900,input=request,env=env)
  plan=json.loads(out.read_text())
  try:data,unmet=build_director(plan,segments,duration)
  except RuntimeError as error:
   if not str(error).startswith('director_'):raise
   repair=request+'\n上次计划违反约束：'+str(error)+'\n修正此计划，仍输出完整Schema：'+json.dumps(plan,ensure_ascii=False)
   self._run([CLI,'exec','--ignore-user-config','--ephemeral','--skip-git-repo-check','-c','model_reasoning_effort="high"','-C',str(work),'-s','read-only','--output-schema',str(work/'director.schema.json'),'-o',str(out),'-'],work,900,input=repair,env=env)
   plan=json.loads(out.read_text());data,unmet=build_director(plan,segments,duration)
  self._render_directed(d,v,work,plan,data,unmet,env)
 def _render_directed(self,d,v,work,plan,data,unmet,env):
  """Render only a validated plan; shared by normal execution and private rehearsal."""
  if MUSIC.is_file():shutil.copy2(MUSIC,work/'public/bgm.mp3')
  else:raise RuntimeError('production_music_asset_missing')
  if SFX.is_file():shutil.copy2(SFX,work/'public/paper.mp3')
  else:raise RuntimeError('production_sound_asset_missing')
  save(work/'render-data.json',data);save(work/'props-nobgm.json',{'bgm':False});save(work/'director-receipt.json',{'workflowVersion':VERSION,'intent':plan['intent'],'scenes':data['scenes'],'unmet':unmet,'skillFiles':[{'name':x.parent.name,'sha256':sha(x)} for x in SKILLS],'qualityBoundary':'技术验证不是本人审片；头像使用固定裁切。'})
  self._update(v,state='rendering',phase='remotion',renderStartedAt=time.time(),error=None,failedStage=None)
  remotion=MODULES/'.bin/remotion';tmp=d/'output.tmp.mp4';self._run([str(remotion),'render','src/index.ts','VoiceEdit',str(tmp),'--codec','h264','--audio-codec','aac','--pixel-format','yuv420p','--browser-executable='+str(BROWSER),'--concurrency=2','--overwrite'],work,1200,env={**env,'HOME':str(work)})
  nobgm=d/'output-nobgm.mp4';self._run([str(remotion),'render','src/index.ts','VoiceEdit',str(nobgm),'--props='+str(work/'props-nobgm.json'),'--codec','h264','--audio-codec','aac','--pixel-format','yuv420p','--browser-executable='+str(BROWSER),'--concurrency=2','--overwrite'],work,1200,env={**env,'HOME':str(work)})
  self._update(v,state='rendering',phase='audio_mastering')
  for audio_target in (tmp,nobgm):
   mastered=audio_target.with_name(audio_target.stem+'.master.mp4')
   self._run([FFMPEG,'-hide_banner','-loglevel','error','-y','-i',str(audio_target),'-c:v','copy','-af','loudnorm=I=-16:TP=-1.5:LRA=11','-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart',str(mastered)],d,180,env=env);mastered.replace(audio_target)
  self._update(v,state='rendering',phase='verifying')
  output=d/'output.mp4';tmp.replace(output);output.chmod(0o600);check=json.loads(self._run([FFPROBE,'-v','error','-show_streams','-show_format','-of','json',str(output)],d,30,env=env));vs=next((x for x in check['streams'] if x.get('codec_type')=='video'),None);au=next((x for x in check['streams'] if x.get('codec_type')=='audio'),None)
  if not vs or not au or vs.get('width')!=1080 or vs.get('height')!=1440 or vs.get('codec_name')!='h264' or au.get('codec_name')!='aac' or output.stat().st_size<2000:raise RuntimeError('render_verification_failed')
  second=json.loads(self._run([FFPROBE,'-v','error','-show_streams','-show_format','-of','json',str(nobgm)],d,30,env=env))
  if not any(x.get('codec_type')=='audio' for x in second['streams']) or not any(x.get('width')==1080 and x.get('height')==1440 for x in second['streams']):raise RuntimeError('nobgm_verification_failed')
  if abs(float(check['format']['duration'])-data['totalFrames']/30)>.3 or abs(float(second['format']['duration'])-data['totalFrames']/30)>.3:raise RuntimeError('render_duration_mismatch')
  frame_checks=[]
  for index,scene in enumerate(data['scenes']):
   frame=min(scene['to']-1,scene['from']+max(1,scene['duration']//2));shot=work/('scene-check-%02d.png'%index)
   self._run([FFMPEG,'-hide_banner','-loglevel','error','-y','-ss',str(frame/30),'-i',str(output),'-frames:v','1',str(shot)],d,30,env=env);frame_checks.append({'frame':frame,'sha256':sha(shot)})
  hashes=[]
  for artifact in (output,nobgm):hashes.append(self._run([FFMPEG,'-v','error','-i',str(artifact),'-map','0:v:0','-f','framemd5','-'],d,60,env=env))
  for name,value in zip(('variant-main.framemd5','variant-nobgm.framemd5'),hashes):(work/name).write_text(value)
  if hashes[0]!=hashes[1]:raise RuntimeError('render_variant_picture_mismatch')
  for index,scene in enumerate(data['scenes']):
   for tag,frame in [('in',scene['from']),('out',scene['to']-1)]:
    shot=work/('scene-check-%02d-%s.png'%(index,tag));self._run([FFMPEG,'-v','error','-y','-ss',str(frame/30),'-i',str(output),'-frames:v','1',str(shot)],d,30,env=env);frame_checks.append({'frame':frame,'sha256':sha(shot)})
  save(work/'verification.json',{'workflowVersion':VERSION,'width':1080,'height':1440,'frames':data['totalFrames'],'mainSha256':sha(output),'nobgmSha256':sha(nobgm),'frameChecks':frame_checks,'status':'technical_pass_human_review_pending'})
  self._update(v,state='succeeded',phase='awaiting_human_review',finishedAt=time.time(),summary=str(plan.get('summary',''))[:600],unmet=unmet,qualityStatus='partial' if unmet else 'needs_review',variants=['main','nobgm'],outputSha256=sha(output),nobgmSha256=sha(nobgm),outputBytes=output.stat().st_size,transcriptSha256=sha(work/'transcript.json'),planSha256=sha(work/'plan.json'),runtime={'codex':CLI,'remotionVersion':'4.0.531'})
