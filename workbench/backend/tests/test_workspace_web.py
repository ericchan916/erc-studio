# -*- coding: utf-8 -*-
import json,pathlib,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from server import workspace_links,mirror_file,browser_index,public_site_target,readonly_content,Problem

class Tests(unittest.TestCase):
 def test_account_snapshot_projects_metrics_without_forcing_success(self):
  from unittest.mock import patch
  from server import read_snapshot
  with tempfile.TemporaryDirectory() as t:
   home=pathlib.Path(t);root=home/'Library/Application Support/ERC Studio';root.mkdir(parents=True)
   p=root/'accounts.json';p.write_text(json.dumps({'accounts':[{'id':'fixture','sourceKey':'private','partition':'private'}]}));p.chmod(0o600)
   result={'accounts':[{'id':'fixture','accountMetrics':{'totalFans':{'value':None,'status':'unverified'}}}],'metadataRefresh':{'phase':'error','error':'source_timeout'}}
   with patch('server.Path.home',return_value=home),patch('server.account_snapshot',return_value=result) as call:
    value=read_snapshot({'workspace_state_dir':str(root)},'accounts')
   self.assertEqual(value['metadataRefresh']['phase'],'error');self.assertEqual(value['browserMode'],'web_native');self.assertNotIn('sourceKey',value['accounts'][0]);self.assertEqual(call.call_count,1)
 def test_browser_adapter_covers_native_preload_surface_without_business_secrets(self):
  script=(ROOT/'web/browser-adapter.js').read_text()
  for token in ['conversation:{call:conversation}','tasks:','website:','content:','dataReview:','analytics:','bar:','list:accounts','open:async id']:
   self.assertIn(token,script)
  self.assertIn("request('/v1/workspace/links')",script)
  self.assertIn("snapshot('content')",script);self.assertIn('window.StudioAnalyticsTransport.create',script);self.assertIn("snapshot('bar')",script)
  transport=(ROOT.parent/'web/analytics-transport.js').read_text();self.assertIn('kind=analytics&days=',transport);self.assertIn('&refresh=1',transport);self.assertIn("request('/v1/analytics/sync'",transport);self.assertNotIn('sync:readonly',script)
  self.assertIn("snapshot('content',undefined,true)",script);self.assertIn('contentRefreshInFlight',script);self.assertIn("emit('content-state',value)",script)
  self.assertNotIn('.feishu.cn/',script)
  self.assertNotIn('Xp4rbIas',script)
  self.assertIn("window.open(u.href,'_blank','noopener,noreferrer')",script)
  self.assertIn("frame.src='/?embedded_site=1'",script)
  self.assertIn("官网已在当前页面内嵌",script)
  self.assertIn("tasks:embeddedPane('tasks','task-mount')",script)
  self.assertIn("project:embeddedPane('content','content-project-mount')",script)
  self.assertIn("videoScripts:embeddedPane('videoScripts','content-video-script-mount')",script)
  self.assertIn("articleScripts:embeddedPane('articleScripts','content-article-script-mount')",script)
  self.assertIn("dataReview:embeddedPane('reviews','data-review-mount')",script)
  self.assertNotIn("review:embeddedPane('reviews','content-review-mount')",script)
  self.assertIn("library:embeddedPane('barFolder','task-mount')",script)
  self.assertIn("dataset.cyberFeishuEmbed==='enabled'",script)
  self.assertIn("'/studio-feishu-embed.zip'",script)
  self.assertIn("window.location.assign(u.href)",script)
  self.assertNotIn('captureScreenshot',script)
 def test_browser_extension_narrowly_removes_feishu_subframe_guards(self):
  extension=ROOT.parent/'browser-extension';manifest=json.loads((extension/'manifest.json').read_text());rules=json.loads((extension/'rules.json').read_text())
  self.assertEqual(manifest['manifest_version'],3);self.assertIn('declarativeNetRequestWithHostAccess',manifest['permissions']);self.assertIn('contentSettings',manifest['permissions'])
  self.assertEqual(rules[0]['condition']['resourceTypes'],['sub_frame']);self.assertEqual(rules[0]['condition']['requestDomains'],['feishu.cn'])
  removed={x['header'] for x in rules[0]['action']['responseHeaders'] if x['operation']=='remove'}
  self.assertEqual(removed,{'x-frame-options','content-security-policy','content-security-policy-report-only','x-webkit-csp'})
  background=(extension/'background.js').read_text();self.assertIn("secondaryPattern:'https://studio.example.invalid/*'",background);self.assertNotIn('chrome.cookies',background)
 def test_workspace_link_allowlist_rejects_credentials_and_unknown_hosts(self):
  good=workspace_links({'workspace_links':{'tasks':'https://tenant.feishu.cn/base/BASE','videoScripts':'https://tenant.feishu.cn/drive/folder/Video','articleScripts':'https://tenant.feishu.cn/drive/folder/Article','website':'https://example.invalid','platforms':[{'id':'wechat','name':'公众号','url':'https://mp.weixin.qq.com/'}]}})
  self.assertEqual(good['tasks'],'https://tenant.feishu.cn/base/BASE')
  self.assertTrue(good['videoScripts'].endswith('/Video'));self.assertTrue(good['articleScripts'].endswith('/Article'))
  for value in ['https://user:pass@tenant.feishu.cn/base/X','https://evil.test/base/X','http://tenant.feishu.cn/base/X']:
   with self.assertRaises(ValueError):workspace_links({'workspace_links':{'tasks':value}})
 def test_mirror_static_path_is_bounded_and_serves_exact_app_tree_names(self):
  with tempfile.TemporaryDirectory() as t:
   root=pathlib.Path(t);(root/'web/assets').mkdir(parents=True);(root/'node_modules/x').mkdir(parents=True)
   (root/'web/index.html').write_text('app');(root/'web/assets/model.glb').write_bytes(b'glTF');(root/'node_modules/x/a.js').write_text('x')
   self.assertEqual(mirror_file('/',root),(root/'web/index.html').resolve())
   self.assertEqual(mirror_file('/assets/model.glb',root),(root/'web/assets/model.glb').resolve())
   self.assertEqual(mirror_file('/node_modules/x/a.js',root),(root/'node_modules/x/a.js').resolve())
   for path in ['/../../config.json','/%2e%2e/config.json','/x.py']:
    with self.assertRaises(Problem):mirror_file(path,root)
 def test_browser_index_allows_only_browser_side_https_frames(self):
  raw=b"<body>connect-src 'self'; object-src 'none'</body>"
  self.assertIn(b"connect-src 'self'; frame-src 'self' https:; object-src 'none'",browser_index(raw))
  self.assertIn(b'/publish-console.js',browser_index(raw))
  with self.assertRaises(Problem):browser_index(b'no contract')
 def test_local_publish_preview_allows_blob_images_without_relaxing_remote_execution(self):
  value=browser_index(b"<body>connect-src 'self'; object-src 'none'; img-src 'self' data: https:</body>")
  self.assertIn(b"img-src 'self' data: blob: https:",value)
  self.assertNotIn(b"script-src *",value)
  self.assertNotIn(b"unsafe-eval",value)
 def test_production_media_policy_is_local_blob_only(self):
  source=(pathlib.Path(__file__).resolve().parents[1]/'server.py').read_text()
  self.assertIn("media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'",source)
  self.assertNotIn("media-src *",source)
  self.assertNotIn("media-src 'self' https:",source)
 def test_public_site_proxy_is_fixed_to_public_origin_and_does_not_shadow_app(self):
  self.assertEqual(public_site_target('/','embedded_site=1'),'https://example.invalid/')
  self.assertEqual(public_site_target('/events/','view=all'),'https://example.invalid/events/?view=all')
  self.assertEqual(public_site_target('/assets/site.js',''),'https://example.invalid/assets/site.js')
  self.assertIsNone(public_site_target('/','','document'))
  self.assertEqual(public_site_target('/','','iframe'),'https://example.invalid/')
  for path in ['/v1/me','/browser-adapter.js','/assets/model.glb','/../../private']:
   self.assertIsNone(public_site_target(path,''))

if __name__=='__main__':unittest.main()

class ReviewLinksTests(unittest.TestCase):
 def test_review_link_is_distinct_and_permission_neutral(self):
  links=workspace_links({'workspace_links':{'content':'https://tenant.feishu.cn/base/Content','reviews':'https://tenant.feishu.cn/base/Review?table=tblReview'}})
  self.assertNotEqual(links['content'],links['reviews'])
  for bad in ['http://tenant.feishu.cn/base/X','https://evil.test/base/X','https://user:pass@tenant.feishu.cn/base/X']:
   with self.assertRaises(ValueError):workspace_links({'workspace_links':{'reviews':bad}})
