"""Read-only yxer account metrics overlay; never edits account/browser state."""
import copy, datetime, hashlib, json, math, os, re, subprocess, threading, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from scientist_metrics import ScientistSupplement

CLI = 'yxer'
PLATFORMS = {'抖音':'douyin','小红书':'xiaohongshu','哔哩哔哩':'bilibili','B站':'bilibili','快手':'kuaishou','视频号':'channels','微信公众号':'wechat','公众号':'wechat'}
FIELDS = ('id','platform','name','group','owner','operator','avatar','fans','fansUpdatedAt','status','statusReason','checkedAt','source','createdAt','metadataPresent')

class MetricsError(Exception):
    pass

def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def parsed_number(value, signed=False):
    result={'value':None,'rawValue':value if isinstance(value,(str,int,float)) and not isinstance(value,bool) else None,
            'numberFormat':'unknown','approximate':False,'unit':None}
    if isinstance(value,bool) or value is None or not isinstance(value,(str,int,float)):return result
    if isinstance(value,(int,float)):
        if not math.isfinite(value):result['rawValue']=None;return result
        if value<0 and not signed or abs(value)>9007199254740991:return result
        result.update(value=int(value) if float(value).is_integer() else value,numberFormat='decimal',displayValue=None)
        return result
    raw=str(value).strip()
    if not raw or len(raw)>100:return result
    # Validate grouping before removing commas: "4,860" is exact; "4,86" is not.
    pattern=r'(-?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(万|亿|千|%|％|秒|分钟|小时|ms|min|s|h)?'
    match=re.fullmatch(pattern,raw)
    if not match or match[1] and not signed:return result
    try:
        n=Decimal(match[1]+match[2].replace(',','')+(match[3] or ''))
        suffix=match[4];multiplier={'千':1000,'万':10000,'亿':100000000}.get(suffix,1)
        n*=multiplier
        if not n.is_finite() or abs(n)>9007199254740991:return result
        result['value']=int(n) if n==n.to_integral_value() else float(n)
    except (InvalidOperation,OverflowError,ValueError):return result
    compact=suffix in ('千','万','亿')
    result.update(numberFormat='compact' if compact else 'percent' if suffix in ('%','％') else 'duration' if suffix else 'thousands' if ',' in match[2] else 'decimal',
                  approximate=compact,unit='%' if suffix in ('%','％') else None if compact else suffix,
                  displayValue=raw if suffix else None)
    return result

def numeric(value, signed=False):
    return parsed_number(value,signed)['value']

def updated(value):
    n=numeric(value)
    if n is None or not 946684800000<=n<=4102444800000:return None
    return datetime.datetime.fromtimestamp(n/1000,datetime.timezone.utc).isoformat()

def metric(key,label,value,source,at,update=None,segment=None,period=None):
    parsed=parsed_number(value,signed=key=='netFansCount')
    return {'key':key,'label':label,**parsed,'source':source,'sourceField':key,
            'fetchedAt':at,'updatedAt':update,'segment':segment,'period':period,'stale':bool(update and (datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(update)).total_seconds()>48*3600),'status':'available' if parsed['value'] is not None else 'missing'}

