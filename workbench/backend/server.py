#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, http.cookies, json, mimetypes, os, re, signal, threading, time, urllib.error, urllib.parse, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from core import Service, Problem, shape, uid
from providers import ModelProxy, FeishuCards
from oauth import DeviceOAuth
from workflow_web import workflow_snapshot
from account_metrics import account_snapshot
from production_backend import Production, CHUNK
from publishing import Publishing, CLIError, CHUNK as PUBLISH_CHUNK
from account_authorizations import AccountAuthorizations
from conversation_protocol import ProtocolError, read_key, verify as verify_studio, headers_for
from studio_internal import dispatch as studio_dispatch
from content_snapshot import ContentRefresher,ContentRefreshError
from analytics_gateway import AnalyticsGateway
from radar_snapshot import RadarRefresher,RadarRefreshError

ERRORS={'authentication_required':'请登录对应成员账号。','not_found':'记录未找到。','invalid_request':'请求参数不符合契约。','login_expired_or_invalid':'登录码或登录请求已失效，请重新获取。','rate_limited':'请求较频繁，请稍后重试。','member_busy':'该成员已有执行中的对话。','conversation_busy_or_unknown':'请先等待或核验此对话的原任务。','idempotency_conflict':'相同请求标识对应的内容不同。','login_delivery_failed':'飞书登录卡片尚未送达。','member_inactive':'成员当前不在已授权名单内。','feishu_identity_check_failed':'飞书成员身份校验暂未完成。','empty_model_response':'模型没有返回可展示的最终答案。','model_http_402':'模型供应商额度或余额不足。','model_http_429':'模型供应商正在限流。','model_connection_failed':'模型连接暂时异常。','oauth_not_configured':'当前使用设备授权流程，无需网页回调。','oauth_expired':'扫码请求已失效，请重新获取二维码。','oauth_member_not_allowed':'扫码身份不在工作室成员名单内。','oauth_cli_failed':'飞书官方授权暂未完成，请重试或使用私聊登录码。','conversation_conflict':'会话已在其他窗口更新，请刷新后再操作。','list_changed':'历史列表已更新，请重新读取第一页。','conversation_archived':'此会话已归档，取消归档后才能继续发送。','invalid_cursor':'分页标识不符合当前查询。','job_not_retryable':'该任务当前不适合重试。','retry_not_latest':'仅支持重试此会话最新未完成的问题。','retry_confirmation_required':'请先确认重试执行结果未知的任务。','content_changed_refresh_required':'内容项目或上传进度已变化，请刷新并核对原任务。','content_stage_conflict':'上传或剪辑任务状态不匹配，请先查询原任务。','production_permission_denied':'当前身份没有启动本机剪辑的权限。','production_result_unknown':'该项目有执行结果未知的原任务，需核验后再制作，不创建新任务绕过。','production_disk_capacity_low':'本机可用空间不足，请核对磁盘容量后再创建剪辑任务。'}
ERRORS.update({'publish_permission_denied':'当前成员没有发布权限。','publish_music_required':'抖音图文需选择查询返回的BGM。','publish_account_unavailable':'发布账号已过期、锁定或暂停操作，请本人核验。','publish_text_length_invalid':'标题或正文不符合目标平台字数要求。','publish_video_cover_required':'视频需另附本人提供的封面。','publish_stage_conflict':'该任务阶段不支持此操作，请查询原任务。','publish_confirmation_changed':'发布包或确认摘要已变化，请重新核验。','publish_choices_expired':'动态选项已过期，请重新查询。','publish_preflight_expired':'检查超过30分钟，请重新准备。','publish_disk_capacity_low':'磁盘空间不足，请先清理后创建发布任务。'})
ERRORS.update({'shared_context_not_enabled':'三端共用上下文正在迁移，当前仍保留原会话入口。','conversation_binding_conflict':'当前会话已在另一端切换，请刷新后明确选择继续。','conversation_queue_full':'此对话的等待队列已满，请先等待或取消原任务。','context_summary_unavailable':'上下文摘要接口尚未就绪，原历史完整保留。','context_active_turn_too_large':'本轮内容超过上下文预算，原文已保留；请核对模型预算或缩短新请求。','context_summary_input_too_large':'历史整理输入超过预算，原历史完整保留。','context_summary_budget_exceeded':'整理后的上下文仍超过预算，原历史完整保留。','context_summary_empty':'模型没有返回有效历史摘要，原任务已保留。','context_summary_output_too_large':'历史摘要超出预算，未截断或改写原历史。'})
ERRORS.update({'content_refresh_unavailable':'飞书实时读取暂未完成，请稍后重试。','content_refresh_protocol_invalid':'飞书实时读取结果格式异常，旧缓存未覆盖。','content_refresh_failed':'飞书实时读取未完成，旧缓存仍保留。','content_authorization_required':'当前固定飞书身份需要重新授权。','content_identity_changed':'飞书读取身份与缓存身份不一致，未展示其他人的数据。','content_cli_failed':'飞书原表读取未通过，旧缓存仍保留。'})
ERRORS.update({'analytics_sync_permission_denied':'仅工作室本人管理员可同步RUM；其他成员可刷新原表。','analytics_sync_busy':'已有官网同步正在执行，请等待原任务。','analytics_sync_unknown':'原同步结果尚未核验完整，保留原任务；不会重新写入。','analytics_restart_unknown':'同步被服务重启中断，请核验原任务，不重复执行。','analytics_identity_changed':'固定飞书身份与本人身份不一致，已停止同步。','analytics_lark_authorization_required':'固定飞书身份需要处理授权，原缓存保持。','analytics_lark_query_failed':'飞书统计原表读取失败，原缓存保持。','analytics_transport_failed':'统计服务连接或执行超时，原任务保持。','analytics_protocol_invalid':'统计服务返回格式异常，未标记为成功。','analytics_config_missing':'官网统计原表尚未配置。','analytics_config_invalid':'官网统计配置异常，原配置未修改。','analytics_readback_failed':'同步读回尚未核验通过，保留原同步任务。','analytics_operation_failed':'官网统计操作未完成，请核验原结果。','analytics_live_read_failed':'实时原表读取未通过，未将缓存标记为最新。'})

