"""Base v3 field gate and journalled creation; no credentials or business defaults."""
from __future__ import annotations
import json
import re
import time
import urllib.parse
from bridge_cherry import ExecutionRejected, ExecutionUnknown

TYPES=set('text number select datetime created_at updated_at user group_chat created_by updated_by link formula lookup auto_number attachment location checkbox button'.split())
COMMON={'name','type','description','default_value'}
EXTRA={'text':{'style'},'number':{'style'},'select':{'multiple','options','dynamic_options_source'},
       'datetime':{'style'},'created_at':{'style'},'updated_at':{'style'},'user':{'multiple'},
       'group_chat':{'multiple'},'link':{'link_table','bidirectional','bidirectional_link_field_name'},
       'formula':{'expression'},'lookup':{'from','select','where','aggregate'},'auto_number':{'style'},
       'button':{'button_config'}}
GUIDE='请读取 skills read lark-base/references/lark-base-field-schema.md，修正后重新预览。'

def invalid(message):
    raise ExecutionRejected('Base 字段契约未通过：'+message+'；尚未提交写入。'+GUIDE)

def fields_contract(value, single=False):
    fields=[value] if isinstance(value,dict) and single else value
    if not isinstance(fields,list) or not fields or len(fields)>200:invalid('需要1–200项字段数组')
    names=set()
    for f in fields:
        if not isinstance(f,dict):invalid('字段应为对象')
        name=f.get('name');kind=f.get('type')
        if not isinstance(name,str) or not name.strip() or name in names:invalid('字段名缺失或重复')
        names.add(name)
        if not isinstance(kind,str) or kind not in TYPES:invalid('type不受支持；单选用select，金额用number，日期用datetime')
        if set(f)-(COMMON|EXTRA.get(kind,set())):invalid('存在旧版或未知属性；移除property/field_name/ui_type等属性')
        if 'description' in f and not isinstance(f['description'],str):invalid('description应为文字')
        if 'default_value' in f and kind not in {'text','number','select','datetime','user'}:invalid('该类型不支持default_value')
        for k in ('multiple','bidirectional'):
            if k in f and type(f[k]) is not bool:invalid(k+'应为布尔值')
        required={'link':('link_table',),'formula':('expression',),'lookup':('from','select','where')}.get(kind,())
        for k in required:
            if k not in f or f[k] is None:invalid('缺少'+k)
        if kind=='link' and (not isinstance(f['link_table'],str) or not f['link_table']):invalid('link_table应为表引用')
        if kind=='formula' and (not isinstance(f['expression'],str) or not f['expression']):invalid('expression应为公式文本')
        if kind=='button' and (not isinstance(f.get('button_config'),dict) or not isinstance(f['button_config'].get('title'),str)):invalid('button_config.title缺失')
        if kind=='select':
            if 'options' in f and 'dynamic_options_source' in f:invalid('静态和动态选项互斥')
            opts=f.get('options',[])
            if not isinstance(opts,list) or len(opts)>10000:invalid('options应为数组')
            seen=set()
            for o in opts:
                if not isinstance(o,dict) or set(o)-{'name','hue','lightness'} or not isinstance(o.get('name'),str) or not o['name'] or o['name'] in seen:invalid('选项结构或名称重复')
                seen.add(o['name'])
                if 'hue' in o and o['hue'] not in 'Red Orange Yellow Lime Green Turquoise Wathet Blue Carmine Purple Gray'.split():invalid('选项hue错误')
                if 'lightness' in o and o['lightness'] not in 'Lighter Light Standard Dark Darker'.split():invalid('选项lightness错误')
            if 'dynamic_options_source' in f:
                s=f['dynamic_options_source']
                if not isinstance(s,dict) or set(s)!={'table_id','field_id'} or not all(isinstance(v,str) and v for v in s.values()):invalid('动态选项来源错误')
                if 'default_value' in f:invalid('动态选项不支持默认值')
        style=f.get('style')
        if style is not None:
            if not isinstance(style,dict):invalid('style应为对象')
            if kind=='number':
                t=style.get('type','plain');allowed={'plain':{'precision','percentage','thousands_separator'},'currency':{'precision','currency_code'},'progress':{'percentage','color'},'rating':{'icon','min','max'}}
                if t not in allowed or set(style)-({'type'}|allowed[t]):invalid('数字style与样式类型不匹配')
                if 'precision' in style and (type(style['precision']) is not int or not 0<=style['precision']<=4):invalid('precision范围0–4')
                if t=='currency' and not re.fullmatch('[A-Z]{3}',str(style.get('currency_code',''))):invalid('货币必须指定currency_code，例如CNY')
                if t=='progress' and not style.get('color'):invalid('进度颜色缺失')
                for k in ('percentage','thousands_separator'):
                    if k in style and type(style[k]) is not bool:invalid(k+'应为布尔值')
            elif kind in {'datetime','created_at','updated_at'}:
                if set(style)-{'format'} or style.get('format','yyyy/MM/dd') not in {'yyyy/MM/dd','yyyy/MM/dd HH:mm','yyyy/MM/dd HH:mm Z','yyyy-MM-dd','yyyy-MM-dd HH:mm','yyyy-MM-dd HH:mm Z','MM-dd','MM/dd/yyyy','dd/MM/yyyy'}:invalid('日期style.format错误')
            elif kind=='text':
                if set(style)-{'type'} or style.get('type','plain') not in {'plain','phone','url','email','barcode'}:invalid('文本style错误')
            elif kind=='auto_number':
                rules=style.get('rules')
                if set(style)!={'rules'} or not isinstance(rules,list) or not 1<=len(rules)<=9:invalid('自动编号rules错误')
                for rule in rules:
                    if not isinstance(rule,dict):invalid('编号规则应为对象')
                    t=rule.get('type')
                    if t=='text':
                        if set(rule)!={'type','text'} or not isinstance(rule['text'],str):invalid('编号文本规则错误')
                    elif t=='incremental_number':
                        if set(rule)!={'type','length'} or type(rule['length']) is not int or not 1<=rule['length']<=9:invalid('编号长度1–9')
                    elif t=='created_time':
                        if set(rule)!={'type','date_format'} or rule['date_format'] not in {'yyyyMMdd','yyyyMM','yyMM','MMdd','yyyy','MM','dd'}:invalid('编号日期格式错误')
                    else:invalid('编号规则类型错误')
        v=f.get('default_value')
        if v is not None:
            if kind=='text' and not isinstance(v,str):invalid('文本默认值应为文字')
            if kind=='number' and (type(v) not in (int,float)):invalid('数字默认值应为数字')
            if kind=='select' and (not isinstance(v,list) or any(not isinstance(x,str) or x not in {o['name'] for o in f.get('options',[])} for x in v) or (not f.get('multiple',False) and len(v)>1)):invalid('选项默认值应为已定义名称数组')
    return fields