def project(local, account_rows, overview_by_platform, at, errors=None):
    """Join by platform + SHA256(yxer ID), never nickname or row order."""
    remote={};errors=errors or {}
    for x in account_rows:
        name=x.get('platformName');sid=x.get('id')
        if name not in PLATFORMS:continue
        if not isinstance(sid,str) or not sid:raise MetricsError('invalid_account_identity')
        k=(PLATFORMS[name],hashlib.sha256(sid.encode()).hexdigest())
        if k in remote:raise MetricsError('duplicate_account_identity')
        remote[k]=x
    overview={}
    for platform,rows in overview_by_platform.items():
        seen=set()
        for x in rows:
            sid=x.get('platformAccountId')
            if not isinstance(sid,str) or not sid:continue
            if sid in seen:raise MetricsError('duplicate_overview_identity')
            seen.add(sid);overview[(platform,hashlib.sha256(sid.encode()).hexdigest())]=x
    accounts=[]
    for item in local.get('accounts',[]):
        a={k:item.get(k) for k in FIELDS};key=(item.get('platform'),item.get('sourceKey'));row=remote.get(key);ov=overview.get(key)
        raw=row.get('fansTotal') if row else None
        fans=numeric(raw);total=metric('fansTotal','总粉丝',raw,'yxer.accounts.list',at)
        total['reason']='账号清单未返回该账号' if not row else '来源未提供平台更新时间'
        if fans==0:
            total.update(value=None,status='unverified',reason='账号清单返回0，缺少指标采集时间，尚未核实真实为零',reportedValue=0)
        metrics=[];overview_update=None;overview_error=errors.get(item.get('platform'))
        try:
            data=ov.get('overviewData') if ov else None
            if isinstance(data,str):data=json.loads(data)
            if data is not None and not isinstance(data,dict):raise ValueError()
            if data:
                overview_update=updated(data.get('updateTime'))
                value=data.get('value',{})
                if not isinstance(value,dict):raise ValueError()
                for segment in ('video','dynamic','article'):
                    values=value.get(segment,[])
                    if not isinstance(values,list):raise ValueError()
                    seen=set()
                    for entry in values:
                        if not isinstance(entry,dict):continue
                        k=entry.get('key');label=entry.get('name')
                        if not isinstance(k,str) or not re.fullmatch('[a-zA-Z][a-zA-Z0-9_]{0,79}',k) or k in seen:continue
                        if not isinstance(label,str) or not 1<=len(label)<=80:continue
                        seen.add(k);metrics.append(metric(k,label,entry.get('value'),'yxer.account-overviews',at,overview_update,segment))
                totals=[m for m in metrics if m['key']=='totalFansCount' and m['value'] is not None]
                if totals:
                    exact=[m for m in totals if not m.get('approximate')]
                    if exact:totals=exact
                    distinct={m['value'] for m in totals}
                    if len(distinct)==1:
                        chosen=totals[0]
                        if chosen.get('approximate') and fans not in (None,0) and not total.get('approximate'):
                            total.update(overviewReportedValue=chosen.get('rawValue'),reason='概览是缩写值，保留账号清单的精确整数；不将缩写回填为精确总数')
                        else:
                            total=copy.deepcopy(chosen);total.update(label='总粉丝',reason='来源明确标为总粉丝；不是净增粉丝或粉丝指数')
                            if not total.get('approximate') and fans not in (None,0,total['value']) and not parsed_number(raw).get('approximate'):total.update(status='conflict',reason='账号清单与数据概览总粉丝不一致，保留两份来源待核实',listReportedValue=fans)
                    else:total.update(value=None,status='conflict',reason='不同内容类型的总粉丝不一致，待核实')
        except (ValueError,TypeError,OverflowError):metrics=[];overview_error='invalid_overview'
        a['fans']=total['value'];a['fansUpdatedAt']=total.get('updatedAt') or total.get('fetchedAt')
        a['accountMetrics']={'totalFans':total,'overview':metrics,'overviewUpdatedAt':overview_update,
            'overviewStatus':'error' if overview_error else ('available' if metrics else 'missing'),
            'overviewError':overview_error,'period':None,'fetchedAt':at,'cached':False,
            'sourcePresent':row is not None,'sourceLoginStatus':row.get('status') if row else None,
            'backendSupplement':'缺失字段从科学家核心对应账号的只读接口补齐'}
        accounts.append(a)
    return accounts

def apply_display_policy(accounts):
    """User-confirmed UI alias; canonical source semantics remain untouched."""
    for a in accounts:
        m=a['accountMetrics'];m.pop('displayTotalFans',None)
        if a['platform']!='douyin' or m['totalFans']['value'] is not None:continue
        index=next((x for x in m['overview'] if x.get('key')=='fansIndex' and x.get('status')=='available' and isinstance(x.get('value'),int) and not isinstance(x.get('value'),bool)),None)
        if index:
            m['displayTotalFans']={**copy.deepcopy(index),'label':'总粉丝','key':'totalFansDisplay',
                'mappingPolicy':'douyin.fans_index_as_display_total.v1','mappingAuthority':'user_confirmed_2026-10-03',
                'originalMetricKey':'fans_index','originalMetricLabel':'粉丝指数','originalUnit':index.get('unit'),
                'reason':'按用户确认的业务展示映射；原始科学家指标与平台口径保留不变'}
    return accounts

