'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const md=require('../web/vendor/markdown-it.min.js');
const api=require('../web/radar-article.js'),formatter=api.create(md);
test('标题单独复制，正文保真且样式内联',()=>{
 const value=formatter.render('# 我的标题\n\n正文与**关键观点**。\n\n## 小节\n\n> 引用。\n\n- 条目甲\n- 条目乙');
 assert.equal(value.title,'我的标题');assert.ok(!value.html.includes('我的标题'));
 for(const text of ['正文与','关键观点','小节','引用。','条目甲','条目乙'])assert.ok(value.html.includes(text));
 assert.ok(value.html.includes('font-size:15px'));assert.ok(value.html.includes('leaf=""'));
 assert.ok(!/class=|id=|<style|<script/.test(value.html));
});
test('原始HTML、协议与恶意属性不激活',()=>{
 const html=formatter.render('<script>alert(1)</script>\n\n![图](https://example.com/a.png)\n\n[坏链接](javascript:alert(1))').html;
 assert.ok(!html.includes('<script>'));assert.ok(html.includes('&lt;script&gt;'));
 assert.ok(!/href="javascript:/.test(html));assert.ok(!/onerror=/.test(html));
 assert.ok(html.includes('max-width:100%;height:auto;display:block;margin:0 auto;'));
});
test('代码换行/缩进、表格、普通链接和Markdown不被吞掉',()=>{
 const html=formatter.render('```js\n  a < b\n  c()\n```\n\n|列一|列二|\n|---|---|\n|值一|值二|\n\n[链接](https://example.com)\n\n[相对链接](/private)').html;
 assert.ok(html.includes('a\u00a0&lt;\u00a0b'));assert.ok(html.includes('<table'));
 assert.ok(html.includes('值二'));assert.ok(html.includes('href="https://example.com/"'));
 assert.ok(!html.includes('href="/private"'));assert.ok(html.includes('相对链接'));
});
test('无一级标题也能完整排版，图片缺失明确标记',()=>{
 const value=formatter.render('普通正文\n\n![图片](/missing.png)');
 assert.equal(value.title,'');assert.ok(value.html.includes('普通正文'));assert.ok(value.html.includes('图片待补'));
});
test('中文连续书写的加粗不泄漏星号，转义与代码原样保持',()=>{
 for(const source of ['**重点。**而它继续','**重点`code`。**而它继续']){
  const value=formatter.render(source);assert.ok(value.html.includes('<strong'));assert.ok(!value.html.includes('**'));
 }
 assert.ok(formatter.render('\\*\\*字。\\*\\*继续').html.includes('**字。**继续'));
 assert.ok(!formatter.render('\\*\\*字。\\*\\*继续').html.includes('<strong'));
 assert.ok(formatter.render('`**字。**继续`').html.includes('**字。**继续'));
});
test('只有标题时不冒充有正文，Markdown备份仍保留标题',()=>{
 assert.equal(formatter.render('# 仅标题').title,'仅标题');
 assert.equal(formatter.render('# 仅标题').hasBody,false);
 assert.equal(formatter.render('# 标题\n\n正文。').hasBody,true);
});

test('预览没有内联style，固定声明与本地CSS逐字一致，导出仍保留内联样式',()=>{
 const fs=require('node:fs'),path=require('node:path');
 const css=fs.readFileSync(path.resolve(__dirname,'../web/radar.css'),'utf8');
 assert.ok(css.includes(api.previewStylesheet()));
 const source='# 标题\n\n**重点。**而它继续。\n\n> 引用\n\n## 章节\n\n1. 一\n2. 二\n\n- 项\n\n[链接](https://example.com)\n\n![图](https://example.com/a.png)\n\n`code`\n\n```js\na < b\n```\n\n---\n\n|a|b|\n|---|---|\n|x|y|';
 const value=formatter.render(source);
 assert.ok(!/\sstyle=|<style/.test(value.previewHtml));assert.ok(/class="radar-article-/.test(value.previewHtml));
 assert.ok(/\sstyle=/.test(value.html));assert.ok(!/\sclass=/.test(value.html));
 const text=html=>html.replace(/<[^>]+>/g,'');assert.equal(text(value.previewHtml),text(value.html));
 assert.throws(()=>api.previewMarkup('<p style="position:fixed">x</p>'),/not_registered/);
});

function clipboardFixture(accepted=true,hasData=true){
 const contents={},handlers=new Map();let selected=false,removed=false;
 const document={
  body:{append(){}},createElement(){return {select(){selected=true;},remove(){removed=true;}};},
  addEventListener(name,handler){handlers.set(name,handler);},removeEventListener(name){handlers.delete(name);},
  execCommand(command){assert.equal(command,'copy');const event={preventDefault(){this.prevented=true;},clipboardData:hasData?{setData(type,value){contents[type]=value;}}:null};handlers.get('copy')?.(event);return accepted;}
 };
 return {document,contents,handlers,selected:()=>selected,removed:()=>removed};
}
test('旧浏览器复制路径明确写入HTML和plain，正文不依赖插入带style的DOM',()=>{
 const fixture=clipboardFixture(),html=formatter.render('# 标题\n\n正文。').html;
 api.copyFallback(fixture.document,'正文。',html);
 assert.equal(fixture.contents['text/plain'],'正文。');assert.equal(fixture.contents['text/html'],html);
 assert.ok(fixture.selected());assert.ok(fixture.removed());assert.equal(fixture.handlers.size,0);
});
test('复制被拒绝或没有clipboardData时不回报成功，并清理事件和临时节点',()=>{
 for(const fixture of [clipboardFixture(false),clipboardFixture(true,false)]){
  assert.throws(()=>api.copyFallback(fixture.document,'正文','<p>正文</p>'),/clipboard_failed/);
  assert.ok(fixture.removed());assert.equal(fixture.handlers.size,0);
 }
});