def flags(args):
    out={};i=2
    while i<len(args):
        arg=args[i]
        if not arg.startswith('--'):invalid('非预期位置参数')
        k,sep,v=arg.partition('=')
        if k in out:invalid('重复参数'+k)
        if not sep:
            if k in {'--dry-run','--yes'}:v=True
            elif i+1<len(args):i+=1;v=args[i]
            else:invalid('参数缺少值')
        out[k]=v;i+=1
    return out

def checked_args(cli,args):
    """Freeze referenced payloads before approval; validate shortcuts and raw v3 writes."""
    if args[0]=='base' and len(args)>1 and args[1] in {'+base-create','+table-create','+field-create','+field-update'}:
        f=flags(args);key='--fields' if args[1] in {'+base-create','+table-create'} else '--json'
        if key not in f:
            if args[1]=='+base-create' and '--table-name' not in f:return args
            invalid('缺少'+key)
        raw=f[key]
        if not isinstance(raw,str):invalid('字段JSON缺失')
        if raw.startswith('@'):
            try:
                p=cli.path(raw[1:])
                if p.stat().st_size>24000:invalid('字段文件超过预算')
                raw=p.read_text()
            except (OSError,UnicodeError):invalid('字段文件读取失败')
        try:value=json.loads(raw)
        except (ValueError,TypeError):invalid('字段JSON格式错误')
        if args[1]=='+field-update' and not isinstance(value,dict):invalid('field-update只接受单个完整字段对象')
        fields_contract(value,single=args[1] in {'+field-create','+field-update'})
        if args[1] in {'+base-create','+table-create'}:
            name=f.get('--table-name' if args[1]=='+base-create' else '--name')
            if not isinstance(name,str) or not name.strip():invalid('表名缺失')
        if args[1]=='+base-create':
            if set(f)-{'--name','--table-name','--fields','--folder-token','--time-zone','--dry-run','--as','--format'}:invalid('创建Base包含未登记参数')
            if not f.get('--name'):invalid('Base名称缺失')
        frozen=json.dumps(value,ensure_ascii=False,separators=(',',':'));cli.material(frozen);out=[];i=0
        while i<len(args):
            if args[i]==key:out.extend([key,frozen]);i+=2
            elif args[i].startswith(key+'='):out.extend([key,frozen]);i+=1
            else:out.append(args[i]);i+=1
        return out
    if args[0]=='api' and len(args)>2 and args[1] in {'POST','PUT','PATCH'}:
        path=args[2].rstrip('/')
        if re.search(r'/open-apis/base/v3/bases(?:/[^/]+/tables(?:/[^/]+/fields(?:/[^/]+)?)?)?$',path):
            if path.endswith('/bases') or path.endswith('/tables'):
                raise ExecutionRejected('创建Base/表请使用base +base-create/+table-create，以经过字段预检与回执核验。')
            # Raw field writes must use the same gate; legacy bitable v1 is a different schema.
            raise ExecutionRejected('Base v3字段写入请使用base +field-create/+field-update，以经过同一字段契约校验。')
    return args

def is_compound(args):
    return args[:2]==['base','+base-create'] and '--fields' in args

def success(result):
    return result.get('exit_status')==0 and isinstance(result.get('output'),dict) and result['output'].get('ok') is True

def data_of(result):
    if not success(result):raise ValueError('cli_non_success')
    return result['output'].get('data') or {}

