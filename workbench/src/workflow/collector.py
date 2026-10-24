#!/usr/bin/env python3
"""Read-only local workflow inventory; fixed sources, aggregate metrics, no business writes."""
import concurrent.futures, datetime as dt, hashlib, json, math, os, pathlib, plistlib, re, sqlite3, subprocess, sys, urllib.request
from receipts import read_receipts
ROOT = pathlib.Path(__file__).parent
UTC = dt.timezone.utc
CST = dt.timezone(dt.timedelta(hours=8))
STATUSES = {'ok','running','degraded','paused','failed','error','unknown','idle','retrying','succeeded'}
METRICS = {'pending_count','pending_event_count','retryable_event_count','exhausted_event_count','execution_unknown_event_count','execution_unknown_count','dead_letter_count','oldest_pending_age_seconds','pending_notification_count','dependency_failure_count','issue_count','account_count','pending_source_count','pending_decision_count','pending_callback_count','callback_configured','worker_alive','polling_ok','cherry_api_ok','native_channel_disabled','execution_deferred','api_healthy','snapshot_verified','collection_performed'}
DATES = {'checked_at','updated_at','last_success_at','last_model_success','next_check_at','next_collection_at','started_at','finished_at'}
QUEUE_STATES = {'pending','sending','sent','failed','running','accepted','writing','succeeded','unknown','cancelled','ready','processed','completed','queued','claimed','baseline','ignored','blocked','retrying','verified','error','received','generated','consumed','attached','expired','dropped','suppressed'}

def parse_time(v):
    if not isinstance(v,str): return None
    try:
        x=dt.datetime.fromisoformat(v.replace('Z','+00:00'))
        return x.astimezone(UTC) if x.tzinfo else None
    except ValueError: return None

def read_json(p):
    if p.stat().st_size>2_000_000: raise ValueError('source_too_large')
    v=json.loads(p.read_text(encoding='utf8'))
    if not isinstance(v,dict): raise ValueError('source_shape_invalid')
    return v

def allowed_path(root,relative):
    if not isinstance(relative,str) or pathlib.PurePosixPath(relative).is_absolute() or '..' in pathlib.PurePosixPath(relative).parts: raise ValueError('source_path_invalid')
    p=(root/relative).resolve()
    if root.resolve() not in p.parents: raise ValueError('source_path_invalid')
    return p

def projection(raw):
    value={}
    for k in METRICS:
        v=raw.get(k)
        if isinstance(v,bool) or (isinstance(v,(int,float)) and math.isfinite(v) and v>=0): value[k]=v
    for k in DATES:
        if parse_time(raw.get(k)): value[k]=raw[k]
    for k in ['status','collection_status','notification_status','power_state','primary_error_code']:
        if isinstance(raw.get(k),str) and raw[k] in STATUSES|{'awake','dark_wake','sleep','runtime_error','polling_stale','dependency_unavailable','execution_unknown','healthy'}: value[k]=raw[k]
    return value

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ValueError('health_redirect_denied')

def normalize_health(definition,raw):
    value=dict(raw)
    if definition.get('statusField'):value['status']=raw.get(definition['statusField'])
    if definition.get('statusContract')=='poll-counter-v1':
        n=raw.get('consecutive_failures')
        value['status']='unknown' if not isinstance(n,int) or isinstance(n,bool) else 'failed' if n>0 or raw.get('last_error') else 'ok'
    return value

def launch_probe(label,uid):
    try:
        r=subprocess.run(['/bin/launchctl','print',f'gui/{uid}/{label}'],capture_output=True,text=True,timeout=3)
        if r.returncode:
            missing='Could not find service' in r.stderr
            return {'state':'not_loaded' if missing else 'unknown','loaded':False if missing else None,'exitStatus':r.returncode}
        v={'loaded':True,'state':'unknown','exitStatus':0}
        for k,key in [('state','state'),('pid','pid'),('last exit code','lastExitCode')]:
            m=re.search(r'^\s*'+k+r' = (.+)$',r.stdout,re.M)
            if m: v[key]=int(m[1]) if re.fullmatch(r'-?\d+',m[1]) else m[1] if k=='last exit code' else m[1] if m[1] in ['running','not running','waiting','spawn scheduled'] else 'unknown'
        return v
    except (OSError,subprocess.TimeoutExpired): return {'loaded':None,'state':'unknown','error':'LAUNCHCTL_UNAVAILABLE'}

