import json,hashlib,tempfile,subprocess,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bridge_native_feishu import NativeUserCLI, NativeFeishuAgent, NativeAuthorizationRequired, PROFILE, CLI, DOMAINS, redact
from bridge_cherry import ExecutionRejected,ExecutionUnknown
from bridge_state import StateStore


class NativeFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name)/'userA';self.directory.mkdir()
        self.prepare(self.directory,'ou_A')
        self.calls=[];self.actor_id='ou_A';self.write_fail=False;self.bot_result=False;self.risk_level='write'
        self.cli=NativeUserCLI('ou_A',self.directory,self.runner)
        self.event={'event_id':'ev-one','chat_id':'oc_A','sender_id':'ou_A'}

    def tearDown(self):self.tmp.cleanup()

    def prepare(self,directory,user):
        root=directory/'native';(root/'cli').mkdir(parents=True)
        (root/'binding.json').write_text(json.dumps({'user_open_id':user,'profile':PROFILE}))
        (root/'cli/config.json').write_text(json.dumps({'apps':[{'appId':PROFILE,'users':[{'userOpenId':user}]}]}))

    def runner(self,argv,**kw):
        self.calls.append((argv,kw))
        args=argv[3:]
        if args[:2]==['auth','status']:
            p={'identity':'user','verified':True,'identities':{'user':{'tokenStatus':'valid','openId':self.actor_id}}}
        elif args[:2]==['auth','login']:
            if '--device-code' in args:
                config=self.cli.config/'config.json';c=json.loads(config.read_text());c['apps'][0]['users']=[{'userOpenId':self.actor_id}];config.write_text(json.dumps(c));p={'ok':True}
            else:p={'device_code':'PRIVATE_DEVICE','verification_url':'https://accounts.feishu.cn/fixture-auth','expires_in':600}
        elif args[:2]==['auth','qrcode']:
            (self.cli.work/'authorization.png').write_bytes(b'fixture-png');p={'ok':True}
        elif '--help' in args:
            return SimpleNamespace(returncode=0,stdout='Risk: '+self.risk_level+'\nUsage: fixture',stderr='')
        elif '--dry-run' in args:
            p={'ok':True,'identity':'user','preview':{'method':'POST','resource':'own-resource'}}
        else:
            if self.write_fail:raise subprocess.TimeoutExpired(argv,45)
            p={'ok':True,'identity':'bot' if self.bot_result else 'user','data':{'id':'OWN_RESOURCE','access_token':'YOUR_PRIVATE_TOKEN'}}
        return SimpleNamespace(returncode=0,stdout=json.dumps(p),stderr='')

    def actual_business_calls(self):
        return [argv for argv,_ in self.calls if argv[3] in DOMAINS and '--help' not in argv and '--dry-run' not in argv]


