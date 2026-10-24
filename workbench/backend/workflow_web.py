"""Authenticated-member browser transport for the read-only workflow collector."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from core import Problem

def collector_path():
    source=Path(__file__).resolve().parents[1]/'src/workflow/collector.py'
    return source if source.is_file() else Path(__file__).parent/'workflow-monitor/collector.py'

def browser_projection(value):
    """Keep technical evidence, strip absolute host paths before network delivery."""
    value=copy.deepcopy(value)
    for item in value.get('items',[]):
        for field in ['health','launchd']:
            container=item.get(field)
            if isinstance(container,dict) and isinstance(container.get('source'),str) and container['source'].startswith('/'):
                container['source']=Path(container['source']).name
        for q in item.get('queues',[])+item.get('receipts',[]):
            if isinstance(q.get('source'),str) and q['source'].startswith('/'):q['source']=Path(q['source']).name
        for code in item.get('runtimeCode',[]):
            if isinstance(code.get('path'),str):code['path']=Path(code['path']).name
    return value

class WorkflowWebMonitor:
    def __init__(self,config,run=None,clock=time.monotonic):
        self.config=config
        self.clock=clock
        self.run=run or self.collect
        self.lock=threading.Lock()
        self.last=-float('inf')
        self.value=None
        self.history=[]
        self.cache=Path(config['state_dir'])/'workflow-observation.json'
        # Cache is a technical observation, never a second business ledger.
        try:
            if self.cache.is_symlink() or self.cache.stat().st_size>4_000_000:raise ValueError()
            raw=json.loads(self.cache.read_text())
            if raw.get('version')==1 and self.valid(raw.get('data')):
                self.value=raw['data'];self.history=raw.get('history',[])[-200:]
        except (OSError,ValueError,TypeError):pass

    @staticmethod
    def valid(value):
        return isinstance(value,dict) and value.get('schemaVersion')==1 and isinstance(value.get('items'),list) and len(value['items'])<=1000 and isinstance(value.get('summary'),dict) and isinstance(value.get('coverage'),dict)

    def collect(self):
        root=Path(self.config.get('workspace_state_dir','')).resolve()
        expected=(Path.home()/'Library/Application Support/ERC Studio').resolve()
        if root!=expected or not root.is_dir():raise ValueError('workflow_state_root_invalid')
        result=subprocess.run([sys.executable,str(collector_path())],input=json.dumps({'operation':'snapshot','studioStateRoot':str(root)}),text=True,capture_output=True,timeout=25,
            env={'HOME':str(Path.home()),'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'})
        if result.returncode:
            try:
                kind=json.loads(result.stdout).get('error',{}).get('type')
                if kind in {'PermissionError','FileNotFoundError','ValueError','TypeError','KeyError'}:print('workflow_collector_error_type:'+kind,flush=True,file=sys.stderr)
            except (ValueError,TypeError):pass
            raise ValueError('workflow_collector_failed')
        if len(result.stdout)>3_000_000:raise ValueError('workflow_collector_failed')
        response=json.loads(result.stdout)
        if response.get('ok') is not True or not self.valid(response.get('data')):raise ValueError('workflow_protocol_invalid')
        return browser_projection(response['data'])

    def snapshot(self,refresh=False):
        with self.lock:
            now=self.clock()
            if self.value is not None and now-self.last<(5 if refresh else 30):return {'ok':True,'phase':'cached','data':self.value,'history':self.history,'readOnly':True}
            try:
                value=self.run()
                if not self.valid(value):raise ValueError('workflow_protocol_invalid')
                previous={x['id']:x.get('status') for x in (self.value or {}).get('items',[])}
                for item in value['items']:
                    if item.get('id') in previous and item.get('status')!=previous[item['id']]:self.history.append({'id':item['id'],'from':previous[item['id']],'to':item.get('status'),'at':value.get('observedAt')})
                self.history=self.history[-200:];self.value=value;self.last=now
                self.cache.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                if self.cache.is_symlink():raise ValueError('workflow_cache_symlink')
                temp=self.cache.with_suffix('.tmp');temp.write_text(json.dumps({'version':1,'data':value,'history':self.history},ensure_ascii=False));temp.chmod(0o600);temp.replace(self.cache)
                return {'ok':True,'phase':'live','data':value,'history':self.history,'readOnly':True}
            except (OSError,ValueError,TypeError,subprocess.SubprocessError) as error:
                safe_codes={'workflow_state_root_invalid','workflow_collector_failed','workflow_protocol_invalid','workflow_cache_symlink'}
                reason=str(error) if str(error) in safe_codes else type(error).__name__
                print('workflow_observation_failed:'+reason,flush=True,file=sys.stderr)
                return {'ok':False,'phase':'stale' if self.value is not None else 'error','error':'workflow_source_unavailable',
                    **({'data':self.value,'history':self.history} if self.value is not None else {}),'readOnly':True}

INIT_LOCK=threading.Lock()
def workflow_snapshot(service,owner,query):
    # Existing authenticated, active workspace members share technical observations.
    # The HTTP handler authenticates owner; no caller-selected identity is accepted.
    service.member(owner)
    if set(query)-{'refresh'} or ('refresh' in query and query['refresh']!=['1']):raise Problem(400,'invalid_request')
    with INIT_LOCK:
        if not hasattr(service,'workflow_monitor'):service.workflow_monitor=WorkflowWebMonitor(service.config)
    return service.workflow_monitor.snapshot(refresh=query.get('refresh')==['1'])
