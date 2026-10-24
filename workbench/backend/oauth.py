"""Official CLI Device Flow, isolated from production UAT/config on macOS.

v1.0.90 stores encrypted credentials under HOME/Library/Application Support/lark-cli,
not CONFIG_DIR. System Keychain lookup also depends on HOME. Each flow therefore gets both a new HOME and CONFIG_DIR. Only the
application-secret encrypted file and its existing file-key (if present) are referenced
read-only via symlinks; no production user tokens are copied. No business scopes.
"""
import base64, hmac, json, os, re, secrets, subprocess, threading, time, urllib.parse, uuid
from pathlib import Path
from core import Problem, digest, shape, uid

class DeviceOAuth:
    def __init__(self,config,runner=subprocess.run,clock=time.time):
        self.config=config;self.runner=runner;self.clock=clock;self.lock=threading.RLock();self.flows={};self.last_start=0
        self.root=Path(config['state_dir']).parent/'oauth';self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700)
        self.cli=config['lark_cli'];self.profile=config['feishu_profile']
    def environment(self,directory):
        env={k:v for k,v in os.environ.items() if not k.startswith('LARKSUITE_CLI_')}
        env.update(HOME=str(directory),PATH='/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin',LARKSUITE_CLI_CONFIG_DIR=str(directory/'config'),LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1',LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
        return env
    def prepare(self,directory):
        original=Path(self.config.get('lark_home','/Users/USER'))
        raw=json.loads((original/'.lark-cli/config.json').read_text());app=next(a for a in raw['apps'] if a['appId']==self.profile)
        if app.get('appSecret')!={'source':'keychain','id':'appsecret'+':'+self.profile}:raise Problem(503,'oauth_credential_reference_invalid')
        directory.mkdir(mode=0o700);config=directory/'config';config.mkdir(mode=0o700)
        selected={k:app[k] for k in ('appId','appSecret','brand')};selected.update(name=self.profile,users=[])
        p=config/'config.json';p.write_text(json.dumps({'currentApp':self.profile,'apps':[selected]}));p.chmod(0o600)
        source=original/'Library/Application Support/lark-cli';target=directory/'Library/Application Support/lark-cli';target.mkdir(parents=True,mode=0o700)
        # security(1) resolves the default Keychain through HOME; reference the existing
        # Keychain directory, without copying/exporting or changing its entries.
        (directory/'Library/Keychains').symlink_to(original/'Library/Keychains',target_is_directory=True)
        for name in ['appsecret_'+self.profile+'.enc','master.key.file']:
            src=source/name
            if src.exists():
                if src.stat().st_mode&0o077:raise Problem(503,'oauth_credential_reference_invalid')
                (target/name).symlink_to(src)
        if not (target/('appsecret_'+self.profile+'.enc')).exists():raise Problem(503,'oauth_credential_reference_invalid')
    def command(self,directory,args,timeout=35):
        try:
            p=self.runner([self.cli,'--profile',self.profile,*args],capture_output=True,text=True,timeout=timeout,env=self.environment(directory),cwd=str(directory))
            if p.returncode:raise Problem(503,'oauth_cli_failed')
            data=json.loads(p.stdout)
            if data.get('ok') is False:raise Problem(503,'oauth_cli_failed')
            return data
        except Problem:raise
        except subprocess.TimeoutExpired:raise Problem(503,'oauth_expired')
        except Exception:raise Problem(503,'oauth_cli_failed')
    def start(self,body):
        shape(body,['client_challenge','device_id'],['client_challenge','device_id']);uid(body['device_id'])
        challenge=body['client_challenge']
        if not isinstance(challenge,str) or not re.fullmatch('[a-f0-9]{64}',challenge):raise Problem(400,'invalid_request')
        with self.lock:
            now=self.clock()
            self.flows={k:v for k,v in self.flows.items() if v['expires']>now}
            if self.last_start+10>now or len(self.flows)>=4:raise Problem(429,'rate_limited')
            self.last_start=now;fid=str(uuid.uuid4());directory=self.root/fid;self.prepare(directory)
            data=self.command(directory,['auth','login','--scope','offline_access','--no-wait','--json'])
            url=data.get('verification_url','');parsed=urllib.parse.urlsplit(url)
            if parsed.scheme!='https' or parsed.hostname not in ('accounts.feishu.cn','open.feishu.cn') or not isinstance(data.get('device_code'),str):raise Problem(503,'oauth_response_invalid')
            self.command(directory,['auth','qrcode',url,'--output','qrcode.png'])
            png=(directory/'qrcode.png').read_bytes()
            if not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png)>500000:raise Problem(503,'oauth_response_invalid')
            expiry=now+max(1,min(int(data['expires_in']),900));self.flows[fid]={'challenge':challenge,'directory':directory,'device_code':data['device_code'],'expires':expiry,'state':'pending','identity':None,'error':None}
            return {'login_id':fid,'verification_url':url,'qrcode_data_url':'data:image/png;base64,'+base64.b64encode(png).decode(),'expires_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(expiry)),'status':'pending','scope':'offline_access'}
    def proof(self,body):
        shape(body,['login_id','client_verifier'],['login_id','client_verifier']);uid(body['login_id']);v=body['client_verifier']
        if not isinstance(v,str) or not 43<=len(v)<=128:raise Problem(400,'invalid_request')
        f=self.flows.get(body['login_id'])
        if not f or f['expires']<=self.clock() or f['state']=='consumed' or not hmac.compare_digest(f['challenge'],digest(v)):raise Problem(401,'oauth_expired')
        return f
    def finish(self,f):
        try:
            self.command(f['directory'],['auth','login','--device-code',f['device_code'],'--json'],timeout=max(1,int(f['expires']-self.clock())))
            packet=self.command(f['directory'],['api','GET','/open-apis/authen/v1/user_info','--as','user','--format','json'])
            identity=packet['data']
            if packet.get('ok') is not True or not isinstance(identity.get('open_id'),str) or not identity.get('tenant_key'):raise Problem(503,'oauth_identity_invalid')
            with self.lock:f['identity']={k:identity[k] for k in ('open_id','tenant_key')};f['state']='verified'
        except Exception as e:
            with self.lock:f['error']=e.code if isinstance(e,Problem) else 'oauth_cli_failed';f['state']='failed'
        finally:
            f['device_code']=''
            try:self.command(f['directory'],['auth','logout','--json'])
            except Problem:pass # No token is exposed; any leftover is confined to this private flow HOME.
    def poll(self,body,grant):
        with self.lock:
            f=self.proof(body)
            if f['state']=='pending':
                f['state']='polling';threading.Thread(target=self.finish,args=(f,),daemon=True,name='studio-oauth').start()
            if f['state']=='failed':raise Problem(503,f['error'])
            if f['state']=='verified':
                try:answer=grant(f['identity'])
                finally:f['state']='consumed';f['identity']=None
                return answer
            return {'status':'pending'}
