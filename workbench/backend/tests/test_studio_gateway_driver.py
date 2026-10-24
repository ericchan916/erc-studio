import json
import threading
import uuid
from pathlib import Path
from unittest import mock
from test_execution_adapter import GatewayExecutionTests
from bridge_studio_gateway import StudioGateway


class DriverTests(GatewayExecutionTests):
    def setUp(self):
        super().setUp()
        port=self.peer.server_port;self.peer.shutdown();self.peer.server_close()
        self.bridge.stop_event=threading.Event();self.bridge.config['feishu_profile']='fixture'
        cfg={**self.config,'host':'127.0.0.1','port':self.gateway.server_port}
        self.gate_file=Path(self.tmp.name)/'gateway.json';self.gate_file.write_text(json.dumps(cfg));self.gate_file.chmod(0o600)
        self.driver_config={'enabled':True,'gateway_config':str(self.gate_file),'key_file':str(self.key_file),'port':port,'wait_seconds':30}
        self.driver=StudioGateway(self.bridge,self.driver_config)
        self.driver.start();self.peer=self.driver.server
        self.service.execution.origin='http://127.0.0.1:'+str(port)

    def private_event(self,text='hello',message='om_driver'):
        return {'event_id':'event:'+message,'message_id':message,'chat_id':'oc_A','sender_id':'ou_A',
                'chat_type':'p2p','message_type':'text','content':text}

    def test_driver_forwards_verified_private_message_to_same_gateway_job(self):
        event=self.private_event();self.bridge.store.add_event(event)
        answer=self.driver.forward(event,'hello')
        self.assertEqual(answer,'shared native answer')
        self.assertEqual(self.client.tool_turn.call_count,1)
        self.assertEqual(self.driver.forward(event,'hello'),answer)
        self.assertEqual(self.client.tool_turn.call_count,1)

    def test_admission_before_delivery_freezes_conversation_across_later_switch(self):
        first=self.send('original');self.wait(first)
        event=self.private_event('queued while delivery is busy','om_queued');self.bridge.store.add_event(event)
        admitted=self.driver.admit(event,event['content'])
        other=self.service.create_conversation(self.owner,{'title':'later view'},str(uuid.uuid4()))['conversation_id']
        self.service.unified.bind(self.owner,other)
        self.assertEqual(self.driver.forward(event,event['content']),'shared native answer')
        self.assertEqual(admitted['conversation_id'],first['conversation_id'])
        self.assertEqual(self.service.check_job(self.owner,admitted['job_id'])['conversation'],first['conversation_id'])
        self.assertEqual(self.service.unified.active(self.owner)['conversation_id'],other)
        self.assertEqual(self.service.one('SELECT count(*) FROM jobs')[0],2)
        self.assertEqual(self.client.tool_turn.call_count,2)

    def test_reopened_driver_reuses_durable_receipt_without_second_admission(self):
        event=self.private_event('first','om_receipt');self.bridge.store.add_event(event)
        original=self.driver.admit(event,event['content']);self.wait(original)
        another=self.service.create_conversation(self.owner,{'title':'another'},str(uuid.uuid4()))['conversation_id'];self.service.unified.bind(self.owner,another)
        checker=StudioGateway(self.bridge,self.driver_config);checker.execution=self.driver.execution
        checker.call=mock.Mock(side_effect=AssertionError('receipt replay should not call admission again'))
        self.assertEqual(checker.admit(event,event['content']),original)
        self.assertEqual(self.service.unified.active(self.owner)['conversation_id'],another)
        self.assertEqual(self.client.tool_turn.call_count,1)

    def test_typing_receipt_survives_driver_reload_and_clears_only_matching_message(self):
        event=self.private_event('first','om_reaction');self.bridge.store.add_event(event);self.driver.admit(event,'first')
        self.driver.save_reaction(event,'reaction-original')
        checker=StudioGateway(self.bridge,self.driver_config);checker.execution=self.driver.execution
        self.assertEqual(checker.reaction(event),'reaction-original')
        checker.clear_reaction('om_other','reaction-original');self.assertEqual(checker.reaction(event),'reaction-original')
        checker.clear_reaction(event['message_id'],'reaction-original');self.assertIsNone(checker.reaction(event))

    def test_cutover_keeps_original_predeployment_auth_and_tasks_on_legacy_path(self):
        import datetime
        self.driver.cutover=datetime.datetime(2026,10,5,tzinfo=datetime.timezone.utc)
        old=self.private_event('original waiting auth','om_old_auth');self.bridge.store.add_event(old)
        with self.bridge.store._connect() as db:db.execute("UPDATE events SET created_at='2026-10-04T00:00:00Z' WHERE event_id=?",(old['event_id'],))
        self.assertFalse(self.driver.eligible(old))
        new=self.private_event('new after activation','om_after');self.bridge.store.add_event(new)
        with self.bridge.store._connect() as db:db.execute("UPDATE events SET created_at='2026-10-06T00:00:00Z' WHERE event_id=?",(new['event_id'],))
        self.assertTrue(self.driver.eligible(new));receipt=self.driver.admit(new,new['content'])
        # Already admitted messages retain their original receipt, even on
        # clock/config changes. An old task is never reinterpreted as new work.
        self.driver.cutover=datetime.datetime(2030,1,1,tzinfo=datetime.timezone.utc)
        self.assertTrue(self.driver.eligible(new));self.assertEqual(self.driver.admit(new,new['content']),receipt)

    def test_legacy_native_final_receipt_retries_signed_delivery_without_new_execution(self):
        import datetime
        from conversation_protocol import ProtocolError
        self.driver.cutover=datetime.datetime(2026,10,5,tzinfo=datetime.timezone.utc)
        old=self.private_event('old request','om_legacy');self.bridge.store.add_event(old)
        with self.bridge.store._connect() as db:db.execute("UPDATE events SET created_at='2026-10-04T00:00:00Z' WHERE event_id=?",(old['event_id'],))
        journal=self.native.journals['ou_A'];task=journal.load(old,'old request',[])
        history=self.service.unified.importer.apply(self.owner,'legacy',[{'source_id':old['event_id'],'chat_id':'oc_A','sender_id':'ou_A','text':'old request','answer':'old confirmation notice','state':'legacy_pending','created_at':'2026-10-04T00:00:00Z'}])
        current=self.service.create_conversation(self.owner,{'title':'current'},str(uuid.uuid4()))['conversation_id'];self.service.unified.bind(self.owner,current)
        task['state']='done';task['answer']='original legacy result';journal.save(task)
        self.bridge.store.save_inference(old['event_id'],'','original legacy result','',True);self.bridge.store.mark_done(old['event_id'])
        self.driver.capture_legacy(old)
        original=self.driver.call;attempts=[]
        def call(action,body,timeout=25):
            if action=='legacy-receipt' and not attempts:attempts.append(True);raise ProtocolError('transport_uncertain',uncertain=True)
            return original(action,body,timeout)
        self.driver.call=call
        with self.assertRaises(ProtocolError):self.driver.poll_legacy_receipts()
        self.driver.poll_legacy_receipts();self.driver.poll_legacy_receipts()
        self.assertEqual([r['text'] for r in self.service.messages(self.owner,history['conversation_id'])['items']],['old request','old confirmation notice','original legacy result'])
        self.assertEqual(self.service.unified.active(self.owner)['conversation_id'],current)
        self.assertEqual(self.service.one('SELECT count(*) FROM jobs')[0],0);self.client.tool_turn.assert_not_called()
        self.assertFalse(self.service.unified.importer.pending(self.owner,history['conversation_id']))

    def test_unregistered_engine_rejects_before_admission_without_legacy_fallback(self):
        from bridge_cherry import ExecutionRejected
        event=self.private_event('first','om_exampleea2d6b1c86db');self.bridge.store.add_event(event)
        self.driver.execution.member=mock.Mock(side_effect=__import__('conversation_protocol').ProtocolError('studio_member_engine_not_registered',403))
        self.assertTrue(self.driver.eligible(event))
        with self.assertRaises(ExecutionRejected):self.driver.admit(event,'first')
        self.assertEqual(self.service.one('SELECT count(*) FROM jobs')[0],0);self.client.tool_turn.assert_not_called()

    def test_group_other_sender_and_old_unscoped_cards_remain_legacy(self):
        event=self.private_event()
        self.assertTrue(self.driver.eligible(event))
        for changed in ({'chat_type':'group'},{'sender_id':'ou_OTHER'},{'chat_id':'oc_OTHER'},
                        {'origin':'local_pipeline_guard'},{'native_card_id':'old-card'}):
            self.assertFalse(self.driver.eligible({**event,**changed}))

    def test_new_command_keeps_old_history_and_does_not_reset_native_checkpoints(self):
        first=self.send();self.wait(first);old=first['conversation_id']
        before=self.service.one('SELECT count(*) FROM messages')[0]
        event=self.private_event('/new','om_new');self.bridge.store.add_event(event)
        answer=self.driver.forward(event,'/new');self.assertIn('旧历史',answer)
        active=self.service.unified.active(self.owner)['conversation_id'];self.assertNotEqual(active,old)
        self.assertEqual(self.service.one('SELECT count(*) FROM messages')[0],before)
        self.assertEqual(self.driver.forward(event,'/new'),answer);self.assertEqual(self.service.unified.active(self.owner)['conversation_id'],active)
        with self.cli.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM native_tasks').fetchone()[0],1)

    def test_constructor_for_preflight_does_not_recover_or_mutate_live_ledger(self):
        first=self.send();self.wait(first)
        with self.bridge.store._connect() as db:
            db.execute("UPDATE studio_execution SET state='running',result_json=NULL WHERE job=?",(first['job_id'],))
        checker=StudioGateway(self.bridge,self.driver_config)
        self.assertIsNone(checker.server);self.assertIsNone(checker.execution)
        with self.bridge.store._connect() as db:self.assertEqual(db.execute('SELECT state FROM studio_execution WHERE job=?',(first['job_id'],)).fetchone()[0],'running')

    def test_listener_port_collision_does_not_mark_an_existing_execution_unknown(self):
        first=self.send();self.wait(first)
        with self.bridge.store._connect() as db:
            db.execute("UPDATE studio_execution SET state='running',result_json=NULL WHERE job=?",(first['job_id'],))
        checker=StudioGateway(self.bridge,self.driver_config)
        with self.assertRaises(OSError):checker.start()
        with self.bridge.store._connect() as db:
            self.assertEqual(db.execute('SELECT state FROM studio_execution WHERE job=?',(first['job_id'],)).fetchone()[0],'running')
        self.assertIsNone(checker.server);self.assertIsNone(checker.execution)

    def test_bridge_retry_poll_delivers_durable_trusted_authorization_state(self):
        self.driver.execution.poll_authorizations=mock.Mock()
        self.driver.poll_authorizations()
        self.driver.execution.poll_authorizations.assert_called_once_with()

    def test_driver_signed_member_command_cannot_select_another_actor(self):
        event=self.private_event('/whoami','om_identity');self.bridge.store.add_event(event)
        answer=self.driver.forward(event,'/whoami');self.assertIn('A',answer)
        self.assertEqual(self.client.tool_turn.call_count,0)
        self.assertEqual(self.model.calls,[])

    def test_real_private_reply_after_question_resumes_original_job(self):
        self.client.tool_turn.side_effect=[{'tool_calls':[{'id':'q','function':{'name':'ask_user',
            'arguments':json.dumps({'question':'which?','options':['A','B']})}}]},{'content':'based on reply'}]
        first=self.private_event('first','om_first');self.bridge.store.add_event(first)
        self.assertEqual(self.driver.forward(first,'first'),'which?\nA / B')
        original=self.service.one('SELECT id FROM jobs')[0]
        reply=self.private_event('A','om_reply');self.bridge.store.add_event(reply)
        self.assertEqual(self.driver.forward(reply,'A'),'based on reply')
        self.assertEqual(self.service.one('SELECT count(*) FROM jobs')[0],1)
        self.assertEqual(self.service.job(self.owner,original)['state'],'succeeded')

    def test_replayed_question_while_waiting_does_not_become_its_own_reply(self):
        self.client.tool_turn.side_effect=[{'tool_calls':[{'id':'q','function':{'name':'ask_user',
            'arguments':json.dumps({'question':'which?','options':['A','B']})}}]},{'content':'unexpected duplicate resume'}]
        first=self.private_event('first','om_first');self.bridge.store.add_event(first)
        self.assertEqual(self.driver.forward(first,'first'),'which?\nA / B')
        jid=self.service.one('SELECT id FROM jobs')[0]
        self.assertEqual(self.driver.forward(first,'first'),'which?\nA / B')
        self.assertEqual(self.service.job(self.owner,jid)['state'],'waiting_input')
        self.assertEqual(self.client.tool_turn.call_count,1)

    def test_replayed_control_after_switch_returns_original_job_without_new_question(self):
        self.client.tool_turn.side_effect=[{'tool_calls':[{'id':'q','function':{'name':'ask_user',
            'arguments':json.dumps({'question':'which?','options':['A','B']})}}]},{'content':'based on reply'}]
        first=self.private_event('first','om_first');self.bridge.store.add_event(first)
        self.driver.forward(first,'first')
        reply=self.private_event('A','om_reply');self.bridge.store.add_event(reply)
        self.assertEqual(self.driver.forward(reply,'A'),'based on reply')
        new=self.private_event('/new','om_new');self.bridge.store.add_event(new)
        self.driver.forward(new,'/new')
        active=self.service.unified.active(self.owner)
        self.assertEqual(self.driver.forward(reply,'A'),'based on reply')
        self.assertEqual(self.service.unified.active(self.owner),active)
        self.assertEqual(self.client.tool_turn.call_count,2)
        self.assertEqual(self.service.one('SELECT count(*) FROM jobs')[0],1)

    def test_original_receipt_recovery_does_not_call_native_engine_again(self):
        first=self.send();self.wait(first);before=self.client.tool_turn.call_count
        with self.service.transaction():
            answer=self.service.one('SELECT answer FROM conversation_turns WHERE job=?',(first['job_id'],))[0]
            self.service.db.execute('UPDATE conversation_turns SET answer=NULL WHERE job=?',(first['job_id'],))
            self.service.db.execute('DELETE FROM messages WHERE id=?',(answer,))
            self.service.db.execute("UPDATE jobs SET state='execution_unknown' WHERE id=?",(first['job_id'],))
        recovered=self.service.recover_job(self.owner,first['job_id'],str(uuid.uuid4()))
        self.assertTrue(recovered['recovered']);self.assertEqual(recovered['state'],'succeeded')
        self.assertEqual(self.client.tool_turn.call_count,before)
        self.assertEqual(len(self.service.messages(self.owner,first['conversation_id'])['items']),2)

    def test_native_auth_ready_signal_resumes_studio_job_instead_of_legacy_event(self):
        self.risk_level='read'
        from bridge_native_feishu import NativeAuthorizationRequired
        self.client.tool_turn.side_effect=[{'tool_calls':[{'id':'read','function':{'name':'feishu_cli',
            'arguments':json.dumps({'args':['docs','+get','--doc','OWN_RESOURCE']})}}]},{'content':'after auth'}]
        original=self.cli.execute;blocked=[]
        def execute(args,event):
            if not blocked:blocked.append(True);raise NativeAuthorizationRequired('ou_A',{'id':'flow-fixture'})
            return original(args,event)
        self.cli.execute=execute
        first=self.send();self.wait(first)
        pending={'event_id':'studio-job:'+first['job_id'],'chat_id':'oc_A'}
        self.assertTrue(self.driver.resume_native_auth('ou_A',pending,{'status':'ready'}))
        self.assertEqual(self.wait(first,('succeeded','failed'))['state'],'succeeded')
        with self.cli.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM native_tasks').fetchone()[0],1)


if __name__=='__main__':
    import unittest
    unittest.main()

    def test_cancel_targets_original_waiting_round_not_later_queued_question(self):
        from studio_internal import dispatch
        first=self.send();self.wait(first)
        second=self.send('later queued question');self.wait(second)
        with self.service.transaction():
            self.service.db.execute("UPDATE jobs SET state='waiting_input' WHERE id=?",(first['job_id'],))
            self.service.db.execute("UPDATE jobs SET state='queued' WHERE id=?",(second['job_id'],))
            with mock.patch.object(self.service,'cancel') as cancel:
                dispatch(self.service,'/internal/studio/command',{'open_id':'ou_A','tenant_key':self.config['members'][0]['tenant_key'],'chat_id':'oc_A','source_id':'cancel-round','text':'/cancel'})
                self.assertEqual(cancel.call_args.args[1],first['job_id'])
