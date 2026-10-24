#!/usr/bin/env python3
"""Narrow content live-refresh deployment preserving the current gateway."""
import argparse,hashlib,json,os,re,shutil,sqlite3,subprocess,time,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LIVE=Path.home()/'Library/Application Support/ContentBaseStudioGateway'
LABEL='com.contentbase.studio-conversation-gateway'
def sha(data):return hashlib.sha256(data).hexdigest()
def once(text,old,new):
 if text.count(old)!=1:raise RuntimeError('content_refresh_anchor_drift')
 return text.replace(old,new)
def server_hooks(text):
 if 'from content_snapshot import ContentRefresher,ContentRefreshError' not in text:text=once(text,'from studio_internal import dispatch as studio_dispatch','from studio_internal import dispatch as studio_dispatch\nfrom content_snapshot import ContentRefresher,ContentRefreshError')
 if 'def readonly_content(value,live=False):' not in text:
  anchor='def load_config(path):'
  block="""def readonly_content(value,live=False):
    if not isinstance(value,dict):raise Problem(503,'not_found')
    value=dict(value);value.pop('_actorId',None);value.pop('_cacheScope',None)
    value['refreshing']=False;value['actor']={'name':'办公室只读','canWrite':False,'canApprove':False};value['viewKey']='office-shared-readonly'
    if live:value['phase']='ok';value['stale']=False;value['message']='已实时读取飞书原表'
    else:value['phase']='cached';value['stale']=True;value['message']='办公室只读缓存；正在后台核对飞书原表'
    return value

"""
  text=once(text,anchor,block+anchor)
 if "'content_refresh_unavailable'" not in text:
  anchor="ERRORS.update({'shared_context_not_enabled'"
  start=text.index(anchor);end=text.index('\n',start)
  text=text[:end+1]+"ERRORS.update({'content_refresh_unavailable':'飞书实时读取暂未完成，请稍后重试。','content_refresh_protocol_invalid':'飞书实时读取结果格式异常，旧缓存未覆盖。','content_refresh_failed':'飞书实时读取未完成，旧缓存仍保留。','content_authorization_required':'当前固定飞书身份需要重新授权。','content_identity_changed':'飞书读取身份与缓存身份不一致，未展示其他人的数据。','content_cli_failed':'飞书原表读取未通过，旧缓存仍保留。'})\n"+text[end+1:]
 old="""            if method=='GET' and path=='/v1/workspace/snapshot':
                if set(query)-{'kind','days'} or len(query.get('kind',[]))!=1:raise Problem(400,'invalid_request')
                try:days=int(query.get('days',['14'])[0])
                except ValueError:raise Problem(400,'invalid_request')
                if days not in (7,14,30,90):raise Problem(400,'invalid_request')
                self.reply(200,read_snapshot(service.config,query['kind'][0],days));return"""
 new="""            if method=='GET' and path=='/v1/workspace/snapshot':
                if set(query)-{'kind','days','refresh'} or len(query.get('kind',[]))!=1 or len(query.get('refresh',['0']))!=1 or query.get('refresh',['0'])[0] not in ('0','1'):raise Problem(400,'invalid_request')
                try:days=int(query.get('days',['14'])[0])
                except ValueError:raise Problem(400,'invalid_request')
                if days not in (7,14,30,90):raise Problem(400,'invalid_request')
                kind=query['kind'][0]
                if kind=='content' and query.get('refresh',['0'])[0]=='1':
                    try:value=service.content_refresher.refresh()
                    except ContentRefreshError as error:raise Problem(503,str(error))
                    self.reply(200,readonly_content(value,live=True));return
                self.reply(200,read_snapshot(service.config,kind,days));return"""
 if "query.get('refresh',['0'])" not in text:text=once(text,old,new)
 old='service.publishing=Publishing(config);server=ThreadingHTTPServer'
 new='service.publishing=Publishing(config);service.content_refresher=ContentRefresher(config);server=ThreadingHTTPServer'
 if 'service.content_refresher=ContentRefresher(config)' not in text:text=once(text,old,new)
 return text
def content_ui_hooks(text):
 old='<button class="content-icon-button" id="content-refresh" title="重新读取飞书"><img src="vendor/tabler-content/refresh.svg" alt="刷新"></button>'
 new='<button class="content-button" id="content-refresh" title="实时读取飞书原表"><img src="vendor/tabler-content/refresh.svg" alt=""><span>刷新飞书数据</span></button>'
 return text if new in text else once(text,old,new)
def idle():
 db=sqlite3.connect((LIVE/'state/chat.sqlite3').as_uri()+'?mode=ro',uri=True)
 try:n=db.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued','cancel_requested')").fetchone()[0]
 finally:db.close()
 active=[]
 for p in (LIVE/'state/production-runs').glob('*/status.json'):
  try:v=json.loads(p.read_text())
  except (OSError,ValueError):continue
  if v.get('state') not in ('succeeded','failed','execution_unknown'):active.append(v.get('id'))
 if n or active:raise RuntimeError('active_gateway_or_production_jobs_wait_before_restart')