def contains(expected,actual):
    if isinstance(expected,dict):return isinstance(actual,dict) and all(k in actual and contains(v,actual[k]) for k,v in expected.items())
    if isinstance(expected,list):return isinstance(actual,list) and len(expected)==len(actual) and all(contains(x,y) for x,y in zip(expected,actual))
    return type(expected)==type(actual) and expected==actual

class BaseCreation:
    """Non-atomic API writes have durable step receipts, never automatic write replay."""
    def __init__(self,cli,approval,args):
        self.cli=cli;self.approval=approval;self.f=flags(args)
        self.record={'operation':'base-create-v1','phase':'starting','steps':[], 'verified':False}
    def save(self):
        with self.cli.connect() as db:db.execute('UPDATE actions SET result=? WHERE id=?',(json.dumps(self.record,ensure_ascii=False),self.approval))
    def call(self,stage,args,write=False):
        self.record['phase']=stage
        step={'stage':stage,'state':'started','args':args};self.record['steps'].append(step);self.save()
        try:
            if not self.cli.actor():raise ValueError('actor_authorization_expired')
            r=self.cli.result(self.cli.command([*args,'--as','user','--format','json'],timeout=90))
            step.update(result=r,state='returned');self.save()
            return data_of(r)
        except Exception as e:
            step.update(state='unknown' if write else 'failed',error_type=type(e).__name__);self.save();raise
    def run(self):
        args=['base','+base-create','--name',self.f['--name']]
        for k in ('--folder-token','--time-zone'):
            if k in self.f:args.extend([k,self.f[k]])
        try:
            # Do NOT invoke CLI's hidden create/table/delete transaction.
            d=self.call('create_base',args,True);b=d.get('base',d)
            token=b.get('base_token')
            if not isinstance(token,str) or not re.fullmatch('[A-Za-z0-9]+',token):raise ValueError('missing_base_receipt')
            self.record.update(base_token=token,url=b.get('url'));self.save()
            self.call('verify_base',['base','+base-get','--base-token',token])
            listing=self.call('initial_tables',['base','+table-list','--base-token',token])
            self.record['initial_tables']=listing;self.save()
            d=self.call('create_table',['base','+table-create','--base-token',token,'--name',self.f['--table-name'],'--fields',self.f['--fields']],True)
            table=d.get('table',d);tid=table.get('id') or table.get('table_id')
            if not isinstance(tid,str) or not re.fullmatch('tbl[A-Za-z0-9]+',tid):raise ValueError('missing_table_receipt')
            self.record['table_id']=tid
            if isinstance(self.record.get('url'),str):
                self.record['base_url']=self.record['url']
                url=urllib.parse.urlsplit(self.record['url'])
                query=dict(urllib.parse.parse_qsl(url.query));query['table']=tid
                self.record['url']=urllib.parse.urlunsplit(url._replace(query=urllib.parse.urlencode(query)))
            self.save()
            expected=json.loads(self.f['--fields'])
            # Eventual consistency: retry ONLY reads, never creates.
            for attempt in range(4):
                got=self.call('verify_fields',['base','+field-list','--base-token',token,'--table-id',tid,'--limit','200'])
                fields=got.get('fields',[])
                by_name={x.get('name'):x for x in fields if isinstance(x,dict)}
                if len(by_name)==len(expected) and got.get('total',len(fields))==len(expected) and all(contains(x,by_name.get(x['name'])) for x in expected):break
                if attempt<3:time.sleep(1+attempt)
            else:raise ValueError('field_readback_mismatch')
            tables=self.call('verify_table',['base','+table-list','--base-token',token])
            if not any(t.get('id')==tid and t.get('name')==self.f['--table-name'] for t in tables.get('tables',[])):raise ValueError('table_readback_mismatch')
            self.record.update(phase='verified',verified=True,fields=fields,default_table_preserved=True)
            self.save()
            return {'exit_status':0,'output':{'ok':True,'identity':'user','data':{k:v for k,v in self.record.items() if k!='steps'}},'journal':self.record,'verification':'Base、目标表及字段已通过本人身份读回；平台默认表保留，不自动删除。'}
        except Exception as e:
            self.record.update(error_type=type(e).__name__,verified=False);self.save()
            target=self.record.get('base_token','尚无可核验Base ID')
            raise ExecutionUnknown('建表停在'+self.record['phase']+'；'+target+'。分步回执已保存，保留已创建资源，先核验再续接，不重复创建。') from None

def approval_text(result):
    args=result.get('args',[]);lines=['待本人确认：'+ ' '.join(args[:2])]
    if is_compound(args):
        f=flags(args);fields=json.loads(f['--fields'])
        lines.extend(['Base：'+f['--name'],'数据表：'+f['--table-name'],'字段：'+'、'.join(x['name']+'（'+x['type']+'）' for x in fields),'执行前校验字段，分步保存回执，读回后才报完成；平台默认空表保留。'])
    else:lines.append('参数：'+json.dumps(args,ensure_ascii=False))
    lines.append('请本人回复：确认 '+result['approval_id'])
    return '\n'.join(lines)