class NativeBoundaryTests(NativeFixture):
    def test_own_home_config_tmp_and_no_inherited_owner_env(self):
        self.risk_level='read';r=self.cli.execute(['docs','+get','--doc','docA'],self.event)
        argv,kw=self.calls[-1]
        self.assertEqual(argv[:3],[CLI,'--profile',PROFILE]);self.assertEqual(argv[-4:],['--as','user','--format','json'])
        self.assertEqual(kw['env']['HOME'],str(self.cli.home));self.assertEqual(kw['env']['LARKSUITE_CLI_CONFIG_DIR'],str(self.cli.config))
        self.assertEqual(kw['cwd'],self.cli.work);self.assertEqual(kw['stdin'],subprocess.DEVNULL)
        self.assertNotIn('FEISHU_APP_SECRET',kw['env']);self.assertNotIn('LARKSUITE_CLI_USER_ACCESS_TOKEN',kw['env'])
        self.assertNotIn('YOUR_PRIVATE_TOKEN',json.dumps(r))

    def test_mismatched_config_user_stops_before_cli(self):
        p=self.cli.config/'config.json';c=json.loads(p.read_text());c['apps'][0]['users']=[{'userOpenId':'ou_OWNER'}];p.write_text(json.dumps(c))
        with self.assertRaises(ExecutionRejected):self.cli.execute(['base','+table-list','--base-token','baseA'],self.event)
        self.assertEqual(self.calls,[])

    def test_remote_identity_mismatch_stops_business(self):
        self.actor_id='ou_B'
        with self.assertRaises(ExecutionRejected):self.cli.execute(['calendar','+agenda'],self.event)
        self.assertEqual(self.actual_business_calls(),[])

    def test_bot_identity_results_are_not_delivered_to_model(self):
        self.bot_result=True;self.risk_level='read'
        with self.assertRaises(ExecutionRejected):self.cli.execute(['docs','+get','--doc','own'],self.event)

    def test_management_id_override_shell_and_profile_override_denied(self):
        bad=[['config','show'],['auth','list'],['profile','list'],['update'],['doctor'],['application','+get'],['event','consume'],['apps','+run'],['im','+chat-list','--as','bot'],['docs','+get','--profile=owner'],['drive','+delete','--yes'],['base','+table-list','--header','Authorization: Bearer other']]
        for args in bad:
            with self.subTest(args=args),self.assertRaises(ExecutionRejected):self.cli.execute(args,self.event)
        self.assertEqual(self.calls,[])

    def test_help_cannot_disguise_mutation_with_flag_value(self):
        for args in [['docs','+create','--title','--help'],['api','POST','/open-apis/drive/v1/files','--help']]:
            with self.assertRaises(ExecutionRejected):self.cli.execute(args,self.event)
        self.assertEqual(self.calls,[])

    def test_official_metadata_without_business_authorization(self):
        p=self.cli.config/'config.json';c=json.loads(p.read_text());c['apps'][0]['users']=[];p.write_text(json.dumps(c))
        self.cli.execute(['base','--help'],self.event)
        self.assertEqual(len(self.calls),1);self.assertEqual(self.calls[0][0][-2:],['base','--help'])
        self.assertNotIn('auth',self.calls[0][0])

    def test_all_native_personal_domains_use_same_gate(self):
        self.risk_level='read'
        for domain in DOMAINS:self.cli.execute([domain,'+fixture'],self.event)
        for argv in self.actual_business_calls():self.assertIn('user',argv);self.assertIn(PROFILE,argv)
        self.assertEqual(len(self.actual_business_calls()),len(DOMAINS))

    def test_raw_api_business_allowed_control_and_traversal_denied(self):
        self.cli.execute(['api','GET','/open-apis/base/v3/bases/own'],self.event)
        for path in ['/open-apis/authen/v1/user_info','/open-apis/application/v6/secrets','/open-apis/drive/../authen/token','https://evil.example','/open-apis/base/%2f../token']:
            with self.assertRaises(ExecutionRejected):self.cli.execute(['api','GET',path],self.event)

    def test_local_owner_credentials_and_cross_user_paths_denied(self):
        for value in ['/Users/USER/.lark-cli/config.json','../native/cli/config.json','~/Library/Application Support/lark-cli/token.enc','file:///etc/passwd','@../../owner']:
            with self.assertRaises(ExecutionRejected):self.cli.execute(['drive','+upload','--file',value],self.event)
        other=Path(self.tmp.name)/'owner-secret';other.write_text('OWNER');(self.cli.work/'link').symlink_to(other)
        with self.assertRaises(ExecutionRejected):self.cli.execute(['drive','+upload','--file','link'],self.event)
        self.assertEqual(self.actual_business_calls(),[])

    def test_write_is_preview_only_until_exact_human_approval(self):
        result=self.cli.execute(['docs','+create','--title','Own doc'],self.event)
        self.assertTrue(result['needs_confirmation']);self.assertEqual(self.actual_business_calls(),[])
        self.cli.confirm(result['approval_id'],dict(self.event,event_id='human-confirm'))
        self.assertEqual(len(self.actual_business_calls()),1)
        self.assertNotIn('--yes',self.actual_business_calls()[0])

    def test_high_risk_confirmation_is_added_only_by_human_path(self):
        self.risk_level='high-risk-write'
        result=self.cli.execute(['drive','+delete','--file-token','OWN'],self.event)
        self.assertNotIn('--yes',self.calls[-1][0])
        self.cli.confirm(result['approval_id'],dict(self.event,event_id='human-confirm'))
        self.assertEqual(self.actual_business_calls()[0][-1],'--yes')

    def test_confirmation_wrong_chat_and_duplicate_are_not_executed(self):
        result=self.cli.execute(['docs','+create','--title','Own'],self.event)
        with self.assertRaises(ExecutionRejected):self.cli.confirm(result['approval_id'],dict(self.event,chat_id='oc_B'))
        self.assertEqual(self.actual_business_calls(),[])
        first=self.cli.confirm(result['approval_id'],self.event);second=self.cli.confirm(result['approval_id'],self.event)
        self.assertEqual(first,second);self.assertEqual(len(self.actual_business_calls()),1)

    def test_cross_user_approval_code_is_not_accepted(self):
        result=self.cli.execute(['docs','+create','--title','Own'],self.event)
        directory=Path(self.tmp.name)/'userB';self.prepare(directory,'ou_B');other=NativeUserCLI('ou_B',directory,self.runner);self.actor_id='ou_B'
        with self.assertRaises(ExecutionRejected):other.confirm(result['approval_id'],dict(self.event,sender_id='ou_B'))
        self.assertEqual(self.actual_business_calls(),[])

    def test_write_uncertainty_is_durable_and_never_replayed(self):
        result=self.cli.execute(['docs','+create','--title','Own'],self.event);self.write_fail=True
        with self.assertRaises(ExecutionUnknown):self.cli.confirm(result['approval_id'],self.event)
        count=len(self.actual_business_calls())
        with self.assertRaises(ExecutionUnknown):self.cli.confirm(result['approval_id'],self.event)
        self.assertEqual(count,len(self.actual_business_calls()))
        with self.cli.connect() as db:self.assertEqual(db.execute('SELECT state FROM actions').fetchone()[0],'unknown')

    def test_same_event_plan_is_idempotent(self):
        a=self.cli.execute(['docs','+create','--title','Own'],self.event);b=self.cli.execute(['docs','+create','--title','Own'],self.event)
        self.assertEqual(a['approval_id'],b['approval_id'])

    def test_manual_dry_run_does_not_create_pending_write(self):
        result=self.cli.execute(['docs','+create','--title','Own','--dry-run'],self.event)
        self.assertTrue(result['preview_only']);self.assertEqual(self.actual_business_calls(),[])
        with self.cli.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM actions').fetchone()[0],0)

    def test_missing_authorization_has_own_flow_no_owner_token_fallback(self):
        p=self.cli.config/'config.json';c=json.loads(p.read_text());c['apps'][0]['users']=[];p.write_text(json.dumps(c))
        with self.assertRaises(NativeAuthorizationRequired) as caught:self.cli.execute(['calendar','+agenda'],self.event)
        self.assertEqual(caught.exception.user,'ou_A');self.assertEqual(caught.exception.flow['user_open_id'],'ou_A')
        self.assertEqual(self.actual_business_calls(),[])
        self.assertEqual((self.cli.root/'auth.json').stat().st_mode & 0o777,0o600)
        self.assertTrue((self.cli.work/'authorization.png').exists())
        self.assertIn('--domain',self.calls[0][0]);self.assertIn('all',self.calls[0][0])

    def test_authorization_flow_reuses_same_valid_nonce_and_collects_pending(self):
        f=self.cli.start_auth(self.event);g=self.cli.start_auth(dict(self.event,event_id='ev-two'))
        self.assertEqual(f['id'],g['id']);self.assertEqual(len(g['pending']),2)
        self.assertEqual(sum('--no-wait' in argv for argv,_ in self.calls),1)

    def test_authorization_success_and_wrong_scanner_are_verified(self):
        flow=self.cli.start_auth(self.event);self.cli.monitor_auth()
        if self.cli.auth_thread:self.cli.auth_thread.join(2)
        ready=self.cli.monitor_auth();self.assertEqual(ready['status'],'ready')
        self.cli.acknowledge_auth(['ev-one']);self.assertEqual(json.loads((self.cli.root/'auth.json').read_text())['pending'],[])
        path=self.cli.root/'auth.json';path.unlink();self.actor_id='ou_OWNER';self.cli.start_auth(self.event);self.cli.monitor_auth()
        if self.cli.auth_thread:self.cli.auth_thread.join(2)
        self.assertEqual(self.cli.monitor_auth()['status'],'failed')

    def test_pending_auth_is_a_real_waiting_state_not_retried(self):
        store=StateStore(Path(self.tmp.name)/'bridge.db');e={'event_id':'ev','message_id':'m','chat_id':'oc_A','sender_id':'ou_A','content':'hello'}
        store.add_event(e);store.claim_event('ev');store.wait_isolated_auth('ev')
        self.assertEqual(store.metrics()['pending_event_count'],1);self.assertEqual(store.retryable(5),[])
        self.assertTrue(store.resume_isolated_auth('ev'));self.assertFalse(store.resume_isolated_auth('ev'))


