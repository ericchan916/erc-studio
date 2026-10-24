import json,time,uuid
import test_execution_adapter as fixtures
from domain import Problem


class TranscriptTests(fixtures.GatewayExecutionTests):
    def ask_then_reply(self):
        self.client.tool_turn.side_effect=[{'tool_calls':[{'id':'q','function':{'name':'ask_user',
            'arguments':json.dumps({'question':'which?','options':['A','B']})}}]},
            {'content':'answer to first'},{'content':'answer to second'}]
        first=self.send('first question');self.assertEqual(self.wait(first)['state'],'waiting_input')
        return first

    def test_question_preview_and_reply_are_visible_once_in_original_round(self):
        first=self.ask_then_reply()
        later=self.service.unified.submit(self.owner,'app','later','second question',first['conversation_id'])
        body={'text':'A','client_message_id':str(uuid.uuid4())};key=str(uuid.uuid4())
        self.service.respond_job(self.owner,first['job_id'],body,key,'web')
        self.wait(later,('succeeded','failed'))
        self.service.respond_job(self.owner,first['job_id'],body,key,'web')
        items=self.service.messages(self.owner,first['conversation_id'])['items']
        self.assertEqual([r['text'] for r in items],['first question','which?\nA / B','A','answer to first','second question','answer to second'])
        self.assertEqual([r.get('entry_kind') for r in items],[None,'notice','control',None,None,None])
        self.assertEqual(len({r['seq'] for r in items}),6)
        self.assertEqual(self.service.one('SELECT count(*) FROM jobs')[0],2)
        self.assertEqual(self.service.one('SELECT count(*) FROM conversation_controls')[0],1)
        history=json.dumps(self.client.tool_turn.call_args.args[0],ensure_ascii=False)
        for text in ('first question','which?','A','answer to first','second question'):self.assertIn(text,history)

    def test_control_transcript_is_owner_scoped_and_paginated_without_duplicates(self):
        first=self.ask_then_reply()
        self.service.respond_job(self.owner,first['job_id'],{'text':'B','client_message_id':str(uuid.uuid4())},str(uuid.uuid4()),'app')
        self.wait(first,('succeeded','failed'))
        result=[];page=self.service.messages(self.owner,first['conversation_id'],limit=2,offset=-1)
        while True:
            result=page['items']+result
            if not page['older_cursor']:break
            page=self.service.messages(self.owner,first['conversation_id'],limit=2,offset=-1,before=int(page['older_cursor']))
        self.assertEqual([r['text'] for r in result],['first question','which?\nA / B','B','answer to first'])
        with self.assertRaises(Problem):self.service.messages(self.other,first['conversation_id'])

    def test_notice_recovery_is_idempotent_and_does_not_copy_engine_journal(self):
        first=self.ask_then_reply();job=self.service.check_job(self.owner,first['job_id'])
        row=self.service.one('SELECT attempt FROM conversation_execution WHERE job=?',(job['id'],))
        self.service.unified.transcript.record(job,'notice',row['attempt'],'assistant','which?\nA / B','web','notice:'+row['attempt'])
        self.assertEqual(self.service.one('SELECT count(*) FROM conversation_entries')[0],1)
        text=json.dumps(self.service.messages(self.owner,first['conversation_id']))
        self.assertNotIn('NATIVE_PROMPT',text);self.assertNotIn('tool_calls',text);self.assertNotIn('fixture-pepper',text)
