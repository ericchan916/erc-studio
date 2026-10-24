import json, sys, unittest, tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar_snapshot import RadarRefresher, RadarRefreshError

from radar_snapshot import RadarRefresher, RadarRefreshError


def cli_result(records, has_more=False, ok=True):
    packet = {'ok': ok, 'identity': 'user' if ok else 'bot',
              'data': {'record_id_list': [r[0] for r in records],
                       'fields': ['博主', '标题', '发布时间', '原文链接', '选题套路', '可模仿选题建议',
                                  '状态', '检测时间', '记录标识', '选题类型', '下一步', '原文文档', '仿写稿'],
                       'data': [r[1] for r in records], 'has_more': has_more}}
    return SimpleNamespace(returncode=0 if ok else 1, stdout=json.dumps(packet, ensure_ascii=False), stderr='')


def record(rid, title, topic_type='热点快讯', nxt='待定', rewrite=''):
    row = ['新智元', title, '2026-10-09T10:00:00Z', 'https://mp.weixin.qq.com/s/a', 'p', 't',
           '待确认', '2026-10-09T20:00:00+08:00', 'wechat_db:k' + rid, [topic_type], [nxt], '', rewrite]
    return (rid, row)


class RadarSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {'workspace_state_dir': self.temp.name}

    def test_missing_or_relative_state_dir_refused(self):
        for bad in ('', None, 'relative/path'):
            with self.assertRaises(RadarRefreshError):
                RadarRefresher({'workspace_state_dir': bad})

    def refresher(self, result):
        return RadarRefresher(self.config, run=lambda *a, **k: result)

    def test_refresh_decodes_selects_and_sorts(self):
        rows = [record('recA', '旧文', detected_old := None or 'x'), record('recB', '新文')]
        rows[0][1][7] = '2026-10-08T10:00:00+08:00'
        value = self.refresher(cli_result(rows)).refresh()
        self.assertEqual(value['items'][0]['key'], 'wechat_db:krecB')
        self.assertEqual(value['items'][0]['topic_type'], '热点快讯')
        self.assertEqual(value['items'][0]['next'], '待定')
        self.assertEqual(value['items'][1]['title'], '旧文')

    def test_bad_shape_rejected(self):
        rows = [record('recA', 'x')]
        rows[0][1].append('多一个值')  # 行与字段数不匹配
        with self.assertRaises(RadarRefreshError):
            self.refresher(cli_result(rows)).refresh()

    def test_cli_failure_raises_not_silently(self):
        with self.assertRaises(RadarRefreshError):
            self.refresher(cli_result([], ok=False)).refresh()

    def test_malformed_packets_and_missing_fields_do_not_clear_cache(self):
        for packet in ([], {'ok':True,'identity':'user','data':[1]},
                       {'ok':True,'identity':'user','data':{'fields':[], 'data':[], 'record_id_list':[]}}):
            ref=self.refresher(SimpleNamespace(returncode=0,stdout=json.dumps(packet)))
            ref.cache.write_text('existing')
            with self.assertRaises(RadarRefreshError):ref.refresh()
            self.assertEqual(ref.cache.read_text(),'existing')

    def test_duplicate_keys_or_wrong_text_types_are_rejected(self):
        ref=self.refresher(cli_result([]))
        for items in ([{'key':'a'},{'key':'a'}],[{'key':'a','rewrite_doc':{'url':'bad'}}]):
            with self.assertRaises(RadarRefreshError):ref._validate({'version':1,'items':items})

    def test_invalid_record_id_does_not_wedge_and_next_refresh_recovers(self):
        row=record('recA','坏ID')
        ref=self.refresher(cli_result([(123,row[1])]))
        with self.assertRaises(RadarRefreshError):ref.refresh()
        self.assertFalse(ref.running)
        ref.run=lambda *a,**kw:cli_result([record('recA','正常')])
        self.assertEqual(ref.refresh()['items'][0]['record_id'],'recA')
        self.assertFalse(ref.running)

    def test_unexpected_exception_releases_all_waiters_and_recovers(self):
        import threading,time
        gate=threading.Event();entered=threading.Event();calls=[];errors=[]
        def broken():
            calls.append(1);entered.set();gate.wait(2);raise TypeError('unexpected')
        ref=self.refresher(cli_result([]));ref._execute=broken
        def worker():
            try:ref.refresh()
            except RadarRefreshError as error:errors.append(str(error))
        first=threading.Thread(target=worker);second=threading.Thread(target=worker)
        first.start();entered.wait(1);second.start();time.sleep(.05);gate.set();first.join(2);second.join(2)
        self.assertFalse(first.is_alive());self.assertFalse(second.is_alive());self.assertFalse(ref.running)
        self.assertEqual(len(calls),1);self.assertEqual(errors,['radar_refresh_protocol_invalid']*2)
        del ref._execute
        self.assertEqual(ref.refresh()['items'],[])

    def test_concurrent_refresh_dedupes(self):
        import threading
        calls = []
        gate = threading.Event()

        def run_block(*a, **k):
            calls.append(1)
            gate.wait(timeout=5)
            return cli_result([record('recA', 'x')])

        refresher = RadarRefresher(self.config)
        refresher.run = run_block
        results = {}

        def worker(name):
            try:
                results[name] = refresher.refresh()
            except Exception as error:  # noqa: BLE001
                results[name] = error

        first = threading.Thread(target=worker, args=('a',))
        first.start()
        import time as _time
        _time.sleep(0.05)  # 让第一个线程进入 running
        second = threading.Thread(target=worker, args=('b',))
        second.start()
        _time.sleep(0.05)
        gate.set()
        first.join(timeout=5)
        second.join(timeout=5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(results['a'], results['b'])

    def test_old_or_invalid_cache_is_stale_and_does_not_get_rewritten(self):
        ref = self.refresher(cli_result([]))
        ref.cache.write_text(json.dumps({'version':1,'readAt':'2026-01-01T00:00:00+08:00','items':[]}))
        self.assertTrue(ref.cached()['stale'])
        ref.cache.write_text('{broken')
        self.assertTrue(ref.cached()['stale'])
        self.assertEqual(ref.cache.read_text(), '{broken')

    def test_article_only_reads_linked_trusted_document_and_removes_cli_title(self):
        calls=[]
        def run(args, **kw):
            calls.append(args)
            return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'identity':'user','data':{'document':{'content':'<title>云文档标题</title>\n\n# 正文标题\n\n正文。'}}}),stderr='')
        ref=RadarRefresher(self.config,run=run)
        ref.cache.write_text(json.dumps({'version':1,'readAt':'2026-01-01T00:00:00+08:00','items':[{'key':'test','rewrite_doc':'https://TENANT.feishu.cn/docx/Oruodk5aUoDugSxxGq4cRbo0nRb'}]}))
        value=ref.article('test','rewrite')
        self.assertEqual(value['markdown'],'# 正文标题\n\n正文。')
        self.assertIn(PROFILE := 'cli_exampleWorkspace',calls[0]);self.assertIn('+fetch',calls[0]);self.assertIn('user',calls[0])

    def test_article_bad_url_unknown_record_or_missing_draft_never_calls_cli(self):
        calls=[]
        ref=RadarRefresher(self.config,run=lambda *a,**kw:calls.append(a))
        for url in ('','https://evil.invalid/docx/Oruodk5aUoDugSxxGq4cRbo0nRb','https://TENANT.feishu.cn@evil.invalid/docx/Oruodk5aUoDugSxxGq4cRbo0nRb','file:///etc/passwd'):
            ref.cache.write_text(json.dumps({'version':1,'readAt':'2026-01-01T00:00:00+08:00','items':[{'key':'test','rewrite_doc':url}]}))
            with self.assertRaises(RadarRefreshError):ref.article('test','rewrite')
        with self.assertRaises(RadarRefreshError):ref.article('missing','rewrite')
        with self.assertRaises(RadarRefreshError):ref.article('test','other')
        self.assertEqual(calls,[])

    def test_article_failure_and_oversize_are_not_reported_as_content(self):
        ref=self.refresher(cli_result([]))
        ref.cache.write_text(json.dumps({'version':1,'readAt':'2026-01-01T00:00:00+08:00','items':[{'key':'test','rewrite_doc':'https://TENANT.feishu.cn/docx/Oruodk5aUoDugSxxGq4cRbo0nRb'}]}))
        for packet in ({'ok':False}, {'ok':True,'identity':'bot'}, {'ok':True,'identity':'user','data':{'document':{'content':'x'*200001}}}):
            ref.run=lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(packet))
            with self.assertRaises(RadarRefreshError):ref.article('test','rewrite')

    def test_cache_write_failure_still_reads_exact_just_refreshed_draft(self):
        from unittest.mock import patch
        responses=[cli_result([record('recA','新稿',rewrite='https://TENANT.feishu.cn/docx/Oruodk5aUoDugSxxGq4cRbo0nRb')]),SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'identity':'user','data':{'document':{'content':'# 新稿\n\n正文。'}}}))]
        ref=RadarRefresher(self.config,run=lambda *a,**kw:responses.pop(0))
        ref.cache.write_text(json.dumps({'version':1,'readAt':'2026-01-01T00:00:00+08:00','items':[]}))
        with patch('radar_snapshot.os.replace',side_effect=OSError('disk failure')):snapshot=ref.refresh()
        self.assertFalse(snapshot['cache_persisted'])
        self.assertEqual(ref.cached()['items'][0]['key'],snapshot['items'][0]['key'])
        self.assertEqual(ref.article(snapshot['items'][0]['key'],'rewrite')['markdown'],'# 新稿\n\n正文。')


if __name__ == '__main__':
    unittest.main()