PLATFORM_HOSTS={'creator.xiaohongshu.com','creator.douyin.com','member.bilibili.com','cp.kuaishou.com','mp.weixin.qq.com','channels.weixin.qq.com'}
MIRROR_ROOT=Path(__file__).parent.parent
PUBLIC_SITE_ORIGIN=os.environ.get('CONTENTBASE_WEBSITE_URL','https://example.invalid').rstrip('/')
PUBLIC_SITE_PREFIXES=('/assets/','/api/','/profile/','/contact/','/lab/','/cases/','/feeds/','/events','/products-services','/cooperation','/guides','/en','/about','/resume','/join-us')
PUBLIC_SITE_FILES={'/profile.v1.json','/profile.v1.schema.json','/site.v1.json','/llms.txt','/llms-full.txt','/robots.txt','/sitemap.xml'}
def workspace_links(config):
    value=config.get('workspace_links') or {}
    if not isinstance(value,dict) or set(value)-{'tasks','content','videoScripts','articleScripts','reviews','bar','barFolder','website','platforms'}:raise ValueError('workspace_links_invalid')
    result={}
    for key in ('tasks','content','videoScripts','articleScripts','reviews','bar','barFolder','website'):
        raw=value.get(key)
        if raw is None:continue
        try:u=urllib.parse.urlsplit(raw)
        except Exception:raise ValueError('workspace_link_invalid')
        feishu=key in ('tasks','content','videoScripts','articleScripts','reviews','bar','barFolder') and (u.hostname or '').endswith('.feishu.cn')
        public=key=='website' and u.hostname in ('example.invalid','www.example.invalid')
        if u.scheme!='https' or u.username or u.password or u.fragment or not (feishu or public):raise ValueError('workspace_link_invalid')
        result[key]=raw
    platforms=value.get('platforms') or []
    if not isinstance(platforms,list) or len(platforms)>6:raise ValueError('workspace_platforms_invalid')
    clean=[]
    for item in platforms:
        if not isinstance(item,dict) or set(item)!= {'id','name','url'}:raise ValueError('workspace_platform_invalid')
        u=urllib.parse.urlsplit(item['url'])
        if u.scheme!='https' or u.username or u.password or u.hostname not in PLATFORM_HOSTS:raise ValueError('workspace_platform_invalid')
        if not re.fullmatch('[a-z0-9_-]{1,30}',item['id']) or not isinstance(item['name'],str) or not 1<=len(item['name'])<=20:raise ValueError('workspace_platform_invalid')
        clean.append(dict(item))
    result['platforms']=clean
    return result

def mirror_file(request_path,root=MIRROR_ROOT):
    path=urllib.parse.unquote(request_path)
    if '\x00' in path or '\\' in path:raise Problem(404,'not_found')
    relative='web/index.html' if path=='/' else ('node_modules/'+path[len('/node_modules/'): ] if path.startswith('/node_modules/') else 'web/'+path.lstrip('/'))
    target=(Path(root)/relative).resolve();base=Path(root).resolve()
    try:target.relative_to(base)
    except ValueError:raise Problem(404,'not_found')
    if not target.is_file() or target.suffix.lower() not in ('.html','.js','.css','.svg','.png','.glb','.json','.txt'):raise Problem(404,'not_found')
    return target

def browser_index(raw):
    adapter=b'<script src="browser-adapter.js"></script>'
    if adapter in raw:raw=raw.replace(adapter,b'<script src="analytics-transport.js"></script>'+adapter)
    needle=b"connect-src 'self'; object-src 'none'"
    if raw.count(needle)!=1:raise Problem(503,'not_found')
    raw=raw.replace(needle,b"connect-src 'self'; frame-src 'self' https:; object-src 'none'")
    raw=raw.replace(b"img-src 'self' data: https:",b"img-src 'self' data: blob: https:")
    if raw.count(b'</body>')!=1:raise Problem(503,'not_found')
    return raw.replace(b'</body>',b'<link rel="stylesheet" href="/publish-console.css"><script src="/publish-console.js"></script><link rel="stylesheet" href="/account-authorizations.css"><script src="/account-authorizations.js"></script></body>')

