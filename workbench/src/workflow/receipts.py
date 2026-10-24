"""Read-only receipt adapters. Never import an executor or select business payloads."""
import datetime as dt
import json
import math
import re
import sqlite3
import time

STATES = {'pending','sending','sent','failed','running','accepted','writing','succeeded','unknown','cancelled','ready','processed','completed','queued','claimed','baseline','ignored','blocked','retrying','verified','error','received','generated','consumed','attached','expired','dropped','suppressed','awaiting_generation','awaiting_publish','published','seen','skipped','extracting','drafting','preview_notifying','notification_failed','publishing','publish_failed','cancel_requested','processing','waiting','received_pending_confirmation','open','paid','confirmed','not_attended','attended','planned','reading','verifying','backfilling','backfill_queued','backfill_failed'}
ACTIVE = {'pending','sending','running','accepted','writing','queued','claimed','processing','reading','verifying','backfilling','backfill_queued','publishing','extracting','drafting','preview_notifying','cancel_requested'}
ATTENTION = {'unknown','failed','error','publish_failed','notification_failed','backfill_failed'}
SUCCESS = {'sent','succeeded','processed','completed','published','paid','attached','ready'}
WHERE_COLUMNS = {'source','action_id','workflow_id','active_workflow_id','kind'}
TIME_COLUMNS = {'updated_at','created_at','finished_at','sent_at','remote_status_verified_at','evaluated_at','completed_at','received_at','created','updated'}

def timestamp(v,parse_time):
    if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v):
        try:return dt.datetime.fromtimestamp(v,dt.timezone.utc).isoformat()
        except (ValueError,OverflowError,OSError):return None
    t=parse_time(v)
    return t.isoformat() if t else None

def sql_receipt(base,spec,path_for,parse_time):
    table=spec.get('table');column=spec.get('column','state');mode=spec.get('mode','state');when=spec.get('timeField','updated_at');where=spec.get('where',{})
    if not isinstance(table,str) or not re.fullmatch('[a-z_]+',table) or column not in {'state','status','attendance_status'} or mode not in {'count','state'} or when not in TIME_COLUMNS or not isinstance(where,dict) or set(where)-WHERE_COLUMNS:raise ValueError('receipt_contract_invalid')
    if any(not isinstance(v,str) or len(v)>100 for v in where.values()):raise ValueError('receipt_filter_invalid')
    source=spec['path'];p=path_for(base,source);result={'source':source,'table':table,'state':'missing','counts':{},'activeCount':None,'attentionCount':None,'total':None,'latestAt':None,'scope':where}
    if not p.exists():return result
    connection=None
    try:
        connection=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.4);connection.execute('PRAGMA query_only=ON');connection.execute('BEGIN')
        started=time.monotonic();connection.set_progress_handler(lambda:int(time.monotonic()-started>1.5),1000)
        columns={r[1] for r in connection.execute('PRAGMA table_info('+table+')')}
        if not columns or (mode=='state' and column not in columns) or set(where)-columns or when not in columns:return {**result,'state':'schema_mismatch'}
        predicate=(' WHERE '+' AND '.join(k+'=?' for k in where)) if where else '';args=list(where.values())
        counts={}
        if mode=='state':
            for state,n in connection.execute('SELECT '+column+',count(*) FROM '+table+predicate+' GROUP BY '+column,args):
                safe=state if isinstance(state,str) and state in STATES else 'other';counts[safe]=counts.get(safe,0)+n
        else:counts['recorded']=connection.execute('SELECT count(*) FROM '+table+predicate,args).fetchone()[0]
        # Snapshot semantics: all counts and times from one read transaction.
        last=connection.execute('SELECT MAX('+when+') FROM '+table+predicate,args).fetchone()[0]
        attention=sum(n for state,n in counts.items() if state in ATTENTION)
        active=sum(n for state,n in counts.items() if state in ACTIVE)
        success_time=None
        if mode=='state' and counts:
            placeholders=','.join('?' for _ in SUCCESS);sql='SELECT MAX('+when+') FROM '+table+predicate+(' AND ' if where else ' WHERE ')+column+' IN ('+placeholders+')'
            success_time=timestamp(connection.execute(sql,args+sorted(SUCCESS)).fetchone()[0],parse_time)
        expired_attention=0
        if table=='notifications' and 'expires_at' in columns:
            now=dt.datetime.now(dt.timezone.utc)
            for state,expires,n in connection.execute('SELECT state,expires_at,count(*) FROM '+table+predicate+' GROUP BY state,expires_at',args):
                expiration=parse_time(expires)
                if state in ATTENTION and expiration and expiration<now:expired_attention+=n
            attention=max(0,attention-expired_attention)
        return {**result,'state':'read','counts':counts,'total':sum(counts.values()),'activeCount':active,'attentionCount':attention,'historicalExpiredAttention':expired_attention,'latestAt':timestamp(last,parse_time),'lastRecordedSuccessAt':success_time,'successMeaning':'原技术状态库记录；未重新核验外部业务结果'}
    except (OSError,ValueError,sqlite3.Error):return {**result,'state':'unavailable'}
    finally:
        if connection:connection.close()

def json_receipt(base,spec,path_for,parse_time,studio_root):
    mode=spec['mode'];filename=spec.get('file');root=studio_root if mode=='studio-json' else base
    p=path_for(root,filename);result={'source':str(p) if mode=='studio-json' else filename,'table':'technical_jobs','state':'missing','counts':{},'total':None,'activeCount':None,'attentionCount':None,'latestAt':None}
    if not p.exists():return result
    try:
        if p.stat().st_size>4_000_000:raise ValueError('receipt_size_exceeded')
        raw=json.loads(p.read_text());jobs=list(raw.get('jobs',{}).values()) if mode=='bar-json' and isinstance(raw,dict) and isinstance(raw.get('jobs'),dict) else raw if isinstance(raw,list) else None
        if jobs is None or len(jobs)>10000:raise ValueError('receipt_shape_invalid')
        counts={};latest=None;last_success=None
        for job in jobs:
            if not isinstance(job,dict):raise ValueError('job_shape_invalid')
            if spec.get('kind') and job.get('kind')!=spec['kind']:continue
            s=job.get('state');key=s if isinstance(s,str) and s in STATES else 'other';counts[key]=counts.get(key,0)+1
            t=timestamp(job.get('finishedAt') or job.get('updated_at') or job.get('startedAt') or job.get('created_at'),parse_time)
            if t and (latest is None or t>latest):latest=t
            if t and s in SUCCESS and (last_success is None or t>last_success):last_success=t
        return {**result,'state':'read','counts':counts,'total':sum(counts.values()),'activeCount':sum(n for s,n in counts.items() if s in ACTIVE),'attentionCount':sum(n for s,n in counts.items() if s in ATTENTION),'latestAt':latest,'lastRecordedSuccessAt':last_success,'successMeaning':'原技术任务状态；不读取正文、成员或目标业务数据'}
    except (OSError,ValueError,TypeError):return {**result,'state':'unavailable'}

def read_receipts(base,specs,path_for,parse_time,studio_root):
    results=[]
    for spec in specs:
        try:
            result=json_receipt(base,spec,path_for,parse_time,studio_root) if spec.get('mode') in {'studio-json','bar-json'} else sql_receipt(base,spec,path_for,parse_time)
        except (OSError,ValueError,TypeError):result={'state':'contract_invalid','source':spec.get('path') or spec.get('file'),'counts':{},'total':None,'activeCount':None,'attentionCount':None}
        results.append(result)
    return results