def schedule(spec,now):
    calendar=spec.get('StartCalendarInterval'); interval=spec.get('StartInterval')
    if spec.get('KeepAlive'): return {'kind':'continuous','label':'持续运行','nextAt':None}
    if calendar:
        slots=calendar if isinstance(calendar,list) else [calendar]; best=None
        for i in range(8*24*60):
            t=now.astimezone(CST).replace(second=0,microsecond=0)+dt.timedelta(minutes=i+1)
            for s in slots:
                if not isinstance(s,dict): continue
                checks={'Minute':t.minute,'Hour':t.hour,'Day':t.day,'Month':t.month,'Weekday':(t.weekday()+1)%7}
                if all(checks.get(k)==v or k=='Weekday' and v==7 and checks[k]==0 for k,v in s.items()): best=t; break
            if best: break
        return {'kind':'calendar','label':'日历调度','calendar':slots,'nextAt':best.isoformat() if best else None,'estimated':True,'interval':interval}
    if isinstance(interval,int) and interval>0: return {'kind':'interval','label':f'每 {interval} 秒','interval':interval,'nextAt':None,'note':'launchd 无精确下次时刻；休眠与唤醒会影响实际运行'}
    return {'kind':'event','label':'事件 / 手动','nextAt':None}

def queue_probe(base,rel,table,column='state'):
    if not re.fullmatch('[a-z_]+',table) or column not in {'state','status'}: raise ValueError('queue_contract_invalid')
    p=allowed_path(base,rel)
    if not p.exists(): return {'source':rel,'table':table,'state':'missing','counts':{}}
    c=None
    try:
        c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.4); c.execute('PRAGMA query_only=ON')
        columns={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
        if column not in columns: return {'source':rel,'table':table,'state':'schema_mismatch','counts':{}}
        start=dt.datetime.now(UTC); c.set_progress_handler(lambda:1 if dt.datetime.now(UTC)-start>dt.timedelta(seconds=1) else 0,1000)
        counts={}
        for status,n in c.execute('SELECT '+column+',count(*) FROM '+table+' GROUP BY '+column):
            key=status if status in QUEUE_STATES else 'other'; counts[key]=counts.get(key,0)+n
        times={}
        for k in ['updated_at','created_at','finished_at','sent_at','remote_status_verified_at']:
            if k in columns:
                x=c.execute('SELECT MAX('+k+') FROM '+table).fetchone()[0]
                if parse_time(x): times[k]=x
        return {'source':rel,'table':table,'state':'read','counts':counts,'times':times}
    except (OSError,sqlite3.Error,ValueError): return {'source':rel,'table':table,'state':'unavailable','counts':{}}
    finally:
        if c: c.close()

def collect(home=None,catalog=None,launch=launch_probe,now=None,studio_root=None):
    home=pathlib.Path(home or pathlib.Path.home()); base=pathlib.Path(os.environ.get('CONTENTBASE_RUNTIME_ROOT',str(home/'.local/share/content-company-workbench/workflows'))); agents=base/'LaunchAgents'; now=now or dt.datetime.now(UTC)
    studio_root=pathlib.Path(studio_root) if studio_root else base/'ERC Studio'
    if base.resolve() not in studio_root.resolve().parents:raise ValueError('studio_root_denied')
    catalog=catalog or read_json(ROOT/'catalog.json')
    if catalog.get('schemaVersion')!=1: raise ValueError('catalog_version_invalid')
    specs={}; discovery_errors=[]
    for p in sorted(agents.glob('*.plist')):
        try:
            if p.stat().st_size>262144: continue
            d=plistlib.loads(p.read_bytes()); label=d.get('Label','')
            if isinstance(label,str) and any(label.startswith(prefix) for prefix in catalog['discoveryPrefixes']): specs[label]=(p,d)
        except (OSError,ValueError,plistlib.InvalidFileException): discovery_errors.append('PLIST_INVALID')
    defs=[dict(x) for x in catalog['components']]; known={x.get('label') for x in defs}
    for label in sorted(specs):
        if label not in known: defs.append({'id':'discovered:'+label,'name':label.removeprefix('org.contentbase.').removeprefix('com.contentbase.').removeprefix('com.example.'),'label':label,'kind':'service','group':'外部集成' if 'diao' in label or 'public-sync' in label else '待维护登记','source':'本机 LaunchAgents','dependencies':[],'discovered':True})
    labels={x.get('label') for x in defs if x.get('label')}; launches={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(launch,label,os.getuid()):label for label in labels}
        for f in concurrent.futures.as_completed(futures):
            try: launches[futures[f]]=f.result()
            except Exception: launches[futures[f]]={'state':'unknown','loaded':None,'error':'LAUNCHCTL_UNAVAILABLE'}
    try: supervisor=read_json(allowed_path(base,catalog['supervisor']))
    except (OSError,ValueError): supervisor={}
    supertime=parse_time(supervisor.get('checked_at')); superfresh=bool(supertime and -30<=(now-supertime).total_seconds()<=900)
    probes={p.get('id'):p for p in (supervisor.get('probes') if isinstance(supervisor.get('probes'),list) else []) if isinstance(p,dict)}; issue_codes={}
    for i in supervisor.get('issues') if isinstance(supervisor.get('issues'),list) else []:
        if isinstance(i,dict) and re.fullmatch('[A-Z_]{2,64}',str(i.get('code',''))): issue_codes.setdefault(i.get('service_id'),[]).append(i['code'])
    items=[]
    for d in defs:
        item={k:d[k] for k in ['id','name','kind','group','source','dependencies','discovered'] if k in d}; item['dependencies']=d.get('dependencies',[])
        label=d.get('label'); pl=specs.get(label,(None,{}))[1]; lp=launches.get(label,{})
        item['launchd']={'label':label,**lp}; item['schedule']=schedule(pl,now); item['evidence']=[]; codes=[]; status='unknown'
        if lp.get('loaded') is False: codes.append('LAUNCHD_NOT_LOADED'); status='attention'
        elif lp.get('loaded') is None and label: codes.append('LAUNCHCTL_UNAVAILABLE')
        if pl.get('KeepAlive') and lp.get('loaded') is True and lp.get('state')!='running': codes.append('CONTINUOUS_SERVICE_STOPPED'); status='attention'
        if isinstance(lp.get('lastExitCode'),int) and lp['lastExitCode']!=0 and lp.get('state')!='running': codes.append('LAST_EXIT_NONZERO'); status='attention'
        if lp.get('loaded') is True: item['evidence'].append('LaunchAgent 已加载 · '+str(lp.get('state')))
        codefiles=[]
        for a in pl.get('ProgramArguments',[]):
            if isinstance(a,str) and a.endswith(('.py','.cjs','.mjs','.js','.sh')) and os.path.isabs(a):
                p=pathlib.Path(a)
                try:
                    if p.is_file() and p.stat().st_size<2_000_000:codefiles.append({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'state':'read'})
                except OSError:
                    # LaunchAgent children can lack macOS access to another project's
                    # Desktop source. One optional hash must not erase every workflow.
                    codefiles.append({'path':str(p),'sha256':None,'state':'unavailable'})
        item['runtimeCode']=codefiles; raw={}; health={}; has_health=False
        if d.get('healthPath'):
            health={'source':d['healthPath'],'state':'missing'}
            try:
                raw=normalize_health(d,read_json(allowed_path(base,d['healthPath']))); health={'source':d['healthPath'],'state':'read','metrics':projection(raw)}; has_health=True
                timestamp=parse_time(raw.get(d.get('timeField','checked_at'))); age=(now-timestamp).total_seconds() if timestamp else None
                health['timestamp']=timestamp.isoformat() if timestamp else None; health['ageSeconds']=int(age) if age is not None else None
                if age is None: codes.append('HEALTH_TIME_INVALID')
                elif age < -30: codes.append('HEALTH_FROM_FUTURE')
                elif age>d.get('maxAge',3600): codes.append('HEALTH_STALE')
                if raw.get('status') not in d.get('statuses',['ok']): codes.append('HEALTH_STATUS_ATTENTION')
                if any(raw.get(k) for k in d.get('errors',['error','fatal_error'])): codes.append('HEALTH_ERROR_PRESENT')
                if not codes: status='healthy'
                item['evidence'].append('health：'+str(raw.get('status') if isinstance(raw.get('status'),str) and raw['status'] in STATUSES else '未知'))
            except (OSError,ValueError): codes.append('HEALTH_MISSING_OR_INVALID')
        if d.get('http'):
            health={'source':d['http'],'state':'unavailable'}
            try:
                if d['http'] not in {'http://127.0.0.1:8765/api/health','http://127.0.0.1:8844/health','http://127.0.0.1:8787/healthz'}: raise ValueError('http_source_denied')
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
                with opener.open(d['http'],timeout=2) as response:
                    blob=response.read(131073)
                    if len(blob)>131072: raise ValueError('http_source_too_large')
                raw=json.loads(blob)
                if not isinstance(raw,dict):raise ValueError('http_shape_invalid')
                if isinstance(raw.get('data'),dict): raw=raw['data']
                if raw.get('status')!='ok': codes.append('HTTP_HEALTH_ATTENTION')
                if raw.get('worker_alive') is False: codes.append('WORKER_STOPPED')
                if any(raw.get(k) for k in ['review_notifications_error','bar_reports_error']): codes.append('WORKBENCH_COMPONENT_ERROR')
                health={'source':d['http'],'state':'read','timestamp':now.isoformat(),'metrics':projection(raw)}; has_health=True
                if not codes: status='healthy'
                item['evidence'].append('本机 health HTTP 已返回')
            except Exception: codes.append('HTTP_HEALTH_UNAVAILABLE')
        for k in ['dead_letter_count','dependency_failure_count','execution_unknown_event_count','execution_unknown_count','exhausted_event_count']:
            if isinstance(raw.get(k),int) and raw[k]>0: codes.append(k.upper())
        if raw.get('collection_status') in ['unknown','failed','error','retrying']: codes.append('COLLECTION_NOT_CURRENT')
        if isinstance(raw.get('issue_count'),int) and raw['issue_count']>0: codes.append('BUSINESS_ISSUES')
        if d['id']=='wechat' and isinstance(raw.get('metrics'),dict):
            health['receiptMetrics']={k:v for k,v in raw['metrics'].items() if k in {'total','pending','processing','retry','failed','done'} and isinstance(v,int) and not isinstance(v,bool) and v>=0}
            if health['receiptMetrics'].get('failed',0)>0:codes.append('WECHAT_REPLY_FAILURES')
            if raw.get('callback_configured') is False:codes.append('CALLBACK_NOT_CONFIGURED')
        item['health']=health; probe=probes.get(d.get('supervisorId'))
        if probe:
            item['supervisor']={'observedAt':supervisor.get('checked_at'),'fresh':superfresh,'healthy':probe.get('healthy') is True,'codes':issue_codes.get(d['supervisorId'],[])}
            if superfresh and probe.get('healthy') is False: codes.extend(issue_codes.get(d['supervisorId'],[]) or ['SUPERVISOR_ATTENTION'])
            elif superfresh and probe.get('healthy') is True:
                if not has_health and not codes: status='healthy'; item['evidence'].append('守护器健康检查通过')
                if 'HEALTH_STALE' in codes and probe.get('health',{}).get('stale_suppressed') is True:
                    codes.remove('HEALTH_STALE'); item['evidence'].append('守护器处于有界唤醒/运行宽限'); status='waiting'
        if codes: status='unknown' if all(c in ['HEALTH_MISSING_OR_INVALID','HTTP_HEALTH_UNAVAILABLE','LAUNCHCTL_UNAVAILABLE','HEALTH_TIME_INVALID'] for c in codes) else 'attention'
        item['status']=status; item['codes']=list(dict.fromkeys(codes)); item['observedAt']=now.isoformat()
        item['business']={'status':'unverified','note':'技术健康不等于某次业务已成功；详情仅展示来源回执的聚合状态'}
        if d.get('receipts'):
            item['receipts']=read_receipts(base,d['receipts'],allowed_path,parse_time,studio_root)
            if any((r.get('attentionCount') or 0)>0 for r in item['receipts']):item['status']='attention';item['codes'].append('RECEIPT_ATTENTION')
        items.append(item)
    byid={x['id']:x for x in items}; queues=[]
    for folder in sorted(base.glob('ContentBase*')):
        for p in list(folder.glob('notifications.db'))+list(folder.glob('notification-center/notifications.db')):
            queues.append(queue_probe(base,str(p.relative_to(base)),'notifications'))
    qspec=[('workbench','ContentBaseWorkbench/workbench.db','agent_requests','state'),('workbench','ContentBaseWorkbench/workbench.db','image_jobs','state'),('workbench','ContentBaseWorkbench/workbench.db','image_source_outbox','state'),('workbench','ContentBaseWorkbench/workbench.db','intake_receipts','state'),('workbench','ContentBaseWorkbench/workbench.db','review_notices','state'),('daily_task','ContentBaseDailyTask/daily_task_checkin.db','report_runs','status'),('note_processor','ContentBaseNoteTopicLinker/note_watcher.db','selection_decisions','status')]
    for id,rel,table,column in qspec:
        if id not in byid: continue
        q=queue_probe(base,rel,table,column); byid[id].setdefault('queues',[]).append(q)
        if q['state']=='read' and q['counts'].get('unknown',0)>0: byid[id]['status']='attention'; byid[id]['codes'].append('QUEUE_UNKNOWN_RESULTS')
        if q['state']!='read': byid[id]['evidence'].append('队列证据 '+q['state'])
    ns='attention' if any(q['counts'].get('failed',0)+q['counts'].get('unknown',0)>0 for q in queues) else 'healthy' if queues and all(q['state']=='read' for q in queues) else 'unknown'
    items.append({'id':'notifications','name':'统一通知中心','kind':'service','group':'基础设施','source':'integrations/notification-center','status':ns,'dependencies':[],'queues':queues,'codes':[] if ns=='healthy' else ['NOTIFICATION_QUEUE_ATTENTION'],'schedule':{'kind':'event','label':'由各来源周期投递','nextAt':None},'launchd':{},'health':{},'observedAt':now.isoformat(),'evidence':[f'{len(queues)} 个来源队列 · 聚合只读'],'business':{'status':'unverified','note':'sent 表示保存了原通知回执，不等于本人已读'}})
    byid['notifications']=items[-1]
    for r in catalog['routes']:
        x=dict(r); x['dependencyStates']=[{'id':i,'name':byid[i]['name'] if i in byid else i,'status':byid[i]['status'] if i in byid else 'unknown'} for i in r['dependencies']]
        x['status']='attention' if any(i['status']=='attention' for i in x['dependencyStates']) else 'waiting' if x['dependencyStates'] and all(i['status'] in ['healthy','waiting'] for i in x['dependencyStates']) else 'unknown'
        x['schedule']={'kind':'event','label':'按原触发条件','nextAt':None}; x['observedAt']=now.isoformat(); x['codes']=[]; x['evidence']=['依赖已监控 · 单次业务未验证'] if x['dependencyStates'] else ['App 按需流程 · 尚无独立运行回执']; x['health']={}; x['launchd']={}; x['business']={'status':'unverified','note':r.get('receipt_boundary',r['verification'])}
        x['receipts']=read_receipts(base,r.get('receipts',[]),allowed_path,parse_time,studio_root)
        if r.get('routeId')=='wechat-official-reply':
            origin=byid.get('wechat',{});metrics=origin.get('health',{}).get('receiptMetrics')
            if metrics is not None:x['receipts']=[{'source':origin['health']['source'],'table':'metrics','state':'read','counts':metrics,'total':metrics.get('total'),'activeCount':sum(metrics.get(k,0) for k in ['pending','processing','retry']),'attentionCount':metrics.get('failed'),'latestAt':origin['health'].get('timestamp'),'successMeaning':'原公众号处理计数，不代表重新向访客发送'}]
        readable=[q for q in x['receipts'] if q['state']=='read']
        if readable:
            x['business']['status']='recorded';x['evidence']=['原技术回执 '+str(sum(q.get('total') or 0 for q in readable))+' 条 · '+str(sum(q.get('activeCount') or 0 for q in readable))+' 项在途']
        if any((q.get('attentionCount') or 0)>0 for q in readable):x['status']='attention';x['codes'].append('RECEIPT_ATTENTION')
        if x['receipts'] and len(readable)!=len(x['receipts']):x['codes'].append('RECEIPT_EVIDENCE_INCOMPLETE');x['business']['status']='partial'
        if not x['dependencyStates'] and readable and len(readable)==len(x['receipts']) and not x['codes']:x['status']='waiting'
        items.append(x)
    for item in items: item['codes']=list(dict.fromkeys(item['codes']))
    runtime_registry={'state':'missing','source':'CherryFeishuBridge/agent-runtime/workflow-registry.json'}
    try:
        path=allowed_path(base,runtime_registry['source']);deployed=read_json(path)
        if not isinstance(deployed.get('routes'),list):raise ValueError('registry_shape_invalid')
        deployed_routes={r['id']:r for r in deployed['routes'] if isinstance(r,dict) and isinstance(r.get('id'),str)}
        projected={r['routeId']:r for r in catalog['routes']};drift=[]
        for id,r in projected.items():
            if id not in deployed_routes or any(r.get(k)!=deployed_routes[id].get(k) for k in ['name','trigger','action','boundary']):drift.append(id)
        runtime_registry.update(state='read',version=deployed.get('registry_version') if isinstance(deployed.get('registry_version'),str) else None,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),routeCount=len(deployed_routes),metadataDriftRouteIds=drift,unregisteredRouteIds=sorted(set(deployed_routes)-set(projected)))
    except (OSError,ValueError,TypeError):pass
    receipts_read=sum(bool(x.get('receipts')) and all(q['state']=='read' for q in x['receipts']) for x in items if x['kind']=='workflow')
    return {'schemaVersion':1,'catalogVersion':catalog['version'],'registryVersion':catalog['registryVersion'],'observedAt':now.isoformat(),'readOnly':True,'items':items,'summary':{s:sum(i['status']==s for i in items) for s in ['healthy','attention','unknown','waiting']},'coverage':{'catalogServices':len(catalog['components'])+1,'discoveredServices':sum(bool(i.get('discovered')) for i in items),'workflowRoutes':len(catalog['routes']),'routesWithReadableReceipts':receipts_read,'launchAgentCount':len(specs),'unregisteredLabels':sorted(set(specs)-known),'discoveryErrors':discovery_errors,'runtimeRegistry':runtime_registry,'supervisorFresh':superfresh,'note':'发现到的 LaunchAgent 自动纳入；原技术回执不等于重新核验外部业务结果'},'partial':bool(discovery_errors)}

if __name__=='__main__':
    try:
        request=json.loads(sys.stdin.read(4096) or '{}')
        if not isinstance(request,dict) or set(request)-{'operation','studioStateRoot'} or request.get('operation','snapshot')!='snapshot': raise ValueError('request_invalid')
        print(json.dumps({'ok':True,'data':collect(studio_root=request.get('studioStateRoot'))},ensure_ascii=False,allow_nan=False))
    except Exception as e:
        print(json.dumps({'ok':False,'error':{'code':'collector_failed','type':type(e).__name__}},ensure_ascii=False)); sys.exit(1)