class NativeModelTests(unittest.TestCase):
    def test_tool_protocol_loop_executes_only_bound_cli(self):
        client=mock.Mock();client.tool_turn.side_effect=[{'tool_calls':[{'id':'call1','type':'function','function':{'name':'feishu_cli','arguments':'{"args":["base","--help"]}'}}]},{'content':'工具已返回原生帮助。'}]
        agent=NativeFeishuAgent.__new__(NativeFeishuAgent);agent.client=client;context=mock.Mock();context.execute.return_value={'exit_status':0,'output':{'text':'native help'}};agent.contexts={'ou_A':context}
        result=agent.run('ou_A',{'event_id':'ev','chat_id':'oc_A'},[{'role':'system','content':'own'},{'role':'user','content':'帮助'}])
        self.assertEqual(result,'工具已返回原生帮助。');context.execute.assert_called_once_with(['base','--help'],{'event_id':'ev','chat_id':'oc_A'})
        self.assertEqual(client.tool_turn.call_args[0][0][-1]['role'],'tool')

    def test_model_cannot_confirm_write_through_tool_arg(self):
        client=mock.Mock();client.tool_turn.return_value={'tool_calls':[{'id':'c','type':'function','function':{'name':'feishu_cli','arguments':'{"args":["docs","+create"]}'}}]}
        agent=NativeFeishuAgent.__new__(NativeFeishuAgent);agent.client=client;context=mock.Mock();context.execute.return_value={'needs_confirmation':True,'approval_id':'012345abcdef'};agent.contexts={'ou_A':context}
        response=agent.run('ou_A',{'event_id':'ev','chat_id':'oc_A'},[{'role':'user','content':'建文档'}])
        self.assertIn('012345abcdef',response);context.confirm.assert_not_called();self.assertEqual(client.tool_turn.call_count,1)

    def test_redaction_removes_system_credentials_but_keeps_resource_tokens(self):
        value=redact({'appSecret':'YOUR_SECRET','access_token':'YOUR_OWNER_TOKEN','refresh_token':'R','device_code':'D','base_token':'OWN_BASE','data':{'text':'Bearer ABC'}})
        self.assertNotIn('YOUR_OWNER_TOKEN',json.dumps(value));self.assertEqual(value['base_token'],'OWN_BASE');self.assertNotIn('Bearer ABC',json.dumps(value))


