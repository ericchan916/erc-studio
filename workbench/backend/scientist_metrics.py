"""Read-only consumer of the scientist core's public multi-account registry.

No profile files, cookies, authorization commands or work-data aggregation.
Numeric supplementation is enabled only against a confirmed core contract.
"""
import datetime,hashlib,json,os,subprocess
CLI='data-scientist'
class ScientistError(Exception):pass

def registry_cli():
 try:
  p=subprocess.run([CLI,'accounts','list'],capture_output=True,timeout=20,env={**os.environ,'PATH':'/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin'})
  if p.returncode or len(p.stdout)>4*1024*1024:raise ValueError()
  d=json.loads(p.stdout)
  if d.get('ok') is not True or d.get('http_status')!=200 or not isinstance(d.get('accounts'),list):raise ValueError()
  return d['accounts']
 except Exception:raise ScientistError('scientist_registry_unavailable')

def overview_cli(ids):
 try:
  p=subprocess.run([CLI,'accounts','overview','--accounts',','.join(ids)],capture_output=True,timeout=20,env={**os.environ,'PATH':'/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin'})
  if p.returncode or len(p.stdout)>4*1024*1024:raise ValueError()
  value=json.loads(p.stdout)
  if value.get('http_status')!=200:raise ValueError()
  return value
 except Exception:raise ScientistError('scientist_overview_unavailable')

def exact_map(records):
 found={}
 for a in records:
  if not isinstance(a,dict) or not isinstance(a.get('id'),str) or not a['id'].startswith('ac_'):raise ScientistError('scientist_registry_invalid')
  if a.get('platform') not in {'douyin','xiaohongshu','bilibili','kuaishou'}:continue
  bindings=a.get('bindings',[])
  if not isinstance(bindings,list):raise ScientistError('scientist_registry_invalid')
  for b in bindings:
   if not isinstance(b,dict) or b.get('source')!='yxer':continue
   sid=b.get('source_id')
   if not isinstance(sid,str) or not sid:raise ScientistError('scientist_binding_invalid')
   k=(a['platform'],hashlib.sha256(sid.encode()).hexdigest())
   if k in found and found[k]['id']!=a['id']:raise ScientistError('scientist_binding_ambiguous')
   found[k]=a
 return found

def status(account):
 auth=account.get('auth') if isinstance(account.get('auth'),dict) else {};identity=account.get('identity') if isinstance(account.get('identity'),dict) else {};healthy=auth.get('status')=='healthy';verified=identity.get('status') in ('matched','verified') and bool(account.get('platform_account_id'))
 if not account.get('enabled',True):state='disabled';reason='科学家核心已停用该账号'
 elif not healthy:state='authorization_pending';reason='使用科学家核心已有账号空间，授权状态待核心核验'
 elif not verified:state='identity_pending';reason='科学家账号已登录；平台稳定身份待核心核验，无需另建登录空间'
 else:state='metrics_contract_pending';reason='科学家登录与身份已核验；账号级数值字段待核心接口确认'
 identity_ref=hashlib.sha256((account['id']+'|'+str(account.get('platform_account_id',''))).encode()).hexdigest()
 return {'identityRef':identity_ref,'source':'data-scientist','state':state,'authStatus':auth.get('status','unknown'),'identityStatus':identity.get('status','unknown'),'checkedAt':auth.get('checked_at'),'reason':reason}

class ScientistSupplement:
 def __init__(self,read=registry_cli,read_overviews=None):self.read=read;self.read_overviews=read_overviews or (overview_cli if read is registry_cli else None)
 def apply(self,accounts,local):
  def missing(a):
   m=a['accountMetrics']
   if m['totalFans']['value'] is None:return True
   if a['platform']!='douyin':return False
   present={x.get('key') for x in m['overview'] if x.get('value') is not None}
   return not {'followingCount','totalLikes','fansIndex'}.issubset(present)
  needed=[a for a in accounts if missing(a)]
  if not needed:return accounts
  try:mapping=exact_map(self.read());error=None
  except ScientistError as e:mapping={};error=str(e)
  local_by_id={a['id']:a for a in local.get('accounts',[])}
  records={};overview_error=None
  selection=sorted({mapping[(a['platform'],local_by_id.get(a['id'],{}).get('sourceKey'))]['id'] for a in needed if (a['platform'],local_by_id.get(a['id'],{}).get('sourceKey')) in mapping and mapping[(a['platform'],local_by_id.get(a['id'],{}).get('sourceKey'))].get('enabled',True)})
  if selection and self.read_overviews:
   try:
    packet=self.read_overviews(selection)
    if packet.get('ok') is not True or packet.get('schema_version')!=1 or packet.get('scope')!='latest_successful_account_overviews' or not isinstance(packet.get('accounts'),list):raise ScientistError('scientist_overview_invalid')
    for row in packet['accounts']:
     if not isinstance(row,dict) or row.get('account_id') not in selection or row['account_id'] in records:raise ScientistError('scientist_overview_identity_invalid')
     records[row['account_id']]=row
   except ScientistError as e:overview_error=str(e);records={}
  for a in needed:
   original=local_by_id.get(a['id'],{});core=mapping.get((a['platform'],original.get('sourceKey')))
   s=status(core) if core else {'source':'data-scientist','state':error or 'source_binding_missing','reason':'科学家核心未提供该来源账号的精确映射或可用状态'}
   a['accountMetrics']['scientistBackend']=s;a['accountMetrics']['backendSupplement']=s['reason']
   if core and core['id'] in records:merge_overview(a,core,records[core['id']])
   elif overview_error:s.update(state=overview_error,reason='科学家账号指标读取失败，保留原来源数据')
   a['accountMetrics']['backendSupplement']=s['reason']
  return accounts

