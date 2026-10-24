'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const css=fs.readFileSync(process.env.WORKFLOW_CSS_UNDER_TEST||path.join(__dirname,'../web/workflow.css'),'utf8');
const rule=selector=>{const start=css.indexOf(selector+' {');assert.ok(start>=0,selector);return css.slice(start,css.indexOf('}',start));};
test('workflow text keeps readable 14px floor, larger main table and detail typography',()=>{
 const sizes=[...css.matchAll(/font-size:\s*(\d+)px/g)].map(x=>Number(x[1]));assert.ok(sizes.length>15);assert.ok(sizes.every(x=>x>=14),'responsive rules must not shrink text below 14 CSS px');
 assert.match(rule('.workflow-table.table'),/font-size: 16px/);assert.match(rule('.workflow-table td strong'),/font-size: 17px/);assert.match(rule('.workflow-field p'),/font-size: 15px/);
 assert.match(rule('.workflow-table td strong'),/white-space: normal/);assert.match(css,/td:nth-child\(6\) \{ white-space: normal/);
});
test('workflow responsive columns follow workspace container width without hiding core evidence',()=>{
 assert.match(rule('.workflow-page'),/container: workflow \/ inline-size/);
 for(const width of [1120,900,840,700])assert.ok(css.includes('@container workflow (max-width: '+width+'px)'));
 assert.ok(css.includes('.workflow-table th:nth-child(5), .workflow-table td:nth-child(5) { display: none; }'));
 assert.ok(css.includes('.workflow-table th:nth-child(3), .workflow-table td:nth-child(3) { display: none; }'));
 for(const col of [1,2,4,6,7])assert.ok(!css.includes('.workflow-table th:nth-child('+col+'), .workflow-table td:nth-child('+col+') { display: none; }'));
 assert.match(rule('.workflow-table-wrap.table-responsive'),/overflow: auto/);assert.ok(css.includes('.workflow-layout, .workflow-content { flex: none; min-height: auto; }'));assert.ok(css.includes('height: clamp(200px, 45vh, 420px)'));
});
test('workflow actions have 44px targets and detail width adapts without introducing cards or global selectors',()=>{
 for(const selector of ['.workflow-button.btn','.workflow-detail-button.btn-link','.workflow-footer .workflow-button','.workflow-dependency'])assert.match(rule(selector),/min-height: 44px/);
 assert.match(rule('.workflow-drawer'),/width: clamp\(520px, 38vw, 680px\)/);assert.match(rule('.workflow-drawer'),/max-width: 100vw/);
 for(const selector of ['.workflow-summary','.workflow-filters.nav-tabs','.workflow-table-wrap.table-responsive']){const body=rule(selector);assert.match(body,/border: 0/);assert.match(body,/background: transparent/);assert.match(body,/box-shadow: none/);}
 assert.ok(!css.includes('body {'));assert.ok(!css.includes('zoom:'));assert.ok(!css.includes('transform: scale'));
});