if __name__=='__main__':unittest.main()


class NativeBridgeTests(unittest.TestCase):
    def setUp(self):
        import test_bridge as fixtures
        fixtures.BridgeTests.setUp(self)
        from bridge_isolated import IsolatedConversations
        self.policy={'user_ids':['ou_A'],'private_chats':{'oc_A':'ou_A'},'storage_root':str(self.root/'isolated')}
        self.bridge.isolated_users={'ou_A'};self.bridge.isolated_private_chats={'oc_A':'ou_A'};self.bridge.allowed_chats.add('oc_A')
        self.bridge.isolated_handler=IsolatedConversations(self.policy,self.cherry)
        self.event_value={'event_id':'native-ev','message_id':'m-native','sender_id':'ou_A','chat_id':'oc_A','chat_type':'p2p','message_type':'text','content':'读自己的表'}
        self.bridge._send_native_authorization=mock.Mock()

    def tearDown(self):
        import test_bridge as fixtures
        fixtures.BridgeTests.tearDown(self)

    def row(self):
        with self.store._connect() as db:return dict(db.execute('SELECT * FROM events WHERE event_id=?',('native-ev',)).fetchone())

    def test_auth_is_private_waiting_and_never_borrows_main_manager(self):
        native=SimpleNamespace(run=mock.Mock(side_effect=NativeAuthorizationRequired('ou_A',{'id':'flow'})))
        self.bridge.isolated_handler.native=native
        self.store.add_event(self.event_value);self.bridge._process('native-ev')
        self.assertEqual(self.row()['status'],'waiting_isolated_auth');self.assertEqual(self.row()['execution_state'],'awaiting_auth')
        self.bridge._send_native_authorization.assert_called_once_with('oc_A','ou_A',{'id':'flow'})
        self.assertEqual(self.cherry.calls,[]);self.assertEqual(self.store.retryable(5),[])

    def test_group_auth_link_never_delivered_to_group(self):
        native=SimpleNamespace(run=mock.Mock(side_effect=NativeAuthorizationRequired('ou_A',{'id':'flow','verification_url':'PRIVATE_LINK'})))
        self.bridge.isolated_handler.native=native
        event=dict(self.event_value,chat_id='group',chat_type='group');self.store.add_event(event);self.bridge._process('native-ev')
        self.bridge._send_native_authorization.assert_not_called()
        self.assertNotIn('PRIVATE_LINK',str(self.sender.streams))
        self.assertEqual(self.row()['status'],'waiting_isolated_auth')

    def test_uncertain_native_execution_stays_unknown_without_retry(self):
        self.bridge.isolated_handler.native=SimpleNamespace(run=mock.Mock(side_effect=ExecutionUnknown('fixture write uncertainty')))
        self.store.add_event(self.event_value);self.bridge._process('native-ev')
        self.assertEqual(self.row()['status'],'execution_unknown');self.assertEqual(self.store.retryable(5),[])
        self.assertEqual(self.cherry.calls,[])

    def test_auth_ready_resumes_only_waiting_native_event(self):
        self.store.add_event(self.event_value);self.store.claim_event('native-ev');self.store.wait_isolated_auth('native-ev')
        context=mock.Mock();context.monitor_auth.return_value={'status':'ready','pending':[{'event_id':'native-ev','chat_id':'oc_A'}]}
        self.bridge.isolated_handler.native=SimpleNamespace(contexts={'ou_A':context});self.bridge.enqueue=mock.Mock()
        self.bridge._poll_native_authorizations();self.bridge.enqueue.assert_called_once_with('native-ev','oc_A')
        self.assertEqual(self.row()['status'],'received');self.assertEqual(self.cherry.calls,[])


