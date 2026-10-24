"""Member-bound technical publishing queue. All vendor operations use installed yxer.

No platform cookies, arbitrary commands, browser account automation or business ledger.
Preparing uploads media and performs validate + dry-run; submitting requires its digest.
"""
import copy, hashlib, json, os, pathlib, re, shutil, subprocess, threading, time, uuid
from core import Problem

CLI='yxer'
PLATFORMS={'小红书':'xiaohongshu','抖音':'douyin','快手':'kuaishou','哔哩哔哩':'bilibili','视频号':'shipinhao','微信公众号':'weixin'}
CHUNK=65536
BUSY={'preparing','submitting'}

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def save(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.chmod(0o600);tmp.replace(path)

class CLIError(Exception):
    def __init__(self,stage,code,message,unknown=False):
        self.stage=stage;self.code=code;self.message=message;self.unknown=unknown

class Publishing:
    def __init__(self,config,run=None):
        self.config=config;self.root=pathlib.Path(config['state_dir'])/'publish-runs'
        if self.root.is_symlink():raise ValueError('publish_state_symlink')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700)
        handles=config.get('publish_member_handles',['owner'])
        self.allowed={m['principal_id'] for m in config.get('members',[]) if m.get('handle') in handles}
        self.run=run or self.command;self.lock=threading.RLock();self.vendor_lock=threading.Lock();self.contracts={};self.choices={}
        for path in self.root.glob('*/status.json'):
            value=json.loads(path.read_text())
            if value['state'] in BUSY:
                value['state']='execution_unknown' if value['state']=='submitting' else 'failed'
                value['error']='服务重启中断；保留原任务，正式发布结果需核验，不自动重发。'
                for target in value['targets']:
                    if target['state']=='submitting':target['state']='execution_unknown'
                save(path,value)

    def permit(self,owner):
        if owner not in self.allowed:raise Problem(403,'publish_permission_denied')

    def command(self,args,timeout=45):
        stage=args[0]
        try:
            p=subprocess.run([CLI,*args,'--json'],capture_output=True,text=True,timeout=timeout,
                env={**os.environ,'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'})
        except (OSError,subprocess.TimeoutExpired):
            raise CLIError(stage,'transport_unknown','CLI连接或执行超时；请查询原任务，不自动重发。',stage=='publish' and '--dry-run' not in args)
        packet=None
        for raw in (p.stdout,p.stderr):
            try:packet=json.loads(raw);break
            except ValueError:pass
        if not isinstance(packet,dict):raise CLIError(stage,'invalid_cli_response','CLI返回格式异常；请核验原任务。',stage=='publish' and '--dry-run' not in args)
        if p.returncode or packet.get('ok') is not True:
            error=packet.get('error') or {};message=str(error.get('message','CLI操作未完成'))[:500]
            # Do not return credential-bearing details, HTTP bodies or command suggestions.
            message=re.sub(r'(?i)(token|api[_ -]?key|clientid|authorization)\s*[:=]\s*\S+',r'\1=[已隐藏]',message)
            raise CLIError(stage,str(error.get('code','cli_failed')),message,stage=='publish' and '--dry-run' not in args)
        return packet.get('data',{})

    def accounts(self):
        rows=self.run(['accounts','list','--all'])
        if not isinstance(rows,list):raise CLIError('accounts','invalid_accounts','账号返回结构异常。')
        return [a for a in rows if a.get('platformName') in PLATFORMS]

    def catalog(self,owner):
        self.permit(owner)
        doctor=self.run(['doctor']);accounts=self.accounts();supported=set()
        # Installed 3.2.7 catalog omits Xiaohongshu imageText even though schema get
        # and prepare both support it. Probe exact contracts, not a stale index.
        for platform in {a['platformName'] for a in accounts}:
            for kind in ('imageText','video'):
                try:self.run(['schema','get',platform,kind]);supported.add((platform,kind))
                except CLIError as error:
                    if error.code!='YIXIAOER_USAGE_ERR':raise
        return {'accounts':[{'id':a['id'],'name':a.get('platformAccountName','未命名'),'platform':a['platformName'],
                'status':a.get('status'),'locked':bool(a.get('isLock') or a.get('isFreeze') or a.get('isOperate') is False),
                'types':[t for t in ('imageText','video') if (a['platformName'],t) in supported]} for a in accounts],
            'channels':{k:{'configured':bool(v.get('configured'))} for k,v in doctor.get('publishChannelReadiness',{}).items()},
            'checkedAt':time.time(),'canPublish':True,'defaultChannel':'cloud'}

    def account(self,id):
        account=next((a for a in self.accounts() if a['id']==id),None)
        if not account:raise Problem(400,'publish_account_missing')
        if account.get('status')!=1 or account.get('isLock') or account.get('isFreeze') or account.get('isOperate') is False:raise Problem(409,'publish_account_unavailable')
        return account

    def contract(self,platform,kind):
        if platform not in PLATFORMS or kind not in ('video','imageText'):raise Problem(400,'invalid_request')
        key=(platform,kind);cached=self.contracts.get(key)
        if not cached or time.time()-cached[0]>300:
            prep=self.run(['prepare',platform,kind]);schema=self.run(['schema','get',platform,kind])
            cached=(time.time(),schema,prep);self.contracts[key]=cached
        return cached[1],cached[2]

    def field_options(self,owner,body):
        self.permit(owner)
        if set(body)-{'accountId','kind','field','query'}:raise Problem(400,'invalid_request')
        a=self.account(body.get('accountId'));kind=body.get('kind');s,prep=self.contract(a['platformName'],kind)
        fields=s['businessFields'];field=body.get('field')
        if field:
            if field not in ('music','category') or field not in fields:raise Problem(400,'invalid_request')
            if field=='music':
                q=str(body.get('query',''))
                if len(q)>100:raise Problem(400,'invalid_request')
                value=self.run(['query','music',a['id'],*(['--query',q] if q else [])])
            else:value=self.run(['query','categories',a['id'],'--type',kind])
            rows=value.get('dataList',value.get('data',[])) if isinstance(value,dict) else value
            if not isinstance(rows,list):raise CLIError('query','invalid_choices','动态选项格式异常。')
            options=[]
            def visit(items,parents=()):
                for item in items:
                    if not isinstance(item,dict) or not item.get('yixiaoerId'):continue
                    children=item.get('children') or item.get('dataList') or []
                    path=(*parents,item)
                    if children:visit(children,path)
                    else:
                        key=digest(path if field=='category' else item);self.choices[(owner,a['id'],kind,field,key)]=(time.time(),list(path) if field=='category' else item)
                        options.append({'id':key,'label':' / '.join(str(x.get('yixiaoerName','')) for x in path)})
            visit(rows)
            return {'options':options}
        visible={k:v for k,v in fields.items() if k in ('title','description','tags','visibleType','createType','pubType','declaration','category','music') or (v.get('required') and k not in ('formType','images','video','cover','accountForms'))}
        return {'platform':a['platformName'],'kind':kind,'fields':visible,'imageLimit':fields.get('images',{}).get('maxItems',20),
            'musicRequired':a['platformName']=='抖音' and kind=='imageText','checkedAt':time.time()}

    def choice(self,owner,id,kind,field,key):
        value=self.choices.get((owner,id,kind,field,key))
        if not value or time.time()-value[0]>1800:raise Problem(409,'publish_choices_expired')
        return copy.deepcopy(value[1])

    def create(self,owner,body,key):
        self.permit(owner)
        if not isinstance(key,str) or not 8<=len(key)<=100:raise Problem(400,'invalid_request')
        if not isinstance(body,dict) or set(body)!={'kind','channel','title','description','targets','files'}:raise Problem(400,'invalid_request')
        kind,channel=body['kind'],body['channel']
        if kind not in ('video','imageText') or channel not in ('cloud','local'):raise Problem(400,'invalid_request')
        if not isinstance(body['title'],str) or len(body['title'])>200 or not isinstance(body['description'],str) or len(body['description'])>50000:raise Problem(400,'invalid_request')
        targets,files=body['targets'],body['files']
        if not isinstance(targets,list) or not 1<=len(targets)<=12 or not isinstance(files,list) or not 1<=len(files)<=21:raise Problem(400,'invalid_request')
        if len({t.get('accountId') for t in targets})!=len(targets):raise Problem(400,'invalid_request')
        if kind=='video' and sorted(f.get('role') for f in files)!=['cover','video']:raise Problem(400,'publish_video_cover_required')
        if kind=='imageText' and any(f.get('role')!='image' for f in files):raise Problem(400,'invalid_request')
        for f in files:
            if set(f)!={'name','size','role'} or not isinstance(f['name'],str) or pathlib.Path(f['name']).name!=f['name'] or len(f['name'])>150 or '\\'in f['name']:raise Problem(400,'invalid_request')
            suffix=pathlib.Path(f['name']).suffix.lower()
            if suffix not in (('.mp4','.mov') if f['role']=='video' else ('.png','.jpg','.jpeg','.webp','.gif')):raise Problem(400,'publish_media_type_invalid')
            if type(f['size']) is not int or not 0<f['size']<=(512*1024**2 if f['role']=='video' else 20*1024**2):raise Problem(413,'invalid_request')
        resolved=[]
        for t in targets:
            if not isinstance(t,dict) or set(t)-{'accountId','fields','title','description'}:raise Problem(400,'invalid_request')
            a=self.account(t.get('accountId'));schema,_=self.contract(a['platformName'],kind);spec=schema['businessFields'];form={'formType':'task'}
            for k,v in spec.items():
                if 'default'in v:form[k]=copy.deepcopy(v['default'])
            form.update({'title':t.get('title',body['title']),'description':t.get('description',body['description'])})
            fields=t.get('fields',{})
            if not isinstance(fields,dict):raise Problem(400,'invalid_request')
            for k,v in fields.items():
                if k not in spec or k in ('formType','images','video','cover','coverKey','accountForms','horizontalCover'):raise Problem(400,'invalid_request')
                if k in ('category','music'):form[k]=self.choice(owner,a['id'],kind,k,v)
                elif spec[k].get('type') in ('string','number','integer','boolean','array') and k not in ('shopping_cart','sync_apps'):
                    if spec[k]['type']=='array' and (not isinstance(v,list) or any(not isinstance(x,str) for x in v)):raise Problem(400,'invalid_request')
                    form[k]=v
                else:raise Problem(400,'invalid_request')
            for k in ('title','description'):
                value=form[k];rule=spec.get(k,{})
                if not isinstance(value,str) or len(value)>rule.get('maxLength',50000) or len(value)<rule.get('minLength',0):raise Problem(400,'publish_text_length_invalid')
            if kind=='imageText' and not spec.get('images'):raise Problem(400,'publish_type_unsupported')
            if kind=='imageText' and len(files)>spec.get('images',{}).get('maxItems',20):raise Problem(400,'publish_image_limit')
            if a['platformName']=='抖音' and kind=='imageText' and not form.get('music'):raise Problem(400,'publish_music_required')
            for k,v in spec.items():
                if v.get('required') and k not in ('formType','images','video','cover','accountForms') and (k not in form or form[k] in ('',[],None)):raise Problem(400,'publish_required_field:'+k)
            resolved.append({'accountId':a['id'],'accountName':a.get('platformAccountName',''),'platform':a['platformName'],'form':form,'state':'waiting','error':None,'taskSetId':None})
        signature=digest(body)
        with self.lock:
            for p in self.root.glob('*/status.json'):
                v=json.loads(p.read_text())
                if v['owner']==owner and v['key']==key:
                    if v['signature']!=signature:raise Problem(409,'idempotency_conflict')
                    return self.dto(v)
            if sum(json.loads(p.read_text())['state'] not in ('submitted','failed','execution_unknown','cancelled') for p in self.root.glob('*/status.json'))>=20:raise Problem(429,'rate_limited')
            if shutil.disk_usage(self.root).free<sum(f['size'] for f in files)+1024**3:raise Problem(507,'publish_disk_capacity_low')
            id=str(uuid.uuid4());folder=self.root/id;folder.mkdir(mode=0o700)
            v={'id':id,'owner':owner,'key':key,'signature':signature,'kind':kind,'channel':channel,'title':body['title'],'description':body['description'],
                'state':'uploading','createdAt':time.time(),'updatedAt':time.time(),'error':None,'targets':resolved,
                'files':[dict(f,id=str(uuid.uuid4()),received=0,sha256=None) for f in files],'confirmation':None}
            save(folder/'status.json',v);return self.dto(v)

    def own(self,owner,id):
        self.permit(owner)
        try:uuid.UUID(id)
        except (ValueError,TypeError):raise Problem(404,'not_found')
        path=self.root/id/'status.json'
        if not path.is_file():raise Problem(404,'not_found')
        v=json.loads(path.read_text())
        if v['owner']!=owner:raise Problem(404,'not_found')
        return v

    def write(self,v):v['updatedAt']=time.time();save(self.root/v['id']/'status.json',v)
    @staticmethod
    def dto(v):
        return {k:copy.deepcopy(v[k]) for k in ('id','kind','channel','title','description','state','createdAt','updatedAt','error','files','confirmation')}|{'targets':[{k:copy.deepcopy(t.get(k)) for k in ('accountId','accountName','platform','state','error','taskSetId','receipt','verification','summary')} for t in v['targets']]}

    def get(self,owner,id):
        with self.lock:return self.dto(self.own(owner,id))

    def listing(self,owner):
        self.permit(owner)
        with self.lock:
            values=[json.loads(p.read_text()) for p in self.root.glob('*/status.json')]
            return {'items':[self.dto(v) for v in sorted(values,key=lambda x:-x['createdAt']) if v['owner']==owner][:100]}

    def upload(self,owner,id,file_id,offset,chunk,checksum):
        if not 0<len(chunk)<=CHUNK or hashlib.sha256(chunk).hexdigest()!=checksum:raise Problem(400,'invalid_request')
        with self.lock:
            v=self.own(owner,id);f=next((f for f in v['files'] if f['id']==file_id),None)
            if not f or v['state']!='uploading':raise Problem(409,'publish_stage_conflict')
            path=self.root/id/(file_id+pathlib.Path(f['name']).suffix.lower())
            if type(offset)is not int or offset<0 or offset+len(chunk)>f['size']:raise Problem(400,'invalid_request')
            # A crash can happen after fsync(data) but before committing status.
            # Resume from the confirmed byte count, never append after an orphan tail.
            actual=path.stat().st_size if path.exists() else 0
            if actual<f['received']:raise Problem(409,'publish_upload_conflict')
            if actual>f['received']:
                with path.open('r+b') as stream:stream.truncate(f['received']);stream.flush();os.fsync(stream.fileno())
            if offset<f['received']:
                with path.open('rb') as stream:stream.seek(offset);same=stream.read(len(chunk))==chunk
                if same and offset+len(chunk)<=f['received']:return self.dto(v)
                raise Problem(409,'publish_upload_conflict')
            if offset!=f['received']:raise Problem(409,'publish_upload_conflict')
            with path.open('ab') as stream:stream.write(chunk);stream.flush();os.fsync(stream.fileno())
            path.chmod(0o600);f['received']+=len(chunk)
            if path.stat().st_size!=f['received']:raise Problem(409,'publish_upload_conflict')
            if f['received']==f['size']:
                h=hashlib.sha256()
                with path.open('rb') as stream:
                    for part in iter(lambda:stream.read(1024*1024),b''):h.update(part)
                f['sha256']=h.hexdigest()
            self.write(v);return self.dto(v)

    def prepare(self,owner,id):
        with self.lock:
            v=self.own(owner,id)
            if v['state'] in ('preparing','ready'):return self.dto(v)
            if v['state'] not in ('uploading','failed') or any(f['received']!=f['size'] for f in v['files']):raise Problem(409,'publish_stage_conflict')
            if any(t.get('taskSetId') or t['state']=='execution_unknown' for t in v['targets']):raise Problem(409,'publish_stage_conflict')
            v['state']='preparing';v['error']=None;self.write(v)
            threading.Thread(target=self._prepare,args=(owner,id),daemon=True).start();return self.dto(v)

    def _prepare(self,owner,id):
        with self.vendor_lock:
            try:
                v=self.own(owner,id)
                for target in v['targets']:
                    self.account(target['accountId']);platform=target['platform'];resources=[]
                    for f in v['files']:
                        path=self.root/id/(f['id']+pathlib.Path(f['name']).suffix.lower())
                        with path.open('rb') as stream:header=stream.read(16)
                        valid=(b'ftyp'in header) if f['role']=='video' else (header.startswith((b'\x89PNG\r\n\x1a\n',b'\xff\xd8\xff',b'GIF8')) or (header.startswith(b'RIFF') and b'WEBP'in header))
                        if not valid:raise CLIError('upload','media_header_invalid','素材格式与内容不匹配，请重新选择图片或MP4/MOV。')
                        resource=self.run(['upload',str(path),'--platform',platform,'--usage',f['role'],'--auto-meta'],timeout=600)
                        if not isinstance(resource,dict) or not all(resource.get(k) for k in ('key','size','width','height')):raise CLIError('upload','media_metadata_missing','上传资源缺少尺寸或文件信息，未进入发布。')
                        if f['role']=='video' and not resource.get('duration'):raise CLIError('upload','video_duration_missing','视频上传回执缺少时长。')
                        resources.append((f['role'],resource))
                        save(self.root/id/(target['accountId']+'-'+f['id']+'-upload.json'),resource)
                    form=copy.deepcopy(target['form']);af={'platformAccountId':target['accountId'],'contentPublishForm':form}
                    cover=next((r for role,r in resources if role=='cover'),resources[0][1]);af.update(cover=cover,coverKey=cover['key'])
                    if v['kind']=='video':
                        video=next(r for role,r in resources if role=='video');af['video']=video;form.update(video=video,cover=cover,coverKey=cover['key'])
                    else:af['images']=[r for _,r in resources];form['images']=af['images']
                    payload={'action':'publish','publishType':v['kind'],'platforms':[platform],'publishChannel':v['channel'],'publishArgs':{'accountForms':[af]}}
                    path=self.root/id/(target['accountId']+'-payload.json');save(path,payload)
                    self.run(['validate',platform,v['kind'],str(path),'--publish-channel',v['channel']],timeout=120)
                    dry=self.run(['publish',v['kind'],platform,str(path),'--publish-channel',v['channel'],'--dry-run'],timeout=120)
                    save(self.root/id/(target['accountId']+'-dry-run.json'),dry)
                    meta=dry.get('meta',{})
                    if meta.get('effectivePublishChannel',v['channel'])!=v['channel']:raise CLIError('dry-run','channel_mismatch','CLI实际发布通道与选择不同，请重新检查。')
                    target.update(state='ready',error=None,payloadDigest=digest(payload),requestHash=meta.get('requestHash'),
                        summary={'title':form.get('title',''),'description':form.get('description',''),'kind':v['kind'],'channel':v['channel'],'fileCount':len(v['files']),
                            'settings':{k:display_setting(value) for k,value in form.items() if k not in ('formType','title','description','images','video','cover','coverKey')}})
                    self.write(v)
                v['confirmation']=digest({'targets':v['targets'],'files':v['files'],'channel':v['channel']});v['preparedAt']=time.time();v['state']='ready';self.write(v)
            except Exception as e:
                v=self.own(owner,id);v['state']='failed';v['confirmation']=None;v['error']=e.message if isinstance(e,CLIError) else getattr(e,'code',type(e).__name__);self.write(v)

    def submit(self,owner,id,body):
        with self.lock:
            v=self.own(owner,id)
            if not isinstance(body,dict) or set(body)!={'confirmation','approved'} or body['approved']is not True:raise Problem(400,'publish_confirmation_required')
            if v['state'] in ('submitting','submitted','execution_unknown'):return self.dto(v)
            if v['state']!='ready' or body['confirmation']!=v['confirmation']:raise Problem(409,'publish_confirmation_changed')
            if time.time()-v['preparedAt']>1800:raise Problem(409,'publish_preflight_expired')
            for target in v['targets']:
                self.account(target['accountId']);p=json.loads((self.root/id/(target['accountId']+'-payload.json')).read_text())
                if digest(p)!=target['payloadDigest']:raise Problem(409,'publish_confirmation_changed')
            v['state']='submitting';self.write(v);threading.Thread(target=self._submit,args=(owner,id),daemon=True).start();return self.dto(v)

    def _submit(self,owner,id):
        with self.vendor_lock:
            v=self.own(owner,id)
            for target in v['targets']:
                if target['state']!='ready':continue
                target['state']='submitting';self.write(v)
                try:
                    self.account(target['accountId']);path=self.root/id/(target['accountId']+'-payload.json')
                    if digest(json.loads(path.read_text()))!=target['payloadDigest']:raise CLIError('publish','payload_changed','发布包已变化，请核验。')
                    result=self.run(['publish',v['kind'],target['platform'],str(path),'--publish-channel',v['channel']],timeout=120)
                    save(self.root/id/(target['accountId']+'-receipt.json'),result)
                    task=result.get('taskSetId') or result.get('taskSet',{}).get('id') or result.get('id')
                    if not isinstance(task,str) or not re.fullmatch('[A-Za-z0-9_-]{1,120}',task):raise CLIError('publish','receipt_missing','CLI已返回，但缺少可查询任务编号；先核验，勿重发。',True)
                    target.update(state='submitted',taskSetId=task,receipt={'accepted':True,'publicVisible':None},error=None)
                except Exception as e:
                    target.update(state='execution_unknown' if not isinstance(e,Problem) else 'failed',error=e.message if isinstance(e,CLIError)else getattr(e,'code',type(e).__name__))
                self.write(v)
            v['state']='execution_unknown' if any(t['state']=='execution_unknown' for t in v['targets']) else ('submitted' if all(t['state']=='submitted' for t in v['targets']) else 'failed');self.write(v)

    def verify(self,owner,id):
        with self.lock:
            v=self.own(owner,id)
            if v['state'] in BUSY:raise Problem(409,'publish_stage_conflict')
            for t in v['targets']:
                if not t.get('taskSetId'):continue
                result=self.run(['query','details',t['taskSetId']])
                save(self.root/id/(t['accountId']+'-details.json'),result)
                # Display vendor evidence, do not invent a public-visible success.
                t['verification']={'checkedAt':time.time(),'details':detail_projection(result),'publicVisible':None}
            self.write(v);return self.dto(v)

    def cancel(self,owner,id):
        with self.lock:
            v=self.own(owner,id)
            if v['state']=='cancelled':return self.dto(v)
            if v['state'] not in ('uploading','ready','failed') or any(t.get('taskSetId') or t['state']=='execution_unknown' for t in v['targets']):raise Problem(409,'publish_stage_conflict')
            v['state']='cancelled';v['confirmation']=None;self.write(v);return self.dto(v)

def redact(value):
    if isinstance(value,dict):return {k:redact(v) for k,v in value.items() if not re.search('token|cookie|secret|clientid|authorization|password|apikey',k,re.I)}
    if isinstance(value,list):return [redact(v) for v in value]
    if isinstance(value,str) and len(value)>2000:return value[:2000]
    return value

def display_setting(value):
    if isinstance(value,dict):return value.get('yixiaoerName','已选查询对象')
    if isinstance(value,list):return [display_setting(v) for v in value]
    return value

def detail_projection(value):
    if not isinstance(value,dict):return {}
    allowed={'platformName','platformAccountName','stageStatus','taskStatus','queryStatus','publishId','openUrl','errorMessage','visibleType','title'}
    return {'id':value.get('id'),'tasks':[{k:redact(v) for k,v in t.items() if k in allowed} for t in value.get('tasks',[]) if isinstance(t,dict)]}
