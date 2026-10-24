"""Fixed signed Bridge ingress/lease/control surface; never browser bearer auth."""
import hashlib,json
from domain import Problem, shape,stamp


def member_for(service, body):
    matches = [m for m in service.members.values()
               if (m['open_id'], m['tenant_key'], m.get('chat_id')) ==
                  (body['open_id'], body['tenant_key'], body['chat_id']) and m.get('active', True)]
    if len(matches) != 1:
        raise Problem(403, 'studio_member_not_allowed')
    member = matches[0]
    service.notifier.ensure_active(member)
    return member


def dispatch(service, path, body):
    if not service.unified_enabled or not service.execution:
        raise Problem(409, 'shared_context_not_enabled')
    if path == '/internal/studio/lease':
        return service.execution.lease(body)
    if path=='/internal/studio/legacy-receipt':
        fields=['open_id','tenant_key','chat_id','source_id','parent_source_id','text','answer','created_at','settled']
        shape(body,fields,fields);member=member_for(service,body)
        return service.unified.importer.receipt(member['principal_id'],{k:body[k] for k in fields[3:]})
    if path=='/internal/studio/command':
        fields=['open_id','tenant_key','chat_id','source_id','text'];shape(body,fields,fields)
        member=member_for(service,body);owner=member['principal_id'];command=body['text'].strip().lower()
        if command not in ('/new','/whoami','/cancel','取消当前任务','停止当前任务'):raise Problem(400,'invalid_request')
        key='command_'+hashlib.sha256(body['source_id'].encode()).hexdigest()
        with service.transaction():
            fingerprint,old=service.existing(owner,'studio-command',key,{'source_id':body['source_id'],'text':body['text']})
            if old:return old
            active=service.unified.active(owner)
            if command=='/new':
                conversation=service.create_conversation(owner,{'title':'飞书新对话'},key)
                service.unified.bind(owner,conversation['conversation_id']);answer='已新建本人对话；旧历史和原任务保留。'
            elif command=='/whoami':answer='当前为'+member['name']+'的本人工作台对话；飞书、网页和客户端复用同一身份。'
            else:
                cid=active['conversation_id'];job=service.one("SELECT id FROM jobs WHERE owner=? AND conversation=? AND state IN ('queued','running','waiting_input','waiting_confirmation','waiting_authorization','paused') ORDER BY CASE WHEN state='queued' THEN 1 ELSE 0 END,rowid ASC LIMIT 1",(owner,cid)) if cid else None
                if job:
                    service.cancel(owner,job['id'],key)
                    answer='已请求停止本人的原任务；已发生的操作保留回执，不自动撤销。'
                else:answer='当前本人对话没有可停止的在途任务；历史保留。'
            result={'text':answer};service.remember(owner,'studio-command',key,fingerprint,result);return result
    if path == '/internal/studio/ingest':
        fields = ['open_id', 'tenant_key', 'chat_id', 'source_id', 'source_event_id', 'text']
        shape(body, fields, fields)
        member = member_for(service, body)
        with service.transaction():
            # Resolve source admission BEFORE looking at the current binding or
            # waiting task. Re-delivered questions are not clarification replies;
            # old replies after a switch still belong to their original job.
            owner=member['principal_id']
            ingress=service.one('SELECT job FROM conversation_ingress WHERE owner=? AND channel=? AND source_id=?',
                (owner,'feishu',body['source_id']))
            if ingress:
                accepted=service.unified.submit(owner,'feishu',body['source_id'],body['text'])
                service.execution.attach_source(owner,'feishu',body['source_id'],body['source_event_id'])
                return accepted
            control=service.one('SELECT job FROM conversation_controls WHERE owner=? AND channel=? AND source_id=?',
                (owner,'feishu',body['source_id']))
            if control:
                return service.execution.control(owner,control['job'],'feishu',body['source_id'],body['text'],body['source_event_id'])
            active=service.unified.active(member['principal_id'])['conversation_id']
            pending=service.one("SELECT * FROM jobs WHERE owner=? AND conversation=? AND state IN ('waiting_input','waiting_confirmation','paused') ORDER BY rowid ASC LIMIT 1",(member['principal_id'],active)) if active else None
            if pending and (pending['state'] in ('waiting_input','waiting_confirmation') or body['text'].strip() in ('继续','继续执行','继续当前事项')):
                return service.execution.control(member['principal_id'],pending['id'],'feishu',body['source_id'],body['text'],body['source_event_id'])
            accepted = service.unified.submit(member['principal_id'], 'feishu', body['source_id'], body['text'])
            service.execution.attach_source(member['principal_id'], 'feishu', body['source_id'], body['source_event_id'])
            return accepted
    if path in ('/internal/studio/status', '/internal/studio/control', '/internal/studio/resume-auth'):
        fields = ['open_id', 'tenant_key', 'chat_id', 'job_id']
        extras = ['source_id', 'source_event_id', 'text'] if path.endswith('/control') else []
        optional=['status'] if path.endswith('/resume-auth') else []
        shape(body, fields+extras+optional, fields+extras)
        member = member_for(service, body)
        owner = member['principal_id']
        job = service.check_job(owner, body['job_id'])
        if path.endswith('/status'):
            return service.job(owner, job['id'])
        if path.endswith('/control'):
            return service.execution.control(owner, job['id'], 'feishu', body['source_id'], body['text'], body['source_event_id'])
        if body.get('status','ready') not in ('ready','failed'):raise Problem(400,'invalid_request')
        if body.get('status')=='failed':
            if job['state']!='waiting_authorization':raise Problem(409,'studio_control_not_waiting')
            with service.transaction():
                service.db.execute("UPDATE jobs SET state='failed',error='native_authorization_failed',updated=? WHERE id=?",(stamp(),job['id']))
                service.event(job['id'],'status',{'state':'failed'})
            return service.unified.receipt(service.check_job(owner,job['id']))
        row = service.one('SELECT request_json,attempt FROM conversation_execution WHERE job=?', (job['id'],))
        if not row:
            raise Problem(404, 'not_found')
        original = json.loads(row['request_json'])
        text = original.get('control', {}).get('text') or original['history'][-1]['content']
        return service.execution.control(owner, job['id'], 'feishu', 'auth:'+job['id']+':'+row['attempt'], text, kind='authorization')
    raise Problem(404, 'not_found')
