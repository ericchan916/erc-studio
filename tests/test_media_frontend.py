"""Synthetic DOM/media-byte regression, without real vendors or real publication."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
import test_frontend_audit as frontend_fixture

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

HARNESS = frontend_fixture.HARNESS
HARNESS = HARNESS.replace('const scenario=process.argv[3];', '''const scenario=process.argv[3];
let decodeFailure=false;
const createdUrls=[],revokedUrls=[];
class PreviewURL extends URL { static createObjectURL(blob){const value="blob:synthetic-"+(createdUrls.length+1);createdUrls.push(value);return value;} static revokeObjectURL(value){revokedUrls.push(value);} }
''')
HARNESS = HARNESS.replace('this.files=[];this.dataset={};', '''this.files=[];this.naturalWidth=1;this.readyState=2;this.isConnected=true;this.dataset={};Object.defineProperty(this,"src",{get:()=>this._src||"",set:value=>{this._src=value;if(value)Promise.resolve().then(()=>{if(decodeFailure){this.onerror?.();return;}if(this.tagName==="VIDEO")this.onloadeddata?.();else if(this.tagName==="IMG")this.onload?.();});}});''')
HARNESS = HARNESS.replace(' focus(){} scrollIntoView(){}', ' pause(){} load(){} focus(){} scrollIntoView(){}')
HARNESS = HARNESS.replace('URL,Uint8Array,TextEncoder', 'URL:PreviewURL,Blob,Uint8Array,TextEncoder')
HARNESS = HARNESS.replace('calls.push({path,body,authorization:opts.headers?.Authorization});', 'calls.push({path,body,authorization:opts.headers?.Authorization,headers:opts.headers});')
HARNESS = HARNESS.replace('return value?.__error?', '''if(value?.__binary){const bytes=Buffer.from(value.bytes);return {ok:true,status:200,headers:{get:key=>key.toLowerCase()==="content-type"?value.mime:null},arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)};}return value?.__error?''')
MEDIA_SCENARIOS = r'''
async function mediaScenario(name){
 const ids=["op_"+"a".repeat(32),"op_"+"b".repeat(32)];
 const confirmations=["c".repeat(64),"d".repeat(64)],mediaDigests=["e".repeat(64),"f".repeat(64)];
 const hashes=bytes=>require("crypto").createHash("sha256").update(Buffer.from(bytes)).digest("hex");
 const imageBytes=[137,80,78,71,13,10,26,10],coverBytes=[137,80,78,71,11,10,26,10],videoBytes=[0,0,0,16,102,116,121,112];
 const makeReview=(id,pos,kind="imageText")=>({operation_id:id,confirmation_digest:confirmations[pos],media_review_digest:mediaDigests[pos],title:"synthetic review "+pos,body:"FULL FROZEN BODY "+pos,platform:"小红书",account_id:"SYNTHETIC_ACCOUNT",kind,channel:"cloud",expires_at:Date.now()/1000+1800,media:kind==="video"?[{index:0,name:"clip.mp4",usage:"video",size:videoBytes.length,sha256:hashes(videoBytes),mime:"video/mp4",preview_url:"/api/operations/"+id+"/media/0"},{index:1,name:"cover.png",usage:"cover",size:coverBytes.length,sha256:hashes(coverBytes),mime:"image/png",preview_url:"/api/operations/"+id+"/media/1"}]:[0,1].map(index=>({index,name:index+".png",usage:"image",size:imageBytes.length,sha256:hashes(imageBytes),mime:"image/png",preview_url:"/api/operations/"+id+"/media/"+index}))});
 const packetA=makeReview(ids[0],0,name==="media_video"||name==="media_large"?"video":"imageText"),packetB=makeReview(ids[1],1);
 if(name==="media_large")packetA.media[0].size=20*1024*1024+1;
 if(name==="media_cross_origin")packetA.media[0].preview_url="https://example.invalid/asset.png";
 const opAt=pos=>({id:ids[pos],kind:"publish",state:"ready",created_at:1,confirmation_digest:confirmations[pos],data:{payload:{publishArgs:{accountForms:[{contentPublishForm:{title:"synthetic title",description:"full body"}}]}}}});
 let jobs=[opAt(0)],exports=0,submits=0,pageCalls=0,release;
 const gate=new Promise(resolve=>release=resolve);
 if(name==="media_other_operation"||name==="media_preflight_race")jobs.push(opAt(1));
 const ui=make(async(path,body)=>{
  if(path.startsWith("/api/operation-page")){pageCalls++;if(name==="media_preflight_race"&&pageCalls===3)await gate;return {items:jobs,next_offset:null,has_more:false,identity_fingerprint:"scope_fixture"};}
  if(path.endsWith("/review-export")){exports++;return {operation_id:ids[0],media_review_digest:mediaDigests[0],directory:"/private/synthetic-review",index_path:"/private/synthetic-review/index.html",manifest_path:"/private/synthetic-review/review.json",manifest_digest:"a".repeat(64)};}
  if(path.endsWith("/review")){if(name==="media_other_operation"&&path.includes(ids[0]))await gate;return path.includes(ids[0])?packetA:packetB;}
  if(path.includes("/media/")){
   if(name==="media_scope_delay"||name==="media_export_without_web")await gate;
   const index=Number(path.split("/").at(-1)),packet=path.includes(ids[0])?packetA:packetB,item=packet.media[index];
   let bytes=item.usage==="video"?videoBytes:item.usage==="cover"?coverBytes:imageBytes;
   if(name==="media_tamper"&&index===1)bytes=[...imageBytes.slice(0,-1),11];
   return {__binary:true,bytes,mime:item.mime};
  }
  if(path==="/api/publish/submit"){submits++;jobs=[{...opAt(0),state:"submitted"}];return {...opAt(0),state:"submitted"};}
  return baseRoute(path);
 });
 ui.input("token","SYNTHETIC_MEDIA_ACCESS");await ui.click("reload-operations");
 const panels=()=>ui.elements["operation-list"].children.flatMap(article=>article.children.filter(node=>node.className==="media-review-panel"));
 const parts=panel=>({load:panel.querySelectorAll("button").find(button=>button.textContent.includes("加载冻结")),cancel:panel.querySelectorAll("button").find(button=>button.textContent.includes("取消素材")),exportButton:panel.querySelectorAll("button").find(button=>button.textContent.includes("导出完整")),submit:panel.querySelectorAll("button").find(button=>button.textContent.includes("正式发布")),ack:panel.children.find(node=>node.tagName==="LABEL").children.find(node=>node.tagName==="INPUT"),status:panel.children.find(node=>node.className==="review-status"),media:panel.children.find(node=>node.className==="review-media")});
 const firstPanel=panels()[0],first=parts(firstPanel);
 if(name==="media_initial")return {disabled:first.submit.disabled,checked:!!first.ack.checked,mediaCalls:ui.calls.filter(call=>call.path.includes("/media/")).length};
 if(name==="media_export_without_web"){
  const pending=first.load.onclick();await new Promise(resolve=>setImmediate(resolve));await first.exportButton.onclick();const exported={disabled:first.submit.disabled,checked:!!first.ack.checked,status:first.status.textContent};release();await pending;first.ack.checked=true;first.ack.onchange();return {exported,exports,enabledAfterHumanCheck:!first.submit.disabled,created:createdUrls};
 }
 if(name==="media_scope_delay"){
  const pending=first.load.onclick();await new Promise(resolve=>setImmediate(resolve));ui.input("token","OTHER_SYNTHETIC_ACCESS");release();await pending;return {privateCards:ui.elements["operation-list"].children.length,created:createdUrls,revoked:revokedUrls,submits};
 }
 if(name==="media_other_operation"||name==="media_preflight_race"){
  const pending=first.load.onclick();await new Promise(resolve=>setImmediate(resolve));const second=parts(panels()[1]);await second.load.onclick();release();await pending;return {firstMedia:first.media.children.length,firstDisabled:first.submit.disabled,secondMedia:second.media.children.length,secondChecked:!!second.ack.checked,secondStatus:second.status.textContent};
 }
 if(name==="media_decode_failure")decodeFailure=true;
 await first.load.onclick();
 if(name==="media_large"){
  const before={disabled:first.submit.disabled,checked:!!first.ack.checked,videoCalls:ui.calls.filter(call=>call.path===packetA.media[0].preview_url).length};await first.exportButton.onclick();const exported={disabled:first.submit.disabled,checked:!!first.ack.checked,status:first.status.textContent};first.ack.checked=true;first.ack.onchange();return {before,exported,enabledAfterHumanCheck:!first.submit.disabled,exports};
 }
 if(name==="media_cancel"){
  const created=[...createdUrls];first.cancel.onclick();return {created,revoked:revokedUrls,disabled:first.submit.disabled,checked:!!first.ack.checked,status:first.status.textContent};
 }
 if(name==="media_tamper"||name==="media_decode_failure"||name==="media_cross_origin")return {disabled:first.submit.disabled,ackDisabled:first.ack.disabled,checked:!!first.ack.checked,status:first.status.textContent,created:createdUrls,revoked:revokedUrls,byteCalls:ui.calls.filter(call=>call.path.includes("/media/")).length};
 const loaded={disabled:first.submit.disabled,checked:!!first.ack.checked,figures:first.media.children.map(figure=>({caption:figure.children[1].textContent,tag:figure.children[0].children[0]?.tagName,src:figure.children[0].children[0]?.src})),status:first.status.textContent};
 if(name==="media_submit"){
  first.ack.checked=true;first.ack.onchange();await first.submit.onclick();const sent=ui.calls.find(call=>call.path==="/api/publish/submit");return {loaded,submits,request:sent?.body,revoked:revokedUrls};
 }
 return {loaded,byteCalls:ui.calls.filter(call=>call.path.includes("/media/")).map(call=>({path:call.path,authorized:call.authorization==="Bearer SYNTHETIC_MEDIA_ACCESS",scope:call.headers["X-Contentbase-Identity"],tokenInUrl:call.path.includes("SYNTHETIC_MEDIA_ACCESS")}))};
}
'''
HARNESS = HARNESS.replace('(async()=>{', MEDIA_SCENARIOS + '\n(async()=>{')
HARNESS = HARNESS.replace(' if(scenario==="lost_response"){', ' if(scenario.startsWith("media_")){result=await mediaScenario(scenario);}\n else if(scenario==="lost_response"){')


@unittest.skipUnless(NODE, "Node runtime required for synthetic media DOM audit")
class MediaFrontendTests(unittest.TestCase):
    def scenario(self, name):
        result = subprocess.run([NODE, "-e", HARNESS, str(ROOT / "contentbase/web/app.js"), str(ROOT / "contentbase/web/index.html"), name],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_metadata_alone_does_not_enable_publish(self):
        result = self.scenario("media_initial")
        self.assertTrue(result["disabled"])
        self.assertFalse(result["checked"])
        self.assertEqual(result["mediaCalls"], 0)

    def test_images_load_in_original_order_with_authenticated_bytes(self):
        result = self.scenario("media_images")
        self.assertTrue(result["loaded"]["disabled"])
        self.assertFalse(result["loaded"]["checked"])
        self.assertEqual([figure["tag"] for figure in result["loaded"]["figures"]], ["IMG", "IMG"])
        self.assertIn("1 · 图片 · 0.png", result["loaded"]["figures"][0]["caption"])
        self.assertIn("2 · 图片 · 1.png", result["loaded"]["figures"][1]["caption"])
        self.assertTrue(all(call["authorized"] and call["scope"] and not call["tokenInUrl"] for call in result["byteCalls"]))

    def test_video_and_independent_cover_are_both_visible(self):
        result = self.scenario("media_video")
        self.assertEqual([figure["tag"] for figure in result["loaded"]["figures"]], ["VIDEO", "IMG"])
        self.assertIn("独立封面", result["loaded"]["figures"][1]["caption"])
        self.assertFalse(result["loaded"]["checked"])

    def test_tamper_revokes_partial_urls_and_keeps_publish_disabled(self):
        result = self.scenario("media_tamper")
        self.assertTrue(result["disabled"])
        self.assertTrue(result["ackDisabled"])
        self.assertFalse(result["checked"])
        self.assertIn("frozen_media_hash_mismatch", result["status"])
        self.assertEqual(result["created"], result["revoked"])

    def test_decode_failure_does_not_count_as_successful_preview(self):
        result = self.scenario("media_decode_failure")
        self.assertTrue(result["disabled"])
        self.assertTrue(result["ackDisabled"])
        self.assertIn("frozen_media_preview_failed", result["status"])

    def test_cancellation_revokes_blob_urls_and_human_ack(self):
        result = self.scenario("media_cancel")
        self.assertEqual(result["created"], result["revoked"])
        self.assertTrue(result["disabled"])
        self.assertFalse(result["checked"])

    def test_scope_change_discards_delayed_media_response(self):
        result = self.scenario("media_scope_delay")
        self.assertEqual(result["privateCards"], 0)
        self.assertEqual(result["created"], [])
        self.assertEqual(result["submits"], 0)

    def test_later_operation_does_not_receive_old_review_response(self):
        result = self.scenario("media_other_operation")
        self.assertEqual(result["firstMedia"], 0)
        self.assertTrue(result["firstDisabled"])
        self.assertEqual(result["secondMedia"], 2)
        self.assertFalse(result["secondChecked"])
        self.assertIn("全部冻结字节", result["secondStatus"])

    def test_late_first_preflight_cannot_cancel_the_newer_operation(self):
        result = self.scenario("media_preflight_race")
        self.assertEqual(result["firstMedia"], 0)
        self.assertTrue(result["firstDisabled"])
        self.assertEqual(result["secondMedia"], 2)
        self.assertIn("全部冻结字节", result["secondStatus"])

    def test_large_video_requires_full_package_and_explicit_human_check(self):
        result = self.scenario("media_large")
        self.assertTrue(result["before"]["disabled"])
        self.assertEqual(result["before"]["videoCalls"], 0)
        self.assertEqual(result["exports"], 1)
        self.assertTrue(result["exported"]["disabled"])
        self.assertFalse(result["exported"]["checked"])
        self.assertIn("未标记已审", result["exported"]["status"])
        self.assertTrue(result["enabledAfterHumanCheck"])

    def test_full_local_package_does_not_require_browser_media_to_finish(self):
        result = self.scenario("media_export_without_web")
        self.assertEqual(result["exports"], 1)
        self.assertEqual(result["created"], [])
        self.assertTrue(result["exported"]["disabled"])
        self.assertFalse(result["exported"]["checked"])
        self.assertIn("未标记已审", result["exported"]["status"])
        self.assertTrue(result["enabledAfterHumanCheck"])

    def test_cross_origin_preview_is_rejected_before_bearer_fetch(self):
        result = self.scenario("media_cross_origin")
        self.assertTrue(result["disabled"])
        self.assertEqual(result["byteCalls"], 0)
        self.assertIn("media_review_manifest_invalid", result["status"])

    def test_submit_carries_exact_media_digest_only_after_explicit_check(self):
        result = self.scenario("media_submit")
        self.assertTrue(result["loaded"]["disabled"])
        self.assertEqual(result["submits"], 1)
        self.assertEqual(result["request"]["reviewed_media_digest"], "e" * 64)
        self.assertEqual(result["request"]["digest"], "c" * 64)
        self.assertTrue(result["request"]["approved"])
        self.assertTrue(result["revoked"])


if __name__ == "__main__":
    unittest.main()