def run_cli(args):
    try:
        p=subprocess.run([CLI,*args,'--json'],capture_output=True,timeout=18,
            env={**os.environ,'PATH':'/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin'})
        if p.returncode or len(p.stdout)>4*1024*1024:raise MetricsError('source_query_failed')
        packet=json.loads(p.stdout)
        if packet.get('ok') is not True:raise MetricsError('source_query_failed')
        return packet['data']
    except MetricsError:raise
    except subprocess.TimeoutExpired:raise MetricsError('source_timeout')
    except Exception:raise MetricsError('source_protocol_error')

def overview_pages(platform,run=run_cli):
    result=[]
    for page in range(1,11):
        data=run(['query','account-overviews','--platform',platform,'--size','100','--page',str(page)])
        if not isinstance(data,dict) or not isinstance(data.get('data'),list):raise MetricsError('invalid_overview')
        result.extend(data['data']);pages=data.get('totalPage')
        if not isinstance(pages,int) or isinstance(pages,bool) or pages<0:raise MetricsError('pagination_unknown')
        if page>=pages:return result
    raise MetricsError('pagination_limit')

class AccountsMetrics:
    def __init__(self,root,run=run_cli,clock=time.monotonic,supplement=None):
        self.root=Path(root);self.run=run;self.clock=clock;self.lock=threading.Lock();self.last=0;self.loaded=False;self.cache=None
        self.file=self.root/'account-metrics-cache.json'
        self.supplement=supplement or ScientistSupplement()
        try:
            if self.file.is_symlink() or self.file.stat().st_mode & 0o077:raise ValueError()
            self.cache=json.loads(self.file.read_text())
            if self.cache.get('version')!=1 or not isinstance(self.cache.get('accounts'),list):raise ValueError()
        except Exception:self.cache=None
    def overlay(self,local):
        cached={a['id']:a for a in (self.cache or {}).get('accounts',[])};out=[]
        for item in local.get('accounts',[]):
            a={k:item.get(k) for k in FIELDS};old=cached.get(a.get('id'))
            # Also bind caches to source identity; same UUID must not leak a changed account.
            if old and old.get('platform')==a.get('platform') and old.get('_sourceKey')==item.get('sourceKey'):
                for k in ('fans','fansUpdatedAt','accountMetrics'):a[k]=copy.deepcopy(old.get(k))
                if a.get('accountMetrics'):a['accountMetrics']['cached']=True
            out.append(a)
        return out
    def read(self,local,refresh=False):
        # Hard floor also applies to manual refresh to avoid a member generating API load.
        with self.lock:
            if self.loaded and self.clock()-self.last<60:
                return {'accounts':self.overlay(local),'metadataRefresh':copy.deepcopy(self.cache['metadataRefresh'])}
            self.last=self.clock();self.loaded=True;at=stamp()
            try:
                rows=self.run(['accounts','list','--all'])
                if not isinstance(rows,list):raise MetricsError('invalid_account_list')
                names={x['platformName'] for x in rows if x.get('platformName') in PLATFORMS};overview={};errors={}
                def read_one(name):
                    try:return name,overview_pages(name,self.run),None
                    except MetricsError as e:return name,[],str(e)
                with ThreadPoolExecutor(max_workers=3) as pool:
                    for name,values,error in pool.map(read_one,sorted(names)):
                        if error:errors[PLATFORMS[name]]=error
                        else:overview[PLATFORMS[name]]=values
                accounts=project(local,rows,overview,at,errors)
                previous={x['id']:x for x in (self.cache or {}).get('accounts',[])}
                identities={x['id']:x.get('sourceKey') for x in local.get('accounts',[])}
                for a in accounts:
                    old=previous.get(a['id']);m=a['accountMetrics']
                    if m['overviewStatus']=='error' and old and old.get('_sourceKey')==identities[a['id']] and old.get('platform')==a['platform']:
                        oldm=old.get('accountMetrics') or {}
                        if oldm.get('overview'):
                            m['overview']=copy.deepcopy(oldm['overview']);m['overviewUpdatedAt']=oldm.get('overviewUpdatedAt');m['overviewCached']=True
                        if m['totalFans']['value'] is None and (oldm.get('totalFans') or {}).get('value') is not None:
                            m['totalFans']=copy.deepcopy(oldm['totalFans']);m['totalFans']['cached']=True;a['fans']=m['totalFans']['value'];a['fansUpdatedAt']=old.get('fansUpdatedAt')
                accounts=self.supplement.apply(accounts,local)
                for a in accounts:
                    old=previous.get(a['id']);m=a['accountMetrics'];core=m.get('scientistBackend') or {};oldm=(old or {}).get('accountMetrics') or {};oldcore=oldm.get('scientistBackend') or {}
                    same_owner=old and old.get('_sourceKey')==identities[a['id']] and old.get('platform')==a['platform'] and core.get('identityRef') and core.get('identityRef')==oldcore.get('identityRef')
                    if same_owner and core.get('state') in ('scientist_overview_unavailable','scientist_overview_invalid','scientist_overview_identity_invalid'):
                        have={(x.get('segment'),x.get('key')) for x in m['overview']}
                        for value in oldm.get('overview',[]):
                            if str(value.get('source','')).startswith('data-scientist.') and (value.get('segment'),value.get('key')) not in have:
                                v=copy.deepcopy(value);v.update(cached=True,stale=True);m['overview'].append(v)
                        total=oldm.get('totalFans') or {}
                        if m['totalFans']['value'] is None and str(total.get('source','')).startswith('data-scientist.') and total.get('value') is not None:
                            m['totalFans']=copy.deepcopy(total);m['totalFans'].update(cached=True,stale=True);a['fans']=total['value'];a['fansUpdatedAt']=old.get('fansUpdatedAt')
                accounts=apply_display_policy(accounts)
                meta={'phase':'partial' if errors else 'ok','source':'yxer_metrics_cli','lastSuccessAt':at,'sourceCount':len(rows),'error':None,'platformErrors':errors,'secondarySource':'data-scientist' if any(a['accountMetrics'].get('scientistBackend') for a in accounts) else None}
                keys={x['id']:x.get('sourceKey') for x in local.get('accounts',[])}
                self.cache={'version':1,'accounts':[{**a,'_sourceKey':keys.get(a['id'])} for a in accounts],'metadataRefresh':meta}
                tmp=self.file.with_suffix('.tmp');tmp.write_text(json.dumps(self.cache,ensure_ascii=False));tmp.chmod(0o600);tmp.replace(self.file)
                return {'accounts':accounts,'metadataRefresh':meta}
            except Exception as e:
                previous=(self.cache or {}).get('metadataRefresh',{})
                meta={**previous,'phase':'error','error':str(e) if isinstance(e,MetricsError) else 'metrics_refresh_failed','attemptedAt':at}
                if self.cache:self.cache['metadataRefresh']=meta
                else:self.cache={'version':1,'accounts':[],'metadataRefresh':meta}
                accounts=self.overlay(local)
                for a in accounts:
                    if a.get('accountMetrics'):a['accountMetrics']['refreshError']=meta['error']
                return {'accounts':accounts,'metadataRefresh':meta}

_READERS={}
_READERS_LOCK=threading.Lock()
def account_snapshot(root,local):
    with _READERS_LOCK:reader=_READERS.setdefault(str(root),None)
    if reader is None:
        with _READERS_LOCK:
            if _READERS[str(root)] is None:_READERS[str(root)]=AccountsMetrics(root)
            reader=_READERS[str(root)]
    return reader.read(local)