# DTO contract observed in the core's in-progress /accounts/overviews implementation.
# Published CLI accounts overview, schema_version=1, confirmed against real results.
OVERVIEW_FIELDS={'total_fans':('totalFans','总粉丝'),'fans_index':('fansIndex','粉丝指数'),
                 'following_count':('followingCount','关注'),'total_likes':('totalLikes','累计获赞')}

def merge_overview(account,core,record):
 m=account['accountMetrics'];meta=m['scientistBackend']
 if not isinstance(record,dict) or record.get('account_id')!=core['id'] or record.get('platform')!=core['platform']:
  meta.update(state='identity_mismatch',reason='科学家指标快照与精确账号身份不匹配');return account
 if not core.get('enabled',True):return account
 state=record.get('state')
 if state not in ('valid','stale'):
  meta.update(state='overview_'+str(state or 'missing') if meta['state']=='metrics_contract_pending' else meta['state'],reason=meta['reason']+'；账号指标快照尚未就绪' if state=='missing' else '科学家核心当前未实现该平台的账号概览适配器');return account
 if not core.get('platform_account_id') or record.get('platform_account_id')!=core['platform_account_id']:
  meta.update(state='identity_mismatch',reason='科学家指标快照与精确平台身份不匹配');return account
 matched=(core.get('identity') or {}).get('status')=='matched' and (record.get('identity') or {}).get('status')=='matched'
 if not matched:
  meta.update(state='identity_pending',reason='账号指标须先通过科学家稳定身份匹配');return account
 at=record.get('collected_at');metrics=record.get('metrics')
 try:
  parsed=None
  if isinstance(at,str):
   try:parsed=datetime.datetime.fromisoformat(at.replace('Z','+00:00'))
   except ValueError:
    for fmt in ['%Y-%m-%dT%H:%M:%S%z','%Y-%m-%dT%H:%M:%S.%f%z']:
     try:parsed=datetime.datetime.strptime(at,fmt);break
     except ValueError:pass
  valid_time=parsed is not None and parsed.tzinfo is not None
 except ValueError:valid_time=False
 if not valid_time or not isinstance(metrics,dict):
  meta.update(state='overview_invalid',reason='科学家账号指标快照缺少采集时间或字段契约');return account
 valid_count=0;declared_total=None
 for origin,(key,label) in OVERVIEW_FIELDS.items():
  item=metrics.get(origin)
  if not isinstance(item,dict):continue
  status=item.get('status');value=item.get('value')
  if origin=='total_fans':declared_total=status
  if status!='valid' or isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=9007199254740991:continue
  if not isinstance(item.get('raw_field'),str) or item.get('source_route')!='/aweme/v1/creator/user/info/':continue
  v={'key':key,'label':label,'value':value,'source':'data-scientist.douyin_creator','sourceField':item['raw_field'],
      'sourceRoute':item['source_route'],'fetchedAt':at,'updatedAt':item.get('data_updated_at'),
      'period':item.get('statistical_period'),'segment':'account','unit':item.get('unit'),
      'status':'available','stale':state=='stale','cached':state=='stale'}
  if origin=='total_fans':
   if m['totalFans']['value'] is None:v['previousSource']=m['totalFans'];m['totalFans']=v;account['fans']=value;account['fansUpdatedAt']=at;valid_count+=1
  elif not any(x.get('key')==key and x.get('value') is not None for x in m['overview']):m['overview'].append(v);valid_count+=1
 if declared_total=='unsupported' and m['totalFans']['value'] is None:
  m['totalFans'].update(status='unsupported',reason='当前自账号接口提供粉丝指数；精确总粉丝尚待核心补查其他聚合接口，两者不混用')
 meta.update(state='overview_'+state,fieldStates={k:v.get('status') for k,v in metrics.items() if k in OVERVIEW_FIELDS and isinstance(v,dict)},collectedAt=at,reason='已读取科学家核心的账号级指标，非作品指标求和',metricCount=valid_count)
 if isinstance(record.get('fans_index_definition'),dict):m['fansIndexDefinition']={k:record['fans_index_definition'].get(k) for k in ('enable','label','explanation')}
 m['backendSupplement']=meta['reason'];return account
