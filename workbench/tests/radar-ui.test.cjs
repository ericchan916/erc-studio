'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');

const ROOT = resolve(__dirname, '..');
const api = require(resolve(ROOT, 'web/radar-ui.js'));

test('metrics 统计口径', () => {
  const items = [
    { blogger: '新智元', topic_type: '热点快讯', status: '已仿写', next: '待定' },
    { blogger: '新智元', topic_type: '热点快讯', status: '待确认', next: '仿写' },
    { blogger: '卡兹克', topic_type: '教程工作流', status: '待确认', next: '待定' },
  ];
  const m = Object.fromEntries(api.metrics(items).map(x => [x.label, x.value]));
  assert.equal(m['监控博主'], 2);
  assert.equal(m['累计文章'], 3);
  assert.equal(m['热点快讯'], 2);
  assert.equal(m['待仿写'], 1);
  assert.equal(m['已仿写'], 1);
});

test('filterItems 按关键词/类型/状态过滤', () => {
  const items = [
    { title: 'GLM 发布', blogger: '新智元', pattern: '热点', topics: '', topic_type: '热点快讯', status: '待确认' },
    { title: '教程', blogger: '卡兹克', pattern: '', topics: '', topic_type: '教程工作流', status: '已仿写' },
  ];
  assert.equal(api.filterItems(items, { term: 'glm', type: '', status: '' }).length, 1);
  assert.equal(api.filterItems(items, { term: '', type: '教程工作流', status: '' }).length, 1);
  assert.equal(api.filterItems(items, { term: '', type: '', status: '已仿写' })[0].title, '教程');
  assert.equal(api.filterItems(items, { term: '', type: '', status: '' }).length, 2);
});

test('sortItems 按检测时间倒序且不修改原数组', () => {
  const items = [{ detected: '2026-10-08T10:00:00+08:00' }, { detected: '2026-10-09T20:00:00+08:00' }];
  const sorted = api.sortItems(items);
  assert.equal(sorted[0].detected, '2026-10-09T20:00:00+08:00');
  assert.equal(items[0].detected, '2026-10-08T10:00:00+08:00');
});

test('页面脚本接线完整（导航注册与后端接口一致）', () => {
  const app = readFileSync(resolve(ROOT, 'web/app.js'), 'utf8');
  assert.ok(app.includes("['radar','选题']"), '侧栏需注册选题导航');
  const index = readFileSync(resolve(ROOT, 'web/index.html'), 'utf8');
  assert.ok(index.includes('radar-experience-icons.css') && index.includes('radar-article.js') && index.includes('radar-ui.js'), 'index.html 需登记版本化样式、排版器与页面脚本');
  const adapter = readFileSync(resolve(ROOT, 'backend/web/browser-adapter.js'), 'utf8');
  assert.ok(adapter.includes("request('/v1/radar/snapshot"), '浏览器适配层需接 radar 接口');
  const server = readFileSync(resolve(ROOT, 'backend/server.py'), 'utf8');
  assert.ok(server.includes("path=='/v1/radar/snapshot'"), '后端需有 radar 路由');
});

test('空数据不虚构五位监控博主，已存在初稿不算排队', () => {
  assert.equal(api.metrics([])[0].value, 0);
  assert.equal(api.metrics([{next:'仿写',rewrite_doc:'https://example.com'}])[3].value, 0);
});

test('已有初稿、热点、博主与去空白搜索可以组合', () => {
  const items=[{title:'GLM 发布',blogger:'A',topic_type:'热点快讯',rewrite_doc:'https://example.com'}, {title:'GLM 教程',blogger:'B',topic_type:'教程工作流'}];
  assert.equal(api.filterItems(items,{term:'  glm ',quick:'draft',blogger:'A'}).length,1);
  assert.equal(api.filterItems(items,{quick:'hot',blogger:'B'}).length,0);
  assert.equal(api.filterItems(items,{quick:'draft'})[0].blogger,'A');
});

test('链接限定HTTPS且没有用户名密码，快照有明确版本与稳定ID', () => {
  assert.equal(api.safeURL('javascript:alert(1)'), '');
  assert.equal(api.safeURL('https://user:pass@example.com'), '');
  assert.equal(api.safeURL('https://example.com/a'), 'https://example.com/a');
  assert.equal(api.validSnapshot({version:1,items:[{key:'x'}]}),true);
  assert.equal(api.validSnapshot({items:[]}),false);
  assert.equal(api.validSnapshot({version:1,items:[null]}),false);
});

test('缺失日期与坏日期不混为真实时间', () => {
  assert.equal(api.fmt(null),'时间未提供');
  assert.equal(api.fmt('invalid'),'时间待核实');
});

test('晚到稿件：身份、视图、编辑版本、页面与选题任一变化都保留原稿', () => {
  const request={epoch:1,view:3,revision:2,mode:'discovery',key:'one'};
  assert.equal(api.isDraftCurrent(request,{...request}),true);
  for(const key of ['epoch','view','revision','mode','key'])assert.equal(api.isDraftCurrent(request,{...request,[key]:key+'changed'}),false);
});

test('选题使用独立雷达，内容灯泡保持原样且侧栏尺寸一致', () => {
  const app=readFileSync(resolve(ROOT,'web/app.js'),'utf8');
  const ui=readFileSync(resolve(ROOT,'web/radar-ui.js'),'utf8');
  const css=readFileSync(resolve(ROOT,'web/radar.css'),'utf8');
  assert.match(app,/function contentNavIcon\(\).*vendor\/tabler-content\/bulb\.svg/);
  assert.match(ui,/function mascot\(\).*e\.append\(icon\('radar-2'\)\)/);
  assert.ok(!ui.includes("icon('bulb')"));
  assert.ok(!css.includes('radar-eyes'));
  assert.match(css,/\.nav-item \.radar-mascot\{width:19px;height:19px;/);
  assert.match(css,/\.nav-item \.radar-mascot>\.radar-icon\{width:19px;height:19px\}/);
});

test('雷达SVG为固定MIT来源，窄部署包含资源且刷新样式缓存', () => {
  const {createHash}=require('node:crypto');
  const svg=readFileSync(resolve(ROOT,'web/vendor/tabler-content/radar-2.svg'));
  assert.equal(createHash('sha256').update(svg).digest('hex'),'7e910b96d08c54cbda8a125b0c25f9d68d1d1b9c3910d5511ba86defa7485152');
  const installer=readFileSync(resolve(ROOT,'scripts/install-radar-experience.py'),'utf8');
  assert.ok(installer.includes("'office-app/web/vendor/tabler-content/radar-2.svg'"));
  const index=readFileSync(resolve(ROOT,'web/index.html'),'utf8');
  assert.ok(index.includes('radar-experience-icons.css'));
});