def public_site_target(path,query,frame_destination=''):
    if '\x00' in path or '\\' in path or not path.startswith('/'):return None
    pairs=urllib.parse.parse_qsl(query,keep_blank_values=True)
    embedded=any(k=='embedded_site' and v=='1' for k,v in pairs)
    clean_query=urllib.parse.urlencode([(k,v) for k,v in pairs if k!='embedded_site'])
    if path=='/':
        if not embedded and frame_destination!='iframe':return None
    elif path=='/assets/model.glb' or not (path in PUBLIC_SITE_FILES or path.startswith(PUBLIC_SITE_PREFIXES)):
        return None
    escaped=urllib.parse.quote(urllib.parse.unquote(path),safe='/%:@-._~!$&\'()*+,;=')
    return PUBLIC_SITE_ORIGIN+escaped+('?' + clean_query if clean_query else '')

def fetch_public_site(url,timeout=12,max_bytes=25*1024*1024):
    request=urllib.request.Request(url,headers={'User-Agent':'ContentBaseOffice/1.0','Accept':'*/*','Accept-Encoding':'identity'})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            length=response.headers.get('Content-Length')
            if length and int(length)>max_bytes:raise Problem(502,'not_found')
            body=response.read(max_bytes+1)
            if len(body)>max_bytes:raise Problem(502,'not_found')
            return response.status,response.headers.get_content_type(),body,response.headers.get('Cache-Control','no-store')
    except Problem:raise
    except (urllib.error.URLError,TimeoutError,ValueError):raise Problem(502,'not_found')

def read_snapshot(config,kind,days=14):
    root=Path(config.get('workspace_state_dir','')).resolve()
    expected=(Path.home()/'Library/Application Support/ERC Studio').resolve()
    if root!=expected or not root.is_dir() or root.is_symlink():raise Problem(503,'not_found')
    names={'accounts':'accounts.json','content':'content-cache.json','bar':'office-bar-snapshot.json','analytics':'analytics-cache-'+str(days)+'.json'}
    if kind not in names:raise Problem(400,'invalid_request')
    file=root/names[kind]
    try:
        if file.is_symlink() or file.stat().st_mode&0o077:raise ValueError()
        value=json.loads(file.read_text())
    except Exception:raise Problem(503,'not_found')
    if kind=='accounts':
        platforms=[{'id':x['id'],'name':x['name'],'short':x['name'][:1],'color':'#777','url':x['url']} for x in workspace_links(config)['platforms']]
        metrics=account_snapshot(root,value)
        return {'version':1,'accounts':metrics['accounts'],'browserMode':'web_native','groups':value.get('groups',[]),'platforms':platforms,'active':None,'metadataRefresh':metrics['metadataRefresh']}
    if not isinstance(value,dict):raise Problem(503,'not_found')
    value.pop('_actorId',None);value.pop('_cacheScope',None)
    value['stale']=True;value['refreshing']=False
    if kind=='content':value['phase']='cached';value['actor']={'name':'办公室只读','canWrite':False,'canApprove':False};value['message']='办公室只读快照；编辑请在飞书原表完成';value['viewKey']='office-shared-readonly'
    if kind=='analytics':value['phase']='cached';value['message']='办公室只读快照；最新数据以管理员同步时间为准'
    return value

def readonly_content(value,live=False):
    if not isinstance(value,dict):raise Problem(503,'not_found')
    value=dict(value);value.pop('_actorId',None);value.pop('_cacheScope',None)
    value['refreshing']=False;value['actor']={'name':'办公室只读','canWrite':False,'canApprove':False};value['viewKey']='office-shared-readonly'
    if live:value['phase']='ok';value['stale']=False;value['message']='已实时读取飞书原表'
    else:value['phase']='cached';value['stale']=True;value['message']='办公室只读缓存；正在后台核对飞书原表'
    return value

def load_config(path):
    p=Path(path).resolve()
    if p.stat().st_mode&0o077:raise ValueError('config_requires_0600')
    c=json.loads(p.read_text())
    if c.get('host')!='127.0.0.1' or not 1024<=c.get('port',0)<=65535:raise ValueError('loopback_listener_required')
    if (not isinstance(c.get('feishu_profile'),str) or not c['feishu_profile'] or c['feishu_profile'].startswith('YOUR_')) or len(c.get('auth_pepper',''))<32 or c.get('auth_pepper','').startswith('YOUR_'):raise ValueError('profile_or_secret_invalid')
    if not c.get('members') or len({m['tenant_key'] for m in c['members']})!=1 or len({m['handle'] for m in c['members']})!=len(c['members']) or len({m['principal_id'] for m in c['members']})!=len(c['members']):raise ValueError('members_invalid')
    for m in c['members']:
        uid(m['principal_id'])
        if not re.fullmatch('[a-z0-9_-]{1,40}',m['handle']) or not m['open_id'].startswith('ou_') or not m['chat_id'].startswith('oc_'):raise ValueError('member_binding_invalid')
    workspace_links(c)
    if c.get('unified_conversations',{}).get('enabled') and not c.get('unified_conversations',{}).get('execution'):
        raise ValueError('shared_context_execution_adapter_required')
    return c

