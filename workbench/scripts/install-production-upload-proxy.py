#!/usr/bin/env python3
"""Narrow video-upload policy for existing studio nginx instances; no auth/data changes."""
import argparse,hashlib,json,os,pathlib,pwd,subprocess,tempfile
HOME=pathlib.Path('/Users/USER')
USER_ROOT=HOME/'Library/Application Support/ContentBaseOfficeLAN'
SYSTEM_ROOT=pathlib.Path('/Library/Application Support/ContentBaseOfficeLAN443')
NGINX='/opt/homebrew/bin/nginx'
UPLOAD_LOCATION="    location ~ \"^/v1/production/jobs/[a-f0-9-]{36}/source$\" {\n      client_max_body_size 512k;\n      proxy_request_buffering off;\n      proxy_pass http://127.0.0.1:8844;\n      proxy_set_header Host $http_host;\n      proxy_set_header Origin $http_origin;\n      proxy_set_header X-Forwarded-Proto https;\n      proxy_set_header X-Studio-Native \"\";\n      proxy_set_header Connection \"\";\n      proxy_buffering off;\n      proxy_connect_timeout 3s;\n      proxy_read_timeout 120s;\n      proxy_send_timeout 15s;\n    }\n"
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
LEGACY_UPLOAD_LOCATION=UPLOAD_LOCATION.replace('      proxy_request_buffering off;','      client_body_buffer_size 512k;\n      proxy_request_buffering on;')
def patch(text):
 anchor='    location / {\n'
 if UPLOAD_LOCATION in text:return text
 if text.count(LEGACY_UPLOAD_LOCATION)==1:return text.replace(LEGACY_UPLOAD_LOCATION,UPLOAD_LOCATION,1)
 if text.count(anchor)!=1 or text.count('client_max_body_size 64k;')!=1 or '/v1/production/jobs/' in text:
  raise RuntimeError('proxy_configuration_changed_review_required')
 return text.replace(anchor,UPLOAD_LOCATION+anchor,1)
def command(args):
 p=subprocess.run(args,capture_output=True,text=True)
 if p.returncode:raise RuntimeError('nginx_validation_failed:'+p.stderr[-1200:])
 return {'command':args,'exit_status':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
def save_record(path,value):
 path.write_text(json.dumps(value,ensure_ascii=False,indent=2));path.chmod(0o600)
 if os.geteuid()==0:os.chown(path,HOME.stat().st_uid,HOME.stat().st_gid)
def checked_base(base):
 base=base.resolve()
 allowed=HOME/'Library/Application Support/ContentBaseChanges'
 if allowed not in base.parents or base.is_symlink() or base.stat().st_uid!=HOME.stat().st_uid or base.stat().st_mode&0o077:
  raise RuntimeError('change_directory_invalid')
 return base
def install(base,system):
 base=checked_base(base);root=SYSTEM_ROOT if system else USER_ROOT
 if system and os.geteuid()!=0:raise RuntimeError('administrator_confirmation_required')
 target=root/'nginx.conf'
 if target.is_symlink() or not target.is_file():raise RuntimeError('proxy_config_invalid')
 manifest=base/('proxy-system-manifest.json' if system else 'proxy-user-manifest.json')
 old=json.loads(manifest.read_text()) if manifest.exists() else None
 if old:assert sha(target)==old['after'],'proxy_changed_since_install'
 original=target.read_bytes();modified=patch(original.decode()).encode()
 if old and modified==original:
  print(json.dumps({'already_installed':True,'system':system}));return
 name='system' if system else 'user';backup=base/(name+'-nginx-original.conf');copy=base/(name+'-nginx-modified.conf')
 for p,data in [(backup,original),(copy,modified)]:
  if p.is_symlink():raise RuntimeError('backup_symlink_invalid')
  if p.exists():
   if not old or sha(p)!=(old['before'] if p==backup else old['after']):raise RuntimeError('backup_already_exists')
   if p==backup:continue
  p.write_bytes(data);p.chmod(0o600)
  if os.geteuid()==0:os.chown(p,HOME.stat().st_uid,HOME.stat().st_gid)
 st=target.stat();candidate=target.with_name('nginx.conf.production-candidate')
 if candidate.exists():raise RuntimeError('candidate_already_exists')
 candidate.write_bytes(modified);candidate.chmod(st.st_mode&0o777)
 if os.geteuid()==0:os.chown(candidate,st.st_uid,st.st_gid)
 try:
  validation=command([NGINX,'-t','-p',str(root)+'/', '-c',str(candidate)])
  assert sha(target)==hashlib.sha256(original).hexdigest(),'proxy_changed_during_install'
  os.replace(candidate,target)
  try:reload=command([NGINX,'-s','reload','-p',str(root)+'/', '-c',str(target)])
  except Exception:
   target.write_bytes(original);target.chmod(st.st_mode&0o777);command([NGINX,'-s','reload','-p',str(root)+'/', '-c',str(target)]);raise
  record={'path':str(target),'before':sha(backup),'after':hashlib.sha256(modified).hexdigest(),'original':str(backup),'modified':str(copy),'mode':st.st_mode&0o777,'uid':st.st_uid,'gid':st.st_gid,'validation':validation,'reload':reload}
  save_record(manifest,record)
 finally:
  if candidate.exists():candidate.unlink()
 print(json.dumps({'installed':True,'system':system,'global_text_limit':'64k','video_route_limit':'512k','graceful_reload':True,'gateway_untouched':True}))
def rollback(base,system):
 base=checked_base(base);root=SYSTEM_ROOT if system else USER_ROOT
 if system and os.geteuid()!=0:raise RuntimeError('administrator_confirmation_required')
 data=json.loads((base/('proxy-system-manifest.json' if system else 'proxy-user-manifest.json')).read_text())
 target=root/'nginx.conf';original=pathlib.Path(data['original'])
 assert data['path']==str(target) and original.parent==base and sha(target)==data['after'] and sha(original)==data['before'],'rollback_hash_guard_failed'
 command([NGINX,'-t','-p',str(root)+'/', '-c',str(original)])
 tmp=target.with_name('nginx.conf.production-rollback');tmp.write_bytes(original.read_bytes());tmp.chmod(data['mode'])
 if os.geteuid()==0:os.chown(tmp,data['uid'],data['gid'])
 os.replace(tmp,target);command([NGINX,'-s','reload','-p',str(root)+'/', '-c',str(target)]);assert sha(target)==data['before']
 print(json.dumps({'proxy_restored':True,'system':system,'gateway_auth_and_jobs_untouched':True}))
def rehearse():
 fixture='http {\n  client_max_body_size 64k;\n  server {\n    location / {\n      proxy_pass http://127.0.0.1:8844;\n    }\n  }\n}\n'
 changed=patch(fixture);assert changed.replace(UPLOAD_LOCATION,'')==fixture and patch(changed)==changed
 assert changed.count('client_max_body_size 64k;')==1 and changed.count('client_max_body_size 512k;')==1
 print(json.dumps({'policy_and_rollback_rehearsed':True,'ordinary_limit_unchanged':True,'video_route_only':True}))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['rehearse','apply-user','apply-system','rollback-user','rollback-system']);a.add_argument('--change',type=pathlib.Path);v=a.parse_args()
 if v.action=='rehearse':rehearse()
 else:
  if not v.change:a.error('--change required')
  (install if v.action.startswith('apply') else rollback)(v.change,v.action.endswith('system'))