class NativeContentBoundaryTests(NativeFixture):
    def test_markdown_local_media_cannot_read_owner_or_credentials(self):
        for content in ['![x](/Users/USER/.lark-cli/config.json)','![x](../native/cli/config.json)','![x](file:///etc/passwd)','![x](~/private.pem)']:
            with self.assertRaises(ExecutionRejected):self.cli.execute(['docs','+create','--markdown',content],self.event)
        self.assertEqual(self.calls,[])

    def test_json_nested_markdown_is_checked_and_own_media_remains_available(self):
        data=json.dumps({'body':{'markdown':'![x](../native/home/secret)'}})
        with self.assertRaises(ExecutionRejected):self.cli.execute(['docs','+create','--json',data],self.event)
        (self.cli.work/'own.png').write_bytes(b'own')
        result=self.cli.execute(['docs','+create','--markdown','![x](own.png)'],self.event)
        self.assertTrue(result['needs_confirmation'])

    def test_private_service_urls_and_non_embedded_skills_are_rejected(self):
        for args in [['im','+messages-send','--image','https://127.0.0.1:23333/health'],['docs','+get','--doc','http://localhost:8791/'],['skills','read','../../secret'],['skills','read','/Users/USER/.lark-cli/config.json']]:
            with self.assertRaises(ExecutionRejected):self.cli.execute(args,self.event)
        self.assertEqual(self.calls,[])
