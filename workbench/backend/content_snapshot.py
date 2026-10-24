"""Deduplicated live content reads for the authenticated browser gateway."""
from __future__ import annotations
import copy,json,os,subprocess,threading
from pathlib import Path

NODE='/usr/local/bin/node'
class ContentRefreshError(RuntimeError):pass

class ContentRefresher:
 def __init__(self,config,run=subprocess.run,script=None):
  self.root=Path(config.get('workspace_state_dir','')).expanduser().resolve()
  self.script=Path(script or Path(__file__).with_name('content_refresh.cjs')).resolve()
  self.run=run;self.condition=threading.Condition();self.running=False;self.last=None;self.error=None
 def _validate(self,value):
  if not isinstance(value,dict) or value.get('phase')!='ok' or value.get('stale') is not False or value.get('refreshing') is not False:raise ContentRefreshError('content_refresh_protocol_invalid')
  if not isinstance(value.get('readAt'),str) or not isinstance(value.get('topics'),list) or not isinstance(value.get('projects'),list) or len(value['topics'])>5000 or len(value['projects'])>5000:raise ContentRefreshError('content_refresh_protocol_invalid')
  if any(not isinstance(x,dict) or not isinstance(x.get('id'),str) for x in value['topics']+value['projects']):raise ContentRefreshError('content_refresh_protocol_invalid')
  return value
 def _execute(self):
  env={k:os.environ[k] for k in ('HOME','USER','TMPDIR') if k in os.environ};env.update(PATH='/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin',LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1',LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
  try:result=self.run([NODE,str(self.script),str(self.root)],text=True,capture_output=True,timeout=45,stdin=subprocess.DEVNULL,env=env)
  except (OSError,subprocess.TimeoutExpired):raise ContentRefreshError('content_refresh_unavailable') from None
  raw=result.stdout if result.returncode==0 else result.stderr
  if len(raw)>4_000_000:raise ContentRefreshError('content_refresh_protocol_invalid')
  try:value=json.loads(raw)
  except (ValueError,UnicodeError):raise ContentRefreshError('content_refresh_protocol_invalid') from None
  if result.returncode:
   code=value.get('error') if isinstance(value,dict) else None
   raise ContentRefreshError(code if isinstance(code,str) and code.startswith('content_') else 'content_refresh_failed')
  return self._validate(value)
 def refresh(self):
  with self.condition:
   if self.running:
    if not self.condition.wait_for(lambda:not self.running,timeout=50):raise ContentRefreshError('content_refresh_unavailable')
    if self.error:raise ContentRefreshError(self.error)
    return copy.deepcopy(self.last)
   self.running=True;self.error=None
  try:value=self._execute()
  except ContentRefreshError as error:
   with self.condition:self.error=str(error);self.running=False;self.condition.notify_all()
   raise
  with self.condition:self.last=value;self.running=False;self.condition.notify_all()
  return copy.deepcopy(value)
