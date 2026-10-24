"""Behavioral audit against synthetic DOM and API fixtures; no vendor calls.

These assertions describe intended user-facing invariants. They intentionally
expose defects in the current frontend until the corresponding fixes land.
Only Node built-ins and Python's standard library are used.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from contentbase.service import Service

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

HARNESS = r'''
const fs=require("fs"), vm=require("vm");
const script=fs.readFileSync(process.argv[1],"utf8");
const html=fs.readFileSync(process.argv[2],"utf8");
const scenario=process.argv[3];
class Element {
 constructor(tag="div",id=""){this.tagName=tag.toUpperCase();this.id=id;this.children=[];this.value="";this.textContent="";this.disabled=false;this.files=[];this.dataset={};this.handlers={};this.attrs={};const classes=new Set();this.classList={add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,force)=>{const yes=force===undefined?!classes.has(x):force;if(yes)classes.add(x);else classes.delete(x);return yes;}};}
 append(...children){for(const c of children){this.children.push(c);c.parentNode=this;}if(this.tagName==="SELECT"&&!this.value){const option=this.children.find(c=>!c.disabled);if(option)this.value=option.value;}}
 appendChild(c){this.append(c);return c;}
 replaceChildren(...children){this.children=[];this.textContent="";this.append(...children);}
 querySelectorAll(selector){const all=[];function visit(node){for(const c of node.children){all.push(c);visit(c);}}visit(this);return all.filter(c=>selector==="button"?c.tagName==="BUTTON":selector==="input, textarea, select"?["INPUT","TEXTAREA","SELECT"].includes(c.tagName):selector.startsWith(".")?c.classList.contains(selector.slice(1)):false);}
 querySelector(s){return this.querySelectorAll(s)[0]||null;}
 addEventListener(type,fn){(this.handlers[type]??=[]).push(fn);}
 dispatchEvent(event){event.target=this;event.currentTarget=this;for(const fn of this.handlers[event.type]||[])fn(event);if(this["on"+event.type])this["on"+event.type](event);return true;}
 setAttribute(name,value){this.attrs[name]=value;}
 getAttribute(name){return this.attrs[name]??null;}
 removeAttribute(name){delete this.attrs[name];}
 focus(){} scrollIntoView(){}
}
function storage(map){return {getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,String(v)),removeItem:k=>map.delete(k),clear:()=>map.clear(),key:i=>[...map.keys()][i]??null,get length(){return map.size;}};}
function make(route,sharedSession=new Map(),sharedLocal=new Map()){
 const elements={};for(const m of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g))elements[m[2]]=new Element(m[1],m[2]);
 const nav=["content","operations","publishing","setup"].map(name=>{const b=new Element("button");b.dataset.tab=name;return b;});
 elements["publish-kind"].value="imageText";elements["publish-channel"].value="cloud";elements["publish-fields"].value="{}";
 const document={getElementById:id=>elements[id],createElement:tag=>new Element(tag),querySelectorAll:s=>s==="nav button"?nav:s===".page"?["content","operations","publishing","setup"].map(x=>elements[x]):s==="button"?[...Object.values(elements).filter(x=>x.tagName==="BUTTON"),...nav,...Object.values(elements).flatMap(e=>e.querySelectorAll("button"))]:s==="input, textarea, select"?Object.values(elements).filter(x=>["INPUT","TEXTAREA","SELECT"].includes(x.tagName)):[],addEventListener(){}};
 const calls=[];let serial=0;
 const context={document,sessionStorage:storage(sharedSession),localStorage:storage(sharedLocal),crypto:{randomUUID:()=>"fixture-"+(++serial),subtle:require("crypto").webcrypto.subtle},window:{confirm:()=>true,addEventListener(){}},console,setTimeout,clearTimeout,setInterval,clearInterval,AbortController,URL,Uint8Array,TextEncoder,TextDecoder,Date,Event:class{constructor(type){this.type=type;}},btoa:x=>Buffer.from(x,"binary").toString("base64"),atob:x=>Buffer.from(x,"base64").toString("binary"),fetch:async(path,opts={})=>{const body=opts.body?JSON.parse(opts.body):null;calls.push({path,body,authorization:opts.headers?.Authorization});const value=await route(path,body,calls);return value?.__error?{ok:false,json:async()=>({ok:false,error:value.__error})}:{ok:true,json:async()=>({ok:true,data:value})};}};
 context.window.document=document;context.window.sessionStorage=context.sessionStorage;context.window.localStorage=context.localStorage;context.location={origin:"http://127.0.0.1:8787",pathname:"/"};context.window.location=context.location;
 vm.createContext(context);vm.runInContext(script,context,{filename:"app.js"});
 return {elements,nav,calls,context,session:sharedSession,local:sharedLocal,click:async id=>elements[id].onclick(),input:(id,value)=>{elements[id].value=value;elements[id].dispatchEvent({type:"input"});elements[id].dispatchEvent({type:"change"});}};
}
const source={identity_fingerprint:"scope_fixture",records:[{record_id:"recFixtureA",fields:{title:"A",body:"source A"}},{record_id:"recFixtureB",fields:{title:"B",body:"source B"}}],field_map:{title:"title",body:"body"}};
const account={id:"account_fixture",platformName:"小红书",platformAccountName:"synthetic",status:1,isOperate:true};
const op={id:"op_fixture",kind:"draft",state:"prepared",confirmation_digest:"fixture-digest",data:{plan:{fields:{title:"A",body:"source A"}}}};
const file={name:"fixture.png",size:8,arrayBuffer:async()=>new Uint8Array([137,80,78,71,13,10,26,10]).buffer};
function stable(value){if(Array.isArray(value))return value.map(stable);if(value&&typeof value==="object")return Object.fromEntries(Object.keys(value).sort().map(key=>[key,stable(value[key])]));return value;}
function rowPacket(row){return {record:JSON.parse(JSON.stringify(row)),source_fingerprint:require("crypto").createHash("sha256").update(JSON.stringify(stable(row))).digest("hex"),field_map:source.field_map,identity_fingerprint:"scope_fixture"};}
async function selectFirst(ui){await ui.click("sync");await ui.elements.records.querySelectorAll("button")[0].onclick();}
async function baseRoute(path){if(path.startsWith("/api/records/")){const row=source.records.find(r=>r.record_id===decodeURIComponent(path.split("/").at(-1)));return rowPacket(row);}if(path.startsWith("/api/operation-page"))return {items:[],next_offset:null,has_more:false,identity_fingerprint:"scope_fixture"};if(path==="/api/sync")return source;if(path==="/api/accounts")return {accounts:[account],provider_fingerprint:"provider_fixture",provider_binding:{},identity_fingerprint:"scope_fixture"};if(path==="/api/operations")return [];if(path==="/api/media")return {id:"media_fixture"};return op;}
(async()=>{
 let result;
 if(scenario==="lost_response"){
  let attempts=0;const ui=make(async(path,body)=>{if(path==="/api/draft"){if(++attempts===1)throw new Error("synthetic_response_lost");return op;}return baseRoute(path);});
  await selectFirst(ui);ui.elements["draft-title"].value="A";ui.elements["draft-body"].value="same body";
  await ui.click("draft");await ui.click("draft");result={keys:ui.calls.filter(x=>x.path==="/api/draft").map(x=>x.body.key)};
 }else if(scenario==="lost_publish_response"){
  let uploads=0,attempts=0;const ui=make(async(path,body)=>{if(path==="/api/media")return {id:"media_fixture_"+(++uploads)};if(path==="/api/publish/prepare"){if(++attempts===1)throw new Error("synthetic_response_lost");return {...op,kind:"publish",state:"ready"};}return baseRoute(path);});
  await ui.click("accounts");ui.elements["publish-account"].value=account.id;ui.elements["publish-record"].value="recFixtureA";ui.elements["publish-media"].files=[file];await ui.click("prepare-publish");await ui.click("prepare-publish");result={requests:ui.calls.filter(x=>x.path==="/api/publish/prepare").map(x=>x.body),uploads};
 }else if(scenario==="record_draft"){
  const ui=make(baseRoute);await ui.click("sync");let buttons=ui.elements.records.querySelectorAll("button");await buttons[0].onclick();ui.input("draft-body","unsaved A");await buttons[1].onclick();await buttons[0].onclick();result={body:ui.elements["draft-body"].value};
 }else if(scenario==="refresh_draft"){
  const session=new Map(),local=new Map();const ui=make(baseRoute,session,local);await ui.click("sync");await ui.elements.records.querySelectorAll("button")[0].onclick();ui.input("draft-body","unsaved A");const refreshed=make(baseRoute,session,local);await refreshed.click("sync");await refreshed.elements.records.querySelectorAll("button")[0].onclick();result={body:refreshed.elements["draft-body"].value};
 }else if(scenario==="credential_change"){
  const ui=make(baseRoute);await ui.click("sync");await ui.click("accounts");await ui.elements.records.querySelectorAll("button")[0].onclick();ui.input("draft-body","private unsaved text");ui.elements.choices.textContent="private choices";ui.elements["operation-list"].textContent="private evidence";ui.input("token","new-fixture-token");await ui.click("connect");result={draft:ui.elements["draft-body"].value,record:ui.elements["record-id"].value,records:ui.elements.records.children.length,accountOptions:ui.elements["publish-account"].children.length,choices:ui.elements.choices.textContent,evidence:ui.elements["operation-list"].textContent};
 }else if(scenario==="cancel_record_switch"){
  const ui=make(baseRoute);await ui.click("sync");const buttons=ui.elements.records.querySelectorAll("button");await buttons[0].onclick();ui.input("draft-body","unsaved A");ui.context.window.confirm=()=>false;await buttons[1].onclick();result={record:ui.elements["record-id"].value,body:ui.elements["draft-body"].value};
 }else if(scenario==="same_token_identity_change"){
  let changed=false;const ui=make(async path=>path.startsWith("/api/operation-page")?{items:[],has_more:false,next_offset:null,identity_fingerprint:changed?"scope_other":"scope_fixture"}:baseRoute(path));await ui.click("sync");await ui.elements.records.querySelectorAll("button")[0].onclick();ui.input("draft-body","private owner draft");changed=true;await ui.click("draft");result={draftCalls:ui.calls.filter(x=>x.path==="/api/draft").length,body:ui.elements["draft-body"].value,records:ui.elements.records.children.length};
 }else if(scenario==="changed_intent"){
  const ui=make(baseRoute);await selectFirst(ui);ui.elements["draft-title"].value="A";ui.elements["draft-body"].value="first";await ui.click("draft");ui.input("draft-body","second");await ui.click("draft");result={keys:ui.calls.filter(x=>x.path==="/api/draft").map(x=>x.body.key)};
 }else if(scenario==="dynamic_choice_receipt"){
  const snapshot={receipt_id:"receipt_fixture",field:"music",account_id:account.id,kind:"imageText",query:"fixture query",options:[{index:0,id:"fixture_hash_a",value:{name:"synthetic A"},label:"synthetic A"},{index:1,id:"fixture_hash_b",value:{name:"synthetic B"},label:"synthetic B"}]};
  const ui=make(async path=>path==="/api/choices"?snapshot:baseRoute(path));await ui.click("accounts");ui.elements["publish-account"].value=account.id;ui.elements["publish-record"].value="recFixtureA";ui.elements["publish-fields"].value=JSON.stringify({music_query:"fixture query"});await ui.click("music");ui.elements["choice-options"].querySelectorAll("button")[0].onclick();result={fields:JSON.parse(ui.elements["publish-fields"].value)};
 }else if(scenario==="full_record_dict_text"){
  const full={record_id:"recFixtureA",fields:{title:{text:"FULL TITLE"},body:{text:"FULL BODY"}}};const ui=make(async path=>path==="/api/records/recFixtureA"?rowPacket(full):baseRoute(path));await selectFirst(ui);await ui.click("draft");result={title:ui.elements["draft-title"].value,body:ui.elements["draft-body"].value,posted:ui.calls.find(x=>x.path==="/api/draft").body,fullCalls:ui.calls.filter(x=>x.path==="/api/records/recFixtureA").length,expected:rowPacket(full).source_fingerprint};
 }else if(scenario==="strict_text_segments"){
  const full={record_id:"recFixtureA",fields:{title:"A",body:[{text:"good text"},{resource:"not a text segment"}]}};const ui=make(async path=>path==="/api/records/recFixtureA"?rowPacket(full):baseRoute(path));await selectFirst(ui);await ui.click("draft");result={body:ui.elements["draft-body"].value,calls:ui.calls.filter(x=>x.path==="/api/draft").length,notice:ui.elements.notice.textContent};
 }else if(scenario==="source_rebase"){
  let body="ORIGINAL FULL";const full=()=>({record_id:"recFixtureA",fields:{title:"A",body}});const ui=make(async path=>path==="/api/records/recFixtureA"?rowPacket(full()):baseRoute(path));await selectFirst(ui);ui.input("draft-body","LOCAL DRAFT");await ui.click("draft");body="LATEST SERVER ORIGINAL";await ui.click("sync");const kept=ui.elements["draft-body"].value,latest=ui.elements["source-original"].textContent;await ui.click("draft");const blockedCalls=ui.calls.filter(x=>x.path==="/api/draft").length;await ui.click("rebase-source");await ui.click("draft");const calls=ui.calls.filter(x=>x.path==="/api/draft");result={kept,latest,blockedCalls,finalBody:ui.elements["draft-body"].value,requests:calls.map(x=>x.body),expected:rowPacket(full()).source_fingerprint};
 }else if(scenario==="workspace_pin_change"||scenario==="workspace_pin_ambiguous"){
  let failed=false;const code=scenario==="workspace_pin_change"?"workspace_operator_changed":"workspace_operator_ambiguous";const ui=make(async path=>failed&&path.startsWith("/api/operation-page")?{__error:{code,unknown:false}}:baseRoute(path));await selectFirst(ui);ui.input("draft-body","private old owner");failed=true;await ui.click("draft");result={body:ui.elements["draft-body"].value,record:ui.elements["record-id"].value,records:ui.elements.records.children.length,notice:ui.elements.notice.textContent,calls:ui.calls.filter(x=>x.path==="/api/draft").length};
 }else if(scenario==="apply_baseline"||scenario==="apply_mid_edit"){
  let applied=false,ui;const wanted={title:"A",body:"SUBMITTED BODY"};const plan={record_id:"recFixtureA",fields:wanted};const row={record_id:"recFixtureA",fields:wanted};const saved={...op,state:"applied",data:{plan,readback:row,readback_source_fingerprint:rowPacket(row).source_fingerprint}};ui=make(async path=>{if(path.startsWith("/api/operation-page"))return {items:[applied?saved:{...op,state:"approved",data:{plan}}],next_offset:null,has_more:false,identity_fingerprint:"scope_fixture"};if(path==="/api/apply"){if(scenario==="apply_mid_edit")ui.input("draft-body","EDITED DURING APPLY");applied=true;return saved;}return baseRoute(path);});await selectFirst(ui);ui.input("draft-body","SUBMITTED BODY");await ui.click("reload-operations");const action=ui.elements["operation-list"].querySelectorAll("button").find(x=>x.textContent.includes("应用"));await action.onclick();result={body:ui.elements["draft-body"].value,rebaseHidden:ui.elements["rebase-source"].classList.contains("hidden"),source:ui.elements["source-state"].textContent,expected:rowPacket(row).source_fingerprint};
 }else if(scenario==="source_success_notice"){
  const ui=make(baseRoute);await selectFirst(ui);result={notice:ui.elements.notice.textContent,enabled:!ui.elements["draft"].disabled};
 }else if(scenario==="history_fold"){
  const items=[{...op,state:"applied"},{...op,id:"op_submitted",kind:"publish",state:"submitted",data:{payload:{publishArgs:{accountForms:[{contentPublishForm:{title:"published title",description:"complete published body"}}]}}}},{...op,id:"op_prepared",state:"prepared",data:{plan:{record_id:"recFixtureA",fields:{title:"review title",body:"COMPLETE REVIEW BODY"}}}},{...op,id:"op_unknown",state:"unknown"}];
  const ui=make(async path=>path.startsWith("/api/operation-page")?{items,next_offset:null,has_more:false,identity_fingerprint:"scope_fixture"}:baseRoute(path));await ui.click("reload-operations");result={cards:ui.elements["operation-list"].children.map(article=>({details:article.children.filter(x=>x.tagName==="DETAILS").map(x=>({open:!!x.open,summary:x.children[0].textContent,raw:x.children[1].textContent})),directPreviews:article.children.filter(x=>x.tagName==="PRE").map(x=>x.textContent)}))};
 }else if(scenario==="unknown_recovery"){
  const ui=make(async path=>path.startsWith("/api/operation-page")?{items:[{...op,state:"unknown"}],next_offset:null,has_more:false,identity_fingerprint:"scope_fixture"}:baseRoute(path));await ui.click("reload-operations");result={labels:ui.elements["operation-list"].querySelectorAll("button").map(x=>x.textContent)};
 }else if(scenario==="long_navigation"){
  let release;const gate=new Promise(r=>release=r);const ui=make(async path=>path==="/api/draft"?gate:baseRoute(path));await selectFirst(ui);ui.elements["draft-title"].value="A";ui.elements["draft-body"].value="A";const running=ui.click("draft");await new Promise(r=>setImmediate(r));result={navigationDisabled:ui.nav.every(x=>x.disabled)};release(op);await running;
 }else if(scenario==="long_navigation_focus"){
  let release;const gate=new Promise(r=>release=r);const ui=make(async path=>path==="/api/draft"?gate:baseRoute(path));await selectFirst(ui);ui.elements["draft-title"].value="A";ui.elements["draft-body"].value="A";const running=ui.click("draft");await new Promise(r=>setImmediate(r));ui.nav[2].onclick();release(op);await running;result={pageTitle:ui.elements["page-title"].textContent};
 }else if(scenario==="invalid_fields_upload"){
  const ui=make(baseRoute);await ui.click("accounts");ui.elements["publish-account"].value=account.id;ui.elements["publish-record"].value="recFixtureA";ui.elements["publish-media"].files=[file];ui.elements["publish-fields"].value="{invalid";await ui.click("prepare-publish");result={mediaUploads:ui.calls.filter(x=>x.path==="/api/media").length};
 }else{throw new Error("unknown audit scenario");}
 console.log(JSON.stringify(result));
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@unittest.skipUnless(NODE, "Node runtime required for synthetic frontend audit")
class FrontendAuditTests(unittest.TestCase):
    def scenario(self, name):
        completed = subprocess.run([NODE, "-e", HARNESS, str(ROOT / "contentbase/web/app.js"), str(ROOT / "contentbase/web/index.html"), name],
                                   capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_lost_response_reuses_original_request_key(self):
        result = self.scenario("lost_response")
        self.assertEqual(len(result["keys"]), 2)
        self.assertEqual(result["keys"][0], result["keys"][1], "retry changed the key despite identical intent and lost response")

    def test_lost_publish_response_reuses_key_and_uploaded_media(self):
        result = self.scenario("lost_publish_response")
        self.assertEqual(len(result["requests"]), 2)
        self.assertEqual(result["requests"][0]["key"], result["requests"][1]["key"])
        self.assertEqual(result["requests"][0]["media_ids"], result["requests"][1]["media_ids"])
        self.assertEqual(result["uploads"], 1, "lost preparation response caused browser media to be registered again")

    def test_switching_records_preserves_unsaved_draft(self):
        self.assertEqual(self.scenario("record_draft")["body"], "unsaved A")

    def test_refresh_restores_unsaved_draft(self):
        self.assertEqual(self.scenario("refresh_draft")["body"], "unsaved A")

    def test_new_connection_hides_previous_private_material(self):
        result = self.scenario("credential_change")
        self.assertEqual(result["draft"], "")
        self.assertEqual(result["record"], "")
        self.assertEqual(result["records"], 0)
        self.assertNotIn("private", result["choices"])
        self.assertNotIn("private", result["evidence"])

    def test_cancel_record_switch_keeps_current_draft(self):
        result = self.scenario("cancel_record_switch")
        self.assertEqual(result["record"], "recFixtureA")
        self.assertEqual(result["body"], "unsaved A")

    def test_same_token_identity_change_does_not_submit_previous_draft(self):
        result = self.scenario("same_token_identity_change")
        self.assertEqual(result["draftCalls"], 0)
        self.assertEqual(result["body"], "")
        self.assertEqual(result["records"], 0)

    def test_changed_intent_gets_a_different_request_key(self):
        result = self.scenario("changed_intent")
        self.assertEqual(len(result["keys"]), 2)
        self.assertNotEqual(result["keys"][0], result["keys"][1])

    def test_dynamic_selection_binds_snapshot_and_stable_object_id(self):
        fields = self.scenario("dynamic_choice_receipt")["fields"]
        self.assertEqual(fields["music_choice_id"], "fixture_hash_a")
        self.assertEqual(fields["music_choice_receipt"], "receipt_fixture")
        self.assertNotIn("music_choice", fields)

    def test_editor_reads_full_record_and_supports_text_objects(self):
        result = self.scenario("full_record_dict_text")
        self.assertGreaterEqual(result["fullCalls"], 1)
        self.assertEqual(result["title"], "FULL TITLE")
        self.assertEqual(result["body"], "FULL BODY")
        self.assertEqual(result["posted"]["expected_source"], result["expected"])

    def test_unknown_structured_segment_is_not_silently_used_as_body(self):
        result = self.scenario("strict_text_segments")
        self.assertEqual(result["body"], "")
        self.assertEqual(result["calls"], 0)
        self.assertNotIn("[object Object]", result["body"])

    def test_changed_source_keeps_draft_and_requires_manual_rebase(self):
        result = self.scenario("source_rebase")
        self.assertEqual(result["kept"], "LOCAL DRAFT")
        self.assertIn("LATEST SERVER ORIGINAL", result["latest"])
        self.assertEqual(result["blockedCalls"], 1)
        self.assertEqual(len(result["requests"]), 2)
        self.assertEqual(result["finalBody"], "LOCAL DRAFT")
        self.assertEqual(result["requests"][1]["expected_source"], result["expected"])
        self.assertNotEqual(result["requests"][0]["key"], result["requests"][1]["key"])

    def test_pinned_operator_errors_clear_private_material(self):
        for scenario in ("workspace_pin_change", "workspace_pin_ambiguous"):
            with self.subTest(scenario=scenario):
                result = self.scenario(scenario)
                self.assertEqual(result["calls"], 0)
                self.assertEqual(result["body"], "")
                self.assertEqual(result["record"], "")
                self.assertEqual(result["records"], 0)
                self.assertIn("workspace_operator_", result["notice"])

    def test_applied_plan_updates_source_baseline_when_editor_matches(self):
        result = self.scenario("apply_baseline")
        self.assertEqual(result["body"], "SUBMITTED BODY")
        self.assertTrue(result["rebaseHidden"])
        self.assertIn(result["expected"], result["source"])

    def test_edit_during_apply_is_preserved_and_not_marked_saved(self):
        result = self.scenario("apply_mid_edit")
        self.assertEqual(result["body"], "EDITED DURING APPLY")
        self.assertFalse(result["rebaseHidden"])

    def test_successful_source_selection_does_not_leave_pending_notice(self):
        result = self.scenario("source_success_notice")
        self.assertTrue(result["enabled"])
        self.assertNotIn("正在执行", result["notice"])
        self.assertIn("完整原记录", result["notice"])

    def test_completed_history_is_collapsed_but_review_body_stays_visible(self):
        cards = self.scenario("history_fold")["cards"]
        for card in cards[:2]:
            self.assertEqual(card["directPreviews"], [])
            self.assertFalse(card["details"][0]["open"])
            self.assertIn('"id"', card["details"][0]["raw"])
        self.assertIn("COMPLETE REVIEW BODY", cards[2]["directPreviews"][0])
        self.assertTrue(cards[3]["directPreviews"])
        for card in cards:
            self.assertFalse(card["details"][0]["open"])

    def test_unknown_write_has_readonly_reconciliation_action(self):
        labels = self.scenario("unknown_recovery")["labels"]
        self.assertTrue(any("核验" in label or "verify-write" in label for label in labels), labels)

    def test_long_operation_does_not_lock_navigation(self):
        self.assertFalse(self.scenario("long_navigation")["navigationDisabled"])

    def test_long_operation_completion_does_not_steal_navigation_focus(self):
        self.assertEqual(self.scenario("long_navigation_focus")["pageTitle"], "发布之前，先看清这一次。")

    def test_invalid_platform_json_is_checked_before_media_upload(self):
        self.assertEqual(self.scenario("invalid_fields_upload")["mediaUploads"], 0)


class BackendSurfaceAuditTests(unittest.TestCase):
    def test_earlier_pending_operation_is_not_hidden_by_unrelated_config(self):
        identity = {"open_id": "ou_fixture", "tenant_key": "tenant_fixture"}
        class Identity:
            def identity(self):
                return identity
        with tempfile.TemporaryDirectory(prefix="contentbase-audit-") as directory:
            service = Service({"feishu": {"profile": "fixture", "base_token": "fixture", "table_id": "tblFixture"}, "yxer": {}},
                              state=directory, feishu=Identity(), yxer=type("Provider", (), {"provider_binding": lambda self, *args, **kwargs: {"provider_hash": "fixture_provider"}})())
            data = {"binding_hash": service.binding_hash(), "plan": {"identity": identity}}
            pending, _ = service.store.create("draft", "pending:fixture", {}, data, "unknown")
            for index in range(201):
                other = {"binding_hash": "another-configuration", "plan": {"identity": identity}}
                service.store.create("draft", "other:fixture:" + str(index), {"index": index}, other, "applied")
            shown = service.inspect()
            self.assertIn(pending["id"], [row["id"] for row in shown], "limit applied before identity/config filtering hides pending work")

    def test_earlier_active_operation_is_not_hidden_by_completed_history(self):
        identity = {"open_id": "ou_fixture", "tenant_key": "tenant_fixture"}
        class Identity:
            def identity(self):
                return identity
        with tempfile.TemporaryDirectory(prefix="contentbase-audit-") as directory:
            service = Service({"feishu": {"profile": "fixture", "base_token": "fixture", "table_id": "tblFixture"}, "yxer": {}},
                              state=directory, feishu=Identity(), yxer=type("Provider", (), {"provider_binding": lambda self, *args, **kwargs: {"provider_hash": "fixture_provider"}})())
            data = {"binding_hash": service.binding_hash(), "plan": {"identity": identity}}
            pending, _ = service.store.create("draft", "active:fixture", {}, data, "unknown")
            for index in range(201):
                service.store.create("draft", "complete:fixture:" + str(index), {"index": index}, data, "applied")
            self.assertIn(pending["id"], [row["id"] for row in service.inspect()], "active operation vanished behind completed history")

    def test_displayed_dynamic_index_does_not_select_reordered_object(self):
        first = {"yixiaoerId": "fixture_music_a", "name": "synthetic A"}
        second = {"yixiaoerId": "fixture_music_b", "name": "synthetic B"}
        class Identity:
            def identity(self):
                return {"open_id": "ou_fixture", "tenant_key": "tenant_fixture"}
        class DynamicChoices:
            count = 0
            def provider_binding(self, *args, **kwargs):
                return {"provider_hash": "fixture_provider"}
            def choices(self, *args):
                self.count += 1
                return [first, second] if self.count == 1 else [second, first]
        with tempfile.TemporaryDirectory(prefix="contentbase-choice-audit-") as directory:
            service = Service({"feishu": {"profile": "fixture", "base_token": "fixture", "table_id": "tblFixture"}, "yxer": {}},
                              state=directory, feishu=Identity(), yxer=DynamicChoices())
            displayed = service.query_choices("account_fixture", "imageText", "music", "fixture query")
            for selector in ({"music_choice": 0}, {"music_choice_id": displayed["options"][0]["id"]}):
                with self.subTest(selector=selector):
                    fields = {"music_query": "fixture query", "music_choice_receipt": displayed["receipt_id"], **selector}
                    prepared = service._dynamic_fields("account_fixture", "imageText", fields)
                    self.assertEqual(prepared["music"], displayed["options"][0]["value"], "second vendor query silently reinterprets the user's snapshot")


if __name__ == "__main__":
    unittest.main()