def fingerprint(root):return {str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
def sources(src):
 files={'server.py':server_hooks((src/'server.py').read_text()).encode(),'web/browser-adapter.js':(ROOT/'backend/web/browser-adapter.js').read_bytes(),'office-app/web/content-ui.js':content_ui_hooks((src/'office-app/web/content-ui.js').read_text()).encode(),'content_snapshot.py':(ROOT/'backend/content_snapshot.py').read_bytes(),'content_refresh.cjs':(ROOT/'backend/content_refresh.cjs').read_bytes(),'view-cache.cjs':(ROOT/'src/view-cache.cjs').read_bytes()}
 for name in ('client.cjs','model.cjs'):files['content/'+name]=(ROOT/'src/content'/name).read_bytes()
 return files
def prepare(change):
 idle();change.mkdir(parents=True,exist_ok=True,mode=0o700)
 if (change/'manifest.json').exists():raise RuntimeError('content_refresh_deploy_already_prepared')
 src=LIVE/'src';files=sources(src);before=fingerprint(src);items=[]
 for name,data in files.items():
  target=src/name
  if target.is_symlink():raise RuntimeError('content_refresh_deploy_symlink')
  old=target.read_bytes() if target.exists() else None
  for folder,value in [('original',old),('modified',data)]:
   if value is None:continue
   p=change/folder/name;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);p.write_bytes(value);p.chmod(0o600)
  items.append({'relative':name,'original':sha(old) if old is not None else None,'modified':sha(data)})
 protected={k:v for k,v in before.items() if k not in files};state={str(p):sha(p.read_bytes()) for p in [LIVE/'config.json',Path.home()/'Library/Application Support/ERC Studio/content-space.json']}
 (change/'manifest.json').write_text(json.dumps({'runtime':str(LIVE),'label':LABEL,'files':items,'protected':protected,'protectedState':state},ensure_ascii=False,indent=2))
 print(json.dumps({'prepared':len(items),'preservedRuntimeFiles':len(protected),'activeJobs':0,'credentialsAndBusinessConfigPreserved':True}))
def validate(change):
 m=json.loads((change/'manifest.json').read_text())
 for item in m['files']:
  p=change/'modified'/item['relative'];assert sha(p.read_bytes())==item['modified']
  if p.suffix=='.py':compile(p.read_text(),str(p),'exec')
  if p.suffix in ('.js','.cjs'):subprocess.run(['/usr/local/bin/node','--check',str(p)],check=True,capture_output=True)
 print(json.dumps({'syntaxValid':True,'files':len(m['files'])}))
def restart():
 subprocess.run(['/bin/launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'],check=True)
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for _ in range(50):
  try:
   with opener.open('http://127.0.0.1:8844/health',timeout=2) as response:value=json.load(response)
   if value.get('worker_alive'):return value
  except Exception:pass
  time.sleep(.3)
 raise RuntimeError('content_refresh_gateway_health_failed')
def apply(change,rollback=False):
 idle();m=json.loads((change/'manifest.json').read_text());src=Path(m['runtime'])/'src'
 for name,h in m['protected'].items():
  if sha((src/name).read_bytes())!=h:raise RuntimeError('unrelated_runtime_changed:'+name)
 for name,h in m['protectedState'].items():
  if sha(Path(name).read_bytes())!=h:raise RuntimeError('protected_state_changed')
 for item in m['files']:
  p=src/item['relative'];current=sha(p.read_bytes()) if p.exists() else None;expected=item['modified'] if rollback else item['original']
  if current!=expected:raise RuntimeError('target_changed_since_prepare:'+item['relative'])
 for item in m['files']:
  p=src/item['relative'];value=item['original'] if rollback else item['modified']
  if value is None:
   p.unlink();continue
  data=(change/('original' if rollback else 'modified')/item['relative']).read_bytes();assert sha(data)==value;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);tmp=p.with_suffix(p.suffix+'.content-refresh-tmp');tmp.write_bytes(data);tmp.chmod(0o600);tmp.replace(p)
 idle();health=restart()
 for name,h in m['protected'].items():assert sha((src/name).read_bytes())==h
 for name,h in m['protectedState'].items():assert sha(Path(name).read_bytes())==h
 print(json.dumps({'rolledBack' if rollback else 'deployed':True,'files':len(m['files']),'health':health['status'],'unrelatedRuntimePreserved':True,'credentialsAndBusinessConfigPreserved':True}))
def rehearse(change):
 m=json.loads((change/'manifest.json').read_text());target=change/'rollback-rehearsal'
 if target.exists():raise RuntimeError('content_refresh_rehearsal_exists')
 target.mkdir(mode=0o700)
 for item in m['files']:
  p=target/item['relative'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((change/'modified'/item['relative']).read_bytes())
  if item['original'] is None:p.unlink()
  else:p.write_bytes((change/'original'/item['relative']).read_bytes());assert sha(p.read_bytes())==item['original']
 print(json.dumps({'rollbackRehearsal':True,'restored':sum(x['original'] is not None for x in m['files']),'newFilesRemoved':sum(x['original'] is None for x in m['files']),'businessDataTouched':False}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','validate','rehearse','deploy','rollback']);p.add_argument('--change',type=Path,required=True);a=p.parse_args();change=a.change.resolve()
 if a.action=='prepare':prepare(change)
 elif a.action=='validate':validate(change)
 elif a.action=='rehearse':rehearse(change)
 else:apply(change,a.action=='rollback')