def handler_for(service):
 class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.0'
    def log_message(self,*args):pass
    def setup(self):super().setup();self.connection.settimeout(15)
    def reply(self,status,data,extra=None):
        raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        for k,v in (extra or {}).items():self.send_header(k,v)
        if getattr(self,'studio_internal_request',False) and service.execution:
            nonce=self.headers.get('X-Studio-Nonce','')
            if re.fullmatch('[a-f0-9]{32}',nonce):
                for k,v in headers_for(read_key(service.execution.key_file),'RESPONSE',self.path,raw,nonce=nonce).items():
                    if k.startswith('X-Studio-'):self.send_header(k,v)
        self.end_headers();self.wfile.write(raw)
    def guard(self):
        host=self.headers.get('Host','').lower()
        if host not in service.config['allowed_hosts']:raise Problem(403,'origin_not_allowed')
        origin=self.headers.get('Origin')
        if origin and origin not in service.config['allowed_origins']:raise Problem(403,'origin_not_allowed')
        if self.headers.get('Transfer-Encoding'):raise Problem(400,'invalid_request')
    def body(self):
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise Problem(400,'invalid_request')
        try:size=int(self.headers.get('Content-Length','0'))
        except ValueError:raise Problem(400,'invalid_request')
        if not 0<size<=65536:raise Problem(413,'invalid_request')
        try:
            self.studio_raw_body=self.rfile.read(size)
            return json.loads(self.studio_raw_body.decode('utf-8'))
        except Exception:raise Problem(400,'invalid_request')
    def token(self):
        auth=self.headers.get('Authorization','')
        if auth.startswith('Bearer '):return auth[7:]
        try:return http.cookies.SimpleCookie(self.headers.get('Cookie',''))['studio_gateway_session'].value
        except (KeyError,http.cookies.CookieError):return ''
    def cookie(self,token,max_age):
        secure='; Secure' if self.headers.get('X-Forwarded-Proto')=='https' else ''
        return 'studio_gateway_session='+token+'; HttpOnly; SameSite=Strict; Path=/; Max-Age='+str(max_age)+secure
    def do_GET(self):self.dispatch('GET')
    def do_POST(self):self.dispatch('POST')
    def discard_rejected_upload(self,method):
        # Authentication has already rejected the request. Discard only a bounded
        # upload remainder so closing HTTP/1.0 does not turn its JSON error into
        # a TCP reset / nginx 502. No parsing, persistence or production call.
        if method!='POST' or self._upload_body_consumed or self.headers.get('Transfer-Encoding'):return
        try:
            if not re.fullmatch(r'/v1/(?:production/jobs/[a-f0-9-]{36}/source|publish/jobs/[a-f0-9-]{36}/files/[a-f0-9-]{36})',urllib.parse.urlsplit(self.path).path):return
            remaining=int(self.headers.get('Content-Length','0'))
            if not 0<remaining<=CHUNK:return
            while remaining:
                data=self.rfile.read(min(remaining,65536))
                if not data:break
                remaining-=len(data)
        except (ValueError,OSError,TimeoutError):pass
    def dispatch(self,method):
        self._upload_body_consumed=False
        self.studio_internal_request=False
        try:
            self.guard();parts=urllib.parse.urlsplit(self.path);path=parts.path;query=urllib.parse.parse_qs(parts.query);upload_route=method=='POST' and re.fullmatch(r'/v1/(?:production/jobs/[a-f0-9-]{36}/source|publish/jobs/[a-f0-9-]{36}/files/[a-f0-9-]{36})',path);body=self.body() if method=='POST' and not upload_route else None
            if path.startswith('/internal/studio/'):
                self.studio_internal_request=True
                if self.client_address[0]!='127.0.0.1' or method!='POST' or query or not service.execution:raise Problem(403,'studio_internal_origin_denied')
                verify_studio(read_key(service.execution.key_file),method,path,self.studio_raw_body,self.headers)
                self.reply(200,studio_dispatch(service,path,body));return
            public_target=public_site_target(path,parts.query,self.headers.get('Sec-Fetch-Dest','')) if method=='GET' else None
            if public_target:
                status,mime,raw,cache=fetch_public_site(public_target);self.send_response(status);self.send_header('Content-Type',mime+('; charset=utf-8' if mime.startswith('text/') or mime in ('application/javascript','application/json','application/xml') else ''));self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control',cache);self.send_header('Content-Security-Policy',"default-src 'self' data: https:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: https:; font-src 'self' data: https:; object-src 'none'; frame-ancestors 'self'; base-uri 'self'");self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','strict-origin-when-cross-origin');self.end_headers();self.wfile.write(raw);return
            if query and path not in ('/v1/conversations','/v1/workspace/snapshot','/v1/workflows/snapshot','/v1/radar/snapshot') and not re.fullmatch(r'/v1/conversations/[^/]+/messages',path):raise Problem(400,'invalid_request')
            if method=='GET' and (path=='/' or path in ('/browser-adapter.js','/browser-adapter.css','/studio-feishu-embed.zip','/publish-console.js','/publish-console.css','/account-authorizations.js','/account-authorizations.css') or path.startswith('/node_modules/') or re.fullmatch(r'/[A-Za-z0-9_./-]+\.(?:js|css|svg|png|glb|json|txt)',path)):
                try:target=(Path(__file__).parent/'web'/path.lstrip('/')) if path in ('/browser-adapter.js','/browser-adapter.css','/studio-feishu-embed.zip','/publish-console.js','/publish-console.css','/account-authorizations.js','/account-authorizations.css') else mirror_file(path)
                except Problem:target=None
                if target and target.is_file():
                    raw=target.read_bytes();raw=browser_index(raw) if path=='/' else raw;mime=mimetypes.guess_type(str(target))[0] or 'application/octet-stream';self.send_response(200);self.send_header('Content-Type',mime+('; charset=utf-8' if target.suffix in ('.html','.js','.css','.json','.txt') else ''));self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store' if target.suffix in ('.html','.js','.json','.zip') or target.name=='production.css' or target.name in ('publish-console.css','account-authorizations.css') else 'public, max-age=3600');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob: https:; frame-src 'self' https:; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw);return
            if method=='GET' and path=='/health':
                # Chat health must not advertise the video module's Codex model.
                capability={}
                model_reader=getattr(service.model,'execution_model',None)
                chat_model=model_reader() if service.execution and callable(model_reader) else service.config.get('model')
                self.reply(200,{'status':'ok','service':'studio-conversation','auth_mode':'feishu_device_oauth_and_private_otp','model':chat_model,'last_model_success':getattr(service.model,'last_success',None),'last_model_error':getattr(service.model,'last_error',None),'tools_enabled':bool(service.execution),'os_execution_sandbox':False,'execution_engine':'bridge' if service.execution else 'text-only','skills':capability.get('skills',[]),'worker_alive':service.worker.is_alive()},{'X-Cyber-Feishu-Embed':'enabled'} if self.headers.get('X-Cyber-Feishu-Embed')=='enabled' else None);return
            if method=='GET' and path=='/v1/auth/members':self.reply(200,{'items':[{'handle':m['handle'],'name':m['name']} for m in service.members.values() if m.get('active',True)]});return
            if method=='POST' and path=='/v1/auth/feishu/start':self.reply(201,service.oauth.start(body));return
            if method=='POST' and path=='/v1/auth/feishu/poll':
                answer=service.oauth.poll(body,service.grant_oauth)
                if answer['status']=='pending':self.reply(200,answer);return
                token=answer.pop('session_credential');extra={'Set-Cookie':self.cookie(token,service.config.get('session_seconds',43200))}
                if self.headers.get('X-Studio-Native')=='1' and not self.headers.get('Origin'):answer['session_credential']=token
                self.reply(200,answer,extra);return
            if method=='POST' and path=='/v1/auth/login/start':self.reply(201,service.start_login(body));return
            if method=='POST' and path=='/v1/auth/login/poll':
                answer=service.poll_login(body);token=answer.pop('session_credential');extra={'Set-Cookie':self.cookie(token,service.config.get('session_seconds',43200))}
                if self.headers.get('X-Studio-Native')=='1' and not self.headers.get('Origin'):answer['session_credential']=token
                self.reply(200,answer,extra);return
            if path=='/v1/auth/feishu/callback':raise Problem(503,'oauth_not_configured')
            token=self.token();owner=service.authenticate(token);key=self.headers.get('Idempotency-Key')
            if path.startswith('/v1/analytics/'):
                if not hasattr(service,'analytics'):service.analytics=AnalyticsGateway(service.config)
                analytics=service.analytics
                if method=='GET' and path=='/v1/analytics/state':self.reply(200,analytics.state(owner));return
                if method=='POST' and path=='/v1/analytics/sync':self.reply(202,analytics.start(owner,body,key));return
                analytics_job=re.fullmatch(r'/v1/analytics/jobs/([a-f0-9-]{36})(?:/(verify))?',path)
                if analytics_job:
                    jid,action=analytics_job.groups()
                    if method=='GET' and not action:self.reply(200,analytics.get(owner,jid));return
                    if method=='POST' and action=='verify':shape(body,[]);self.reply(200,analytics.verify(owner,jid));return
                raise Problem(404,'not_found')
            if path.startswith('/v1/account-authorizations'):
                if not hasattr(service,'account_authorizations'):service.account_authorizations=AccountAuthorizations(service.config)
                auth=service.account_authorizations;auth.permit(owner)
                if path=='/v1/account-authorizations':
                    if method=='GET':self.reply(200,auth.catalog(owner));return
                if path=='/v1/account-authorizations/preview' and method=='POST':self.reply(200,auth.preview(owner,body,key));return
                auth_match=re.fullmatch(r'/v1/account-authorizations/([a-f0-9-]{36})(?:/(submit))?',path)
                if auth_match:
                    aid,action=auth_match.groups()
                    if method=='GET' and not action:self.reply(200,auth.status(owner,aid));return
                    if method=='POST' and action=='submit':self.reply(202,auth.submit(owner,aid,body));return
                raise Problem(404,'not_found')
            if path.startswith('/v1/publish/'):
                if not hasattr(service,'publishing'):service.publishing=Publishing(service.config)
                publisher=service.publishing;publisher.permit(owner)
                if method=='GET' and path=='/v1/publish/catalog':self.reply(200,publisher.catalog(owner));return
                if method=='POST' and path=='/v1/publish/fields':self.reply(200,publisher.field_options(owner,body));return
                if path=='/v1/publish/jobs':
                    if method=='GET':self.reply(200,publisher.listing(owner));return
                    if method=='POST':self.reply(201,publisher.create(owner,body,key));return
                publish_match=re.fullmatch(r'/v1/publish/jobs/([a-f0-9-]{36})(?:/(prepare|submit|verify|cancel|files/([a-f0-9-]{36})))?',path)
                if publish_match:
                    jid,action,fid=publish_match.groups()
                    if method=='GET' and not action:self.reply(200,publisher.get(owner,jid));return
                    if method=='POST' and fid:
                        if self.headers.get('Content-Type')!='application/octet-stream':raise Problem(400,'invalid_request')
                        try:size=int(self.headers.get('Content-Length','0'));offset=int(self.headers.get('X-Upload-Offset','-1'))
                        except ValueError:raise Problem(400,'invalid_request')
                        if not 0<size<=PUBLISH_CHUNK:raise Problem(413,'invalid_request')
                        publisher.own(owner,jid);self._upload_body_consumed=True;chunk=self.rfile.read(size)
                        self.reply(200,publisher.upload(owner,jid,fid,offset,chunk,self.headers.get('X-Chunk-SHA256','')));return
                    if method=='POST' and action=='prepare':shape(body,[]);self.reply(202,publisher.prepare(owner,jid));return
                    if method=='POST' and action=='submit':self.reply(202,publisher.submit(owner,jid,body));return
                    if method=='POST' and action=='verify':shape(body,[]);self.reply(200,publisher.verify(owner,jid));return
                    if method=='POST' and action=='cancel':shape(body,[]);self.reply(200,publisher.cancel(owner,jid));return
                raise Problem(404,'not_found')
            if method=='GET' and path=='/v1/production/engine':self.reply(200,service.production.engine());return
            if method=='POST' and path=='/v1/production/jobs':self.reply(201,service.production.start(owner,body,key));return
            match=re.fullmatch(r'/v1/production/jobs/([a-f0-9-]{36})(?:/(source|start|video|video-nobgm|confirm|respond))?',path)
            if match:
                jid,action=match.groups()
                if method=='POST' and action=='source':
                    if self.headers.get('Content-Type')!='application/octet-stream':raise Problem(400,'invalid_request')
                    try:size=int(self.headers.get('Content-Length','0'));offset=int(self.headers.get('X-Upload-Offset','-1'))
                    except ValueError:raise Problem(400,'invalid_request')
                    if not 0<size<=CHUNK:raise Problem(413,'invalid_request')
                    self._upload_body_consumed=True;chunk=self.rfile.read(size)
                    self.reply(200,service.production.upload(owner,jid,offset,chunk,self.headers.get('X-Chunk-SHA256','')));return
                if method=='POST' and action=='respond':self.reply(202,service.production.respond(owner,jid,body,key));return
                if method=='POST' and action=='confirm':shape(body,[]);self.reply(200,service.production.confirm(owner,jid));return
                if method=='POST' and action=='start':shape(body,[]);self.reply(202,service.production.submit(owner,jid));return
                if method=='GET' and not action:self.reply(200,service.production.get(owner,jid));return
                if method=='GET' and action in ('video','video-nobgm'):self.production_video(service.production.video(owner,jid,'nobgm') if action=='video-nobgm' else service.production.video(owner,jid));return
            projects=re.fullmatch(r'/v1/production/projects/(rec[A-Za-z0-9]{8,48})/jobs',path)
            if method=='GET' and projects:self.reply(200,service.production.list(owner,projects[1]));return
            if method=='POST' and path=='/v1/radar/article':
                shape(body,['key','kind'],['key','kind'])
                if not hasattr(service,'radar'):service.radar=RadarRefresher(service.config)
                if not service.radar.can_read(owner):raise Problem(403,'radar_draft_owner_required')
                try:self.reply(200,service.radar.article(body['key'],body['kind']))
                except RadarRefreshError as error:raise Problem(503,str(error))
                return
            if method=='GET' and path=='/v1/radar/snapshot':
                if set(query)-{'refresh'} or query.get('refresh',['0'])[0] not in ('0','1'):raise Problem(400,'invalid_request')
                if query.get('refresh',['0'])[0]=='1':
                    if not hasattr(service,'radar'):service.radar=RadarRefresher(service.config)
                    try:self.reply(200,{**service.radar.refresh(),'canReadDraft':service.radar.can_read(owner)})
                    except RadarRefreshError as error:raise Problem(503,str(error))
                    return
                if not hasattr(service,'radar'):service.radar=RadarRefresher(service.config)
                self.reply(200,{**service.radar.cached(),'canReadDraft':service.radar.can_read(owner)});return
            if method=='GET' and path=='/v1/me':self.reply(200,service.info(owner));return
            if method=='GET' and path=='/v1/workflows/snapshot':self.reply(200,workflow_snapshot(service,owner,query));return
            if method=='GET' and path=='/v1/workspace/links':self.reply(200,workspace_links(service.config));return
            if method=='GET' and path=='/v1/workspace/snapshot':
                if set(query)-{'kind','days','refresh'} or len(query.get('kind',[]))!=1 or len(query.get('refresh',['0']))!=1 or query.get('refresh',['0'])[0] not in ('0','1'):raise Problem(400,'invalid_request')
                try:days=int(query.get('days',['14'])[0])
                except ValueError:raise Problem(400,'invalid_request')
                if days not in (7,14,30,90):raise Problem(400,'invalid_request')
                kind=query['kind'][0]
                if kind=='analytics':
                    if not hasattr(service,'analytics'):service.analytics=AnalyticsGateway(service.config)
                    if query.get('refresh',['0'])[0]=='1':self.reply(200,service.analytics.read(owner,days));return
                    value=read_snapshot(service.config,kind,days);value['canSync']=owner in service.analytics.allowed;self.reply(200,value);return
                if kind=='content' and query.get('refresh',['0'])[0]=='1':
                    try:value=service.content_refresher.refresh()
                    except ContentRefreshError as error:raise Problem(503,str(error))
                    self.reply(200,readonly_content(value,live=True));return
                self.reply(200,read_snapshot(service.config,kind,days));return
            if method=='POST' and path=='/v1/auth/logout':shape(body,[]);service.key(key);self.reply(200,service.logout(token),{'Set-Cookie':self.cookie('',0)});return
            artifact=re.fullmatch(r'/v1/codex/([^/]+)/files/(.+)',path)
            if method=='GET' and artifact:
                cid,name=artifact.groups();service.conversation_store.get(owner,cid)
                if not service.execution or not service.execution.local:raise Problem(404,'not_found')
                import pathlib
                name=urllib.parse.unquote(name);relative=pathlib.Path(name)
                if relative.is_absolute() or '..' in relative.parts or any(x.startswith('.') for x in relative.parts):raise Problem(400,'invalid_request')
                from local_codex import owned_file
                work=service.execution.local.workspace(owner,cid);file=work/'edit/artifacts'/relative
                if not owned_file(work,file) or not file.is_file() or file.stat().st_size>512*1024*1024:raise Problem(404,'not_found')
                if file.suffix.lower()=='.mp4':self.production_video(file);return
                raw=file.read_bytes();mime={'.json':'application/json','.txt':'text/plain','.md':'text/plain','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.wav':'audio/wav','.mp3':'audio/mpeg'}.get(file.suffix.lower(),'application/octet-stream')
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw);return
            if method=='POST' and path=='/v1/conversations/start-message':self.reply(202,service.start_message(owner,body,key,'app' if self.headers.get('X-Studio-Native')=='1' and not self.headers.get('Origin') else 'web'));return
            if path=='/v1/conversations/active' and method=='GET':
                if not service.unified_enabled:raise Problem(409,'shared_context_not_enabled')
                self.reply(200,service.unified.active(owner));return
            continuation=re.fullmatch(r'/v1/conversations/([^/]+)/continue',path)
            if continuation and method=='POST':
                if not service.unified_enabled:raise Problem(409,'shared_context_not_enabled')
                self.reply(200,service.unified.activate(owner,continuation[1],body,key));return
            if path=='/v1/conversations':
                if method=='GET':
                    limit,offset,scope,search,cursor=self.conversation_page(query);self.reply(200,service.conversations(owner,limit,offset,scope,search,cursor));return
                if method=='POST':self.reply(201,service.create_conversation(owner,body,key));return
            detail=re.fullmatch(r'/v1/conversations/([^/]+)',path)
            if detail and method=='GET':
                with service.lock:answer=service.conversation_store.dto(service.conversation_store.get(owner,detail[1],include_deleted=True))
                self.reply(200,answer);return
            lifecycle=re.fullmatch(r'/v1/conversations/([^/]+)/(rename|pin|archive|trash|restore)',path)
            if lifecycle and method=='POST':self.reply(200,service.update_conversation(owner,lifecycle[1],lifecycle[2],body,key));return
            match=re.fullmatch(r'/v1/conversations/([^/]+)/messages',path)
            if match:
                if method=='GET':
                    limit,offset,before=self.message_page(query);self.reply(200,service.messages(owner,match[1],limit,offset,before));return
                if method=='POST':self.reply(202,service.submit(owner,match[1],body,key,'app' if self.headers.get('X-Studio-Native')=='1' and not self.headers.get('Origin') else 'web'));return
            match=re.fullmatch(r'/v1/jobs/([^/]+)(?:/(events|cancel|retry|respond|recover))?',path)
            if match:
                jid,action=match.groups()
                if method=='GET' and not action:self.reply(200,service.job(owner,jid));return
                if method=='POST' and action=='cancel':shape(body,[]);self.reply(202,service.cancel(owner,jid,key));return
                if method=='POST' and action=='retry':self.reply(202,service.retry_job(owner,jid,body,key));return
                if method=='POST' and action=='respond':self.reply(202,service.respond_job(owner,jid,body,key,'app' if self.headers.get('X-Studio-Native')=='1' and not self.headers.get('Origin') else 'web'));return
                if method=='POST' and action=='recover':shape(body,[]);self.reply(200,service.recover_job(owner,jid,key));return
                if method=='GET' and action=='events':self.sse(owner,token,jid);return
            raise Problem(404,'not_found')
        except ProtocolError as error:
            self.reply(error.status or 400,{'code':error.code,'execution_started':False})
        except CLIError as error:
            self.reply(502,{'code':error.code,'message':error.message,'stage':error.stage})
        except Problem as error:
            self.discard_rejected_upload(method)
            self.reply(error.status,{'code':error.code,'message':ERRORS.get(error.code,'服务请求未完成：'+error.code),'request_id':str(uuid.uuid4()),**({'execution_started':False} if self.studio_internal_request else {})})
        except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
        except Exception:
            try:self.reply(500,{'code':'internal_error','message':'服务请求未完成，请查询原任务。','request_id':str(uuid.uuid4())})
            except (BrokenPipeError,ConnectionResetError):pass
    def production_video(self,path):
        size=path.stat().st_size;start,end=0,size-1;header=self.headers.get('Range')
        if header:
            match=re.fullmatch(r'bytes=(\d+)-(\d*)',header)
            if not match:raise Problem(416,'invalid_request')
            start=int(match[1]);end=int(match[2]) if match[2] else size-1
            if start>=size or end<start or end>=size:raise Problem(416,'invalid_request')
        self.send_response(206 if header else 200)
        self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(end-start+1))
        self.send_header('Cache-Control','private, no-store');self.send_header('Accept-Ranges','bytes')
        self.send_header('Content-Disposition','inline; filename="studio-cut.mp4"')
        self.send_header('X-Content-Type-Options','nosniff')
        if header:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        with path.open('rb') as source:
            source.seek(start);remaining=end-start+1
            while remaining:
                block=source.read(min(65536,remaining))
                if not block:break
                self.wfile.write(block);remaining-=len(block)
    def conversation_page(self,query):
        if set(query)-{'limit','cursor','scope','q'} or any(len(v)!=1 for v in query.values()):raise Problem(400,'invalid_request')
        try:limit=int(query.get('limit',['30'])[0])
        except ValueError:raise Problem(400,'invalid_request')
        cursor=query.get('cursor',[None])[0];offset=0
        if cursor is not None:
            if len(cursor)>256:raise Problem(400,'invalid_cursor')
            if cursor.isdigit():offset=int(cursor);cursor=None
        if not 1<=limit<=100 or not 0<=offset<=1000000:raise Problem(400,'invalid_request')
        return limit,offset,query.get('scope',['active'])[0],query.get('q',[''])[0],cursor
    def message_page(self,query):
        if set(query)-{'limit','cursor','before'} or any(len(v)!=1 for v in query.values()):raise Problem(400,'invalid_request')
        try:
            limit=int(query.get('limit',['100'])[0]);cursor=query.get('cursor',['latest'])[0];offset=-1 if cursor=='latest' else int(cursor);before=int(query['before'][0]) if 'before' in query else None
        except ValueError:raise Problem(400,'invalid_request')
        if not 1<=limit<=100 or not -1<=offset<=1000000 or (before is not None and before<1):raise Problem(400,'invalid_request')
        return limit,offset,before
    def sse(self,owner,token,jid):
        service.job(owner,jid)
        try:after=int(self.headers.get('Last-Event-ID','0'))
        except ValueError:raise Problem(400,'invalid_request')
        if not 0<=after<=1000000:raise Problem(400,'invalid_request')
        self.send_response(200);self.send_header('Content-Type','text/event-stream; charset=utf-8');self.send_header('Cache-Control','no-store');self.end_headers()
        end=time.monotonic()+20
        while time.monotonic()<end:
            try:service.authenticate(token)
            except Problem:break
            for event in service.events(owner,jid,after):self.wfile.write(('id: '+str(event['seq'])+'\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\n').encode());after=event['seq']
            self.wfile.flush()
            if service.job(owner,jid)['state'] in ('succeeded','failed','cancelled','execution_unknown'):break
            time.sleep(.3)
 return Handler

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args();config=load_config(args.config)
    service=Service(config,ModelProxy(config),FeishuCards(config));service.oauth=DeviceOAuth(config);service.production=Production(config);service.publishing=Publishing(config);service.content_refresher=ContentRefresher(config);service.analytics=AnalyticsGateway(config);server=ThreadingHTTPServer((config['host'],config['port']),handler_for(service));server.daemon_threads=True
    def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:print(json.dumps({'status':'running','url':'http://127.0.0.1:'+str(config['port']),'auth_mode':'feishu_device_oauth_and_private_otp','tools_enabled':False}),flush=True);server.serve_forever(poll_interval=.2)
    finally:server.server_close();service.close()
if __name__=='__main__':main()
