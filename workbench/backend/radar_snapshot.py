# -*- coding: utf-8 -*-
"""Deduplicated live reads of the blogger radar table for the authenticated gateway."""
from __future__ import annotations
import os
import copy, json, os, re, subprocess, threading, time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from pathlib import Path

LARK = 'lark-cli'
PROFILE = os.environ.get('CONTENTBASE_LARK_PROFILE','')
BASE = os.environ.get('CONTENTBASE_RADAR_BASE','')
TABLE = 'tblexampledfe6518fdb7a'
FIELDS = ['博主', '标题', '发布时间', '原文链接', '选题套路', '可模仿选题建议', '状态', '检测时间',
          '记录标识', '选题类型', '下一步', '原文文档', '仿写稿']
TABLE_URL = 'https://TENANT.feishu.cn/base/%s?table=%s' % (BASE, TABLE)


def read_time():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


class RadarRefreshError(RuntimeError):
    pass


class RadarRefresher:
    def __init__(self, config, run=subprocess.run):
        configured = str(config.get('workspace_state_dir', '') or '')
        if not configured or not Path(configured).expanduser().is_absolute():
            # 拒绝隐式落点：空/相对路径会把缓存写进进程 cwd（曾把 radar-cache.json 写进仓库根）
            raise RadarRefreshError('radar_state_dir_required')
        self.root = Path(configured).expanduser().resolve()
        self.cache = self.root / 'radar-cache.json'
        self.run = run
        self.condition = threading.Condition()
        self.running = False
        self.last = None
        self.error = None
        self.readers={m.get('principal_id') for m in config.get('members',[])
                      if m.get('handle')=='owner' and m.get('active',True)}

    def can_read(self, principal):
        return isinstance(principal,str) and bool(principal) and principal in self.readers

    # ---- 协议校验：坏数据不覆盖旧缓存 ----

    def _validate(self, value):
        if not isinstance(value, dict) or value.get('version') != 1 \
                or not isinstance(value.get('items'), list) or len(value['items']) > 500:
            raise RadarRefreshError('radar_refresh_protocol_invalid')
        keys=set()
        for item in value['items']:
            if not isinstance(item, dict) or not isinstance(item.get('key'), str) or not item['key']:
                raise RadarRefreshError('radar_refresh_protocol_invalid')
            if item['key'] in keys or any(name in item and not isinstance(item[name],str)
                for name in ('blogger','title','published','link','pattern','topics','status','detected','topic_type','next','source_doc','rewrite_doc','url')):
                raise RadarRefreshError('radar_refresh_protocol_invalid')
            keys.add(item['key'])
        return value

    def _execute(self):
        env = {k: os.environ[k] for k in ('HOME', 'USER', 'TMPDIR') if k in os.environ}
        env.update(PATH='/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin',
                   LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1', LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
        args = [LARK, '--profile', PROFILE, 'base', '+record-list',
                '--base-token', BASE, '--table-id', TABLE, '--limit', '200',
                '--sort-json', '[{"field":"检测时间","desc":true}]', '--as', 'user', '--format', 'json']
        for name in FIELDS:
            args += ['--field-id', name]
        try:
            result = self.run(args, text=True, capture_output=True, timeout=45,
                              stdin=subprocess.DEVNULL, env=env)
        except (OSError, subprocess.TimeoutExpired):
            raise RadarRefreshError('radar_refresh_unavailable') from None
        if result.returncode:
            raise RadarRefreshError('radar_refresh_failed')
        try:
            packet = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise RadarRefreshError('radar_refresh_protocol_invalid') from None
        if not isinstance(packet, dict) or packet.get('ok') is not True or packet.get('identity') != 'user':
            raise RadarRefreshError('radar_refresh_failed')
        data = packet.get('data') or {}
        if not isinstance(data,dict):
            raise RadarRefreshError('radar_refresh_protocol_invalid')
        ids = data.get('record_id_list') or []
        rows = data.get('data') or []
        names = data.get('fields') or []
        if not all(isinstance(v,list) for v in (ids,rows,names)) \
                or not all(isinstance(name,str) for name in names) \
                or len(names)!=len(FIELDS) or set(names)!=set(FIELDS) \
                or any(not isinstance(rid,str) or not re.fullmatch(r'rec[A-Za-z0-9]+',rid) for rid in ids) \
                or len(rows) != len(ids) or any(not isinstance(row,list) or len(row) != len(names) for row in rows):
            raise RadarRefreshError('radar_refresh_protocol_invalid')
        items = []
        for record_id, row in zip(ids, rows):
            raw = dict(zip(names, row))
            def pick(name):
                value = raw.get(name)
                return value[0] if isinstance(value, list) and value else (value if isinstance(value, str) else '')
            items.append({'record_id': record_id, 'key': pick('记录标识') or record_id,
                          'blogger': pick('博主'), 'title': pick('标题'), 'published': pick('发布时间'),
                          'link': pick('原文链接'), 'pattern': pick('选题套路'), 'topics': pick('可模仿选题建议'),
                          'status': pick('状态') or '待确认', 'detected': pick('检测时间'),
                          'topic_type': pick('选题类型'), 'next': pick('下一步') or '待定',
                          'source_doc': pick('原文文档'), 'rewrite_doc': pick('仿写稿'),
                          'url': TABLE_URL + '&record=' + record_id})
        items.sort(key=lambda x: x['detected'], reverse=True)
        return self._validate({'version': 1, 'readAt': read_time(),
                               'items': items, 'has_more': bool(data.get('has_more')), 'table_url': TABLE_URL})

    def refresh(self):
        with self.condition:
            if self.running:
                if not self.condition.wait_for(lambda: not self.running, timeout=50):
                    raise RadarRefreshError('radar_refresh_unavailable')
                if self.error:
                    raise RadarRefreshError(self.error)
                return copy.deepcopy(self.last)
            self.running = True
            self.error = None
        try:
            value = self._execute()
            persisted=True
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                temporary = self.cache.with_suffix('.tmp')
                temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.cache)
            except OSError:
                persisted=False
            value['cache_persisted']=persisted
            with self.condition:
                self.last=value
            return copy.deepcopy(value)
        except Exception as error:
            code=str(error) if isinstance(error,RadarRefreshError) else 'radar_refresh_protocol_invalid'
            if not isinstance(error,RadarRefreshError):
                import logging
                logging.getLogger(__name__).warning('radar_refresh_exception:%s',type(error).__name__)
            with self.condition:
                self.error=code
            raise RadarRefreshError(code) from None
        finally:
            with self.condition:
                self.running=False
                self.condition.notify_all()

    def cached(self):
        try:
            with self.condition:
                current=copy.deepcopy(self.last)
            value = self._validate(current if current is not None else json.loads(self.cache.read_text(encoding='utf-8')))
            stamp = datetime.fromisoformat(value.get('readAt', ''))
            if stamp.tzinfo is None:
                raise ValueError('timestamp_not_zoned')
            age = (datetime.now(timezone.utc) - stamp).total_seconds()
            value['stale'] = age < -60 or age > 1800
        except (OSError, ValueError, TypeError, RadarRefreshError):
            return {'version': 1, 'readAt': '', 'items': [], 'stale': True, 'table_url': TABLE_URL}
        return value

    def article(self, key, kind):
        """Read only a document linked by this radar snapshot; never an arbitrary URL."""
        if not isinstance(key, str) or not 1 <= len(key) <= 200 or kind not in ('source', 'rewrite'):
            raise RadarRefreshError('radar_draft_invalid')
        item = next((i for i in self.cached()['items'] if i['key'] == key), None)
        if not item:
            raise RadarRefreshError('radar_record_missing')
        url = item.get('rewrite_doc' if kind == 'rewrite' else 'source_doc') or ''
        if not url:
            raise RadarRefreshError('radar_draft_missing')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'TENANT.feishu.cn' \
                or parsed.query or parsed.fragment or not re.fullmatch(r'/docx/[A-Za-z0-9]{20,40}', parsed.path):
            raise RadarRefreshError('radar_draft_invalid')
        args = [LARK, '--profile', PROFILE, 'docs', '+fetch', '--doc', url,
                '--doc-format', 'markdown', '--as', 'user', '--format', 'json']
        try:
            result = self.run(args, text=True, capture_output=True, timeout=60,
                              stdin=subprocess.DEVNULL)
            packet = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError):
            raise RadarRefreshError('radar_draft_unavailable') from None
        if result.returncode or not isinstance(packet, dict) or packet.get('ok') is not True \
                or packet.get('identity') != 'user':
            raise RadarRefreshError('radar_draft_unavailable')
        data = packet.get('data') or {}
        document = data.get('document') if isinstance(data, dict) else None
        text = document.get('content') if isinstance(document, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > 200000:
            raise RadarRefreshError('radar_draft_invalid')
        # CLI Markdown includes a leading XML title envelope; not article body.
        text = re.sub(r'\A<title>[^\n]*</title>\s*\n', '', text, count=1)
        return {'key': key, 'kind': kind, 'markdown': text, 'readAt': read_time(), 'document_url': url}
