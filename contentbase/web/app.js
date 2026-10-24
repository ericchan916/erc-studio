"use strict";
const $ = id => document.getElementById(id);
const STORAGE_PREFIX = "contentbase-ui:";
const titles = {content:"把内容工作，连成一条链。",operations:"每一个决定，都绑定一个版本。",publishing:"发布之前，先看清这一次。",setup:"自己的账号，自己的业务事实。"};
let records = [], mapping = {}, accounts = [], busy = false, selectedRecord = "";
let fingerprint = null, providerFingerprint = null, contextReady = false, generation = 0, activeTab = "content";
let state = freshState(), choices = {}, queueOffset = 0, queueHasMore = false, userNavigation = 0;
const mediaReviews = new Map();
function freshState() { return {drafts:{}, publication:{}, requests:{}, selected:"", tab:"content"}; }
function getStored(key) { try { return sessionStorage.getItem(key); } catch (_) { return null; } }
function putStored(key, value) { try { sessionStorage.setItem(key, value); return true; } catch (_) { return false; } }
function removeStored(key) { try { sessionStorage.removeItem(key); } catch (_) {} }
function message(value, error = false) { $("notice").textContent = value; $("notice").classList.toggle("error", error); }
function canonical(value) { if (Array.isArray(value)) return value.map(canonical); if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])); return value; }
function signature(value) { return JSON.stringify(canonical(value)); }
function newKey() { return "web-" + crypto.randomUUID(); }
function saveState() { if (contextReady && fingerprint && !putStored(STORAGE_PREFIX + fingerprint, JSON.stringify(state))) message("当前标签页存储不可用；草稿只留在内存，请先保存文稿。", true); }
function cell(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (!Array.isArray(value) && typeof value === "object" && typeof value.text === "string") return value.text;
  if (Array.isArray(value)) return value.map(part => { if (typeof part === "string") return part; if (part && typeof part === "object" && typeof part.text === "string") return part.text; throw new ApiError("feishu_text_representation_unsupported"); }).join("");
  throw new ApiError("feishu_text_representation_unsupported");
}
function hasUnsaved() { const draft = state.drafts[selectedRecord]; return !!draft && (draft.sourceChanged || draft.title !== draft.sourceTitle || draft.body !== draft.sourceBody); }
function clearPrivate(purge = true) {
  disposeMediaReviews();
  const previous = fingerprint || getStored("contentbase-context"); if (purge && previous) removeStored(STORAGE_PREFIX + previous);
  records = []; mapping = {}; accounts = []; selectedRecord = ""; choices = {}; state = freshState();
  fingerprint = null; providerFingerprint = null; contextReady = false; generation += 1; queueOffset = 0; queueHasMore = false;
  removeStored("contentbase-context");
  for (const id of ["record-id","draft-title","draft-body","publish-record","published-url","review-metrics","review-body"]) $(id).value = "";
  $("publish-fields").value = "{}"; $("publish-media").value = ""; $("publish-cover").value = "";
  $("records").replaceChildren(); $("records").textContent = "尚未读取当前本人空间。";
  $("publish-account").replaceChildren(); const empty = document.createElement("option"); empty.value = ""; empty.textContent = "先读取本人账号"; $("publish-account").append(empty);
  $("choices").textContent = "动态字段先查询，再选择具体对象。"; $("choice-options").replaceChildren();
  $("operation-list").replaceChildren(); $("operation-list").textContent = "尚未读取当前本人的技术队列。";
  $("source-original").textContent = "尚未读取完整原记录。"; $("source-state").textContent = "来源未核验"; $("rebase-source").classList.add("hidden");
  $("doctor-result").textContent = "环境尚未检查。"; $("connection-state").textContent = "待连接";
  $("more-operations").classList.add("hidden");
}
function acceptContext(value) {
  if (typeof value !== "string" || !value) throw new Error("identity_fingerprint_required");
  const previous = fingerprint || getStored("contentbase-context");
  const changed = !!previous && previous !== value;
  if (changed) { if (previous) removeStored(STORAGE_PREFIX + previous); clearPrivate(false); }
  if (!contextReady || fingerprint !== value) {
    fingerprint = value; contextReady = true;
    try { const cached = JSON.parse(getStored(STORAGE_PREFIX + value) || "null"); state = cached && cached.drafts && cached.requests && cached.publication ? cached : freshState(); } catch (_) { state = freshState(); }
    putStored("contentbase-context", value);
  }
  if (changed) message("本人身份或绑定来源发生变化；已清理上一空间的稿件、账号、选项和证据。", true);
  return changed;
}
class ApiError extends Error { constructor(code, unknown = false, details = {}) { super(code); this.code = code; this.unknown = unknown; this.details = details; } }
async function api(path, body) {
  const token = $("token").value.trim(), epoch = generation;
  const headers = {"Content-Type":"application/json", "Authorization":"Bearer " + token};
  if (contextReady && fingerprint) headers["X-Contentbase-Identity"] = fingerprint;
  if (providerFingerprint) headers["X-Contentbase-Provider"] = providerFingerprint;
  let response, packet;
  try { response = await fetch(path, {method:body ? "POST" : "GET", headers, body:body ? JSON.stringify(body) : undefined}); } catch (_) { throw new ApiError("local_request_transport_failed", !!body && !["/api/doctor","/api/sync","/api/choices"].includes(path)); }
  try { packet = await response.json(); } catch (_) { throw new ApiError("local_response_invalid", !!body); }
  if (epoch !== generation || token !== $("token").value.trim()) throw new ApiError("connection_changed", true);
  if (!response.ok || !packet.ok) {
    if (["client_identity_changed","identity_context_changed","workspace_operator_changed","workspace_operator_ambiguous","operator_or_configuration_changed","feishu_identity_changed"].includes(packet.error?.code)) clearPrivate(true);
    if (["client_provider_changed","publish_provider_changed","provider_binding_changed","yxer_provider_changed"].includes(packet.error?.code)) clearProvider();
    throw new ApiError(packet.error?.code || "request_failed", !!packet.error?.unknown, packet.error?.details || {});
  }
  if (packet.data?.identity_fingerprint) acceptContext(packet.data.identity_fingerprint);
  return packet.data;
}
async function page(offset = 0, limit = 100) {
  const result = await api("/api/operation-page?limit=" + limit + "&offset=" + offset);
  if (!result || !Array.isArray(result.items) || typeof result.has_more !== "boolean") throw new Error("operation_page_invalid");
  acceptContext(result.identity_fingerprint); return result;
}
async function ensureContext() {
  const previous = fingerprint, result = await page(0, 1);
  if (previous && previous !== result.identity_fingerprint) throw new ApiError("identity_context_changed");
}
function controls() {
  document.querySelectorAll("button").forEach(button => { button.disabled = busy && !button.dataset.tab; });
  const draft = state.drafts[selectedRecord]; $("draft").disabled = busy || !draft?.sourceFingerprint || !!draft.sourceChanged;
  $("reload-source").disabled = busy || !selectedRecord; $("rebase-source").disabled = busy || !draft?.sourceChanged;
  for (const review of mediaReviews.values()) reviewControls(review);
}
function errorText(error) {
  const descriptions = {
    media_stream_busy:"本机媒体读取正在使用两个快照槽；稍后重试读取，不重建或重发发布任务。", media_snapshot_unavailable:"冻结快照未建立；保留原任务，检查磁盘空间和私有目录权限后重试读取。", media_bytes_limit_use_stream:"此素材超过字节读取上限；请使用完整本机审核包或受控媒体流，不重新上传。",
    explicit_media_review_required:"先加载并核验实际冻结素材，或导出完整审核包由本人观看，再明确勾选。", media_review_manifest_invalid:"冻结审核清单与当前任务不一致；停止确认，核验原任务。", frozen_media_hash_mismatch:"冻结素材字节与摘要不一致；已撤销预览和本人确认，停止投递。", frozen_media_size_mismatch:"素材长度与冻结清单不一致；停止投递。", frozen_media_type_mismatch:"素材类型与冻结清单不一致；停止投递。", frozen_media_preview_failed:"浏览器尚未成功显示实际素材；不算已审，可使用完整本机审核包观看。", frozen_media_fetch_failed:"实际冻结素材未成功读取；确认保持关闭，先核验原任务。", media_review_hash_unavailable:"浏览器缺少字节摘要能力；使用完整本机审核包，不用 metadata 冒充已审。",
    authentication_required:"本机访问令牌未通过；读取当前 web-access.json 后重新连接。",
    client_identity_required:"先连接本人空间并读取当前身份指纹。", identity_context_changed:"本人身份或来源已变化；旧材料已清理，重新连接并读取。",
    workspace_operator_changed:"这个 state 固定原本人，当前 Profile 已换人；旧私有显示已清理。新本人需独立 state / 配置与自己的供应商授权，不删除旧数据。", workspace_operator_ambiguous:"旧 state 混有多个身份，先核验和保留原数据；新本人使用独立 state，不借用旧授权。",
    full_record_source_required:"先选择记录并完整读取原记录，列表预览或旧缓存不是编辑依据。", source_rebase_required:"来源已经变化；先核对最新原文，再由本人点重新建立计划。", feishu_text_representation_unsupported:"正文结构尚未适配；停止当正文使用，不把对象 repr 或 [object Object] 写入文稿。",
    client_identity_changed:"真实本人身份已变化；旧材料已清理，请重新连接。", client_provider_required:"先读取当前蚁小二账号，再执行这次准备或查询。",
    client_provider_changed:"蚁小二提供方已变化；旧账号与选项已清理，重新读取账号。", publish_provider_changed:"原任务的蚁小二提供方变化；回到原提供方核验，不重新投递。",
    connection_changed:"连接发生变化，旧响应未显示；在原本人空间核验已有任务。",
    dynamic_choice_receipt_required:"动态选择缺少有效查询快照；重新查询并选择具体对象。", vendor_choice_receipt_required:"动态选择必须携带原查询 receipt；重新查询并选择。",
    vendor_choice_receipt_stale_or_mismatched:"配乐或分区查询快照过期或不匹配；在当前账号重新查询。", vendor_choice_changed_refresh_required:"原动态对象已变化；重新查询和本人核对，不替换成另一个索引。",
    publish_preflight_expired:"发布快照超过 30 分钟；先查看原任务，不自动换键重跑。", confirmation_changed:"批准摘要与冻结版本不一致；重新查看原任务。",
    feishu_record_changed:"飞书记录已变化；保留文稿，核对新来源后生成明确新版本。", feishu_source_changed:"发布来源已变化；停止使用旧批准，核对新来源。", feishu_schema_changed:"飞书字段已变化；检查映射和当前字段类型。",
    feishu_field_mapping_missing:"映射字段在当前表中缺失；本人核对配置，不自动加字段。", feishu_write_field_type_unsupported:"当前字段类型首版尚未适配；本人调整准确映射，保留原字段。",
    feishu_write_value_invalid:"字段值不符合类型或单选项；核对阶段映射和实际选项。", prior_write_requires_verification:"该记录有未知写入；先对原任务 verify-write。",
    prior_publish_requires_verification:"该来源有未知发布；先查询原任务，不能重发。", same_source_already_submitted:"同源已受理；查看原任务与平台结果，不重复投递。",
    task_receipt_missing_no_automatic_replay:"没有可靠 taskSetId；保留原回执，人工核验供应商任务，不重发。", idempotency_conflict:"同一 key 的输入已变化；核对原请求，不用换键绕过未知结果。",
    operation_not_found:"该任务不属于当前本人空间或不存在；核对原配置和身份。", resource_operation_in_progress:"同一记录仍有操作执行中；等待并刷新原任务。",
    yxer_account_unavailable:"当前账号不可操作；本人在供应商核验状态，不切换账号代发。", yxer_music_required:"抖音图文须先查询并选择真实配乐。",
    yxer_bilibili_fields_required:"哔哩哔哩视频须选真实分区并填标签。", yxer_dynamic_field_unsupported:"这个动态字段尚未适配；首版动态查询仅支持配乐和分区。",
    yxer_field_value_invalid:"平台字段格式不符；按当前 schema 核对，上传前修正。", yxer_required_field_missing:"缺少平台必填项；按当前 schema 补齐后本人核对。",
    metric_provenance_required:"指标须有非空平台、作品 ID、观测时间和 values。", metric_timestamp_with_timezone_required:"观测时间须为带时区的 ISO 时间，例如 +08:00 或 Z。",
    metric_values_must_be_finite_or_missing:"指标值只接受有限数值或 null；不填 NaN / Infinity，不把缺失补零。",
    cli_not_installed:"找不到供应商 CLI；按官方或供应商指引安装并核对路径。", cli_validation:"CLI 参数或输入未通过；先看对应 --help，再修正原输入。",
    cli_auth:"本人供应商授权失效或缺失；核验原 Profile，不自动换账号。", cli_permission:"本人缺少目标权限；核对目标对象和所需范围。",
    cli_unsupported:"当前平台、类型或字段未支持；以本机精确契约为准，不走隐藏接口。", cli_rate_limit:"供应商限流；读取建议等待时间，写入结果先核验。",
    cli_provider_unavailable:"供应商或网络暂不可用；保留原任务，外部写入先核验。", cli_execution_unknown:"CLI 写入回执不确定；核验原任务，不认定未执行。",
    cli_transport_failed:"只读调用连接失败；稍后核验原供应商状态。", local_request_transport_failed:"本机 HTTP 回执未收到；保留原请求，先刷新技术队列。",
    local_response_invalid:"本机响应格式不符；保留原任务并核验，不回显响应正文。", invalid_platform_json:"平台字段 JSON 格式错误；修正结构，不上传素材。",
    invalid_metrics_json:"指标 JSON 格式错误；修正结构和实际口径。", request_lookup_incomplete:"技术队列尚未完整核验；保留原请求，不重新上传或创建。"
  };
  const code = error.code || error.message;
  let text = descriptions[code] ? descriptions[code] + "（" + code + "）" : error.message || "request_failed";
  if (typeof error.details?.retry_after === "number") text += " 建议等待 " + error.details.retry_after + " 秒。";
  if (error.unknown) text += " 结果未知：可能已执行，先核验原任务，不换键重跑。";
  return text;
}

async function run(action, verify = true) {
  if (busy) { message("已有操作执行中；可以切页查看，暂不提交另一项业务。", true); return; }
  busy = true; controls(); const pendingNotice = "正在执行；可切页查看，勿重复提交同一项业务。"; message(pendingNotice);
  try { if (verify) await ensureContext(); await action(); if ($("notice").textContent === pendingNotice) message("本次操作已结束；请查看当前结果与技术队列证据。"); } catch (error) { message(errorText(error), true); } finally { busy = false; controls(); }
}
async function readQueueWhileBusy() { try { await loadOperations(); } catch (error) { message(errorText(error), true); } }
function tab(name) {
  activeTab = name;
  document.querySelectorAll(".page").forEach(element => element.classList.toggle("hidden", element.id !== name));
  document.querySelectorAll("nav button").forEach(element => element.classList.toggle("active", element.dataset.tab === name));
  $("page-title").textContent = titles[name]; state.tab = name; saveState();
}
$("token").value = getStored("contentbase-access") || "";
let knownToken = $("token").value.trim();
$("token").oninput = () => {
  const next = $("token").value.trim();
  if (next !== knownToken) { clearPrivate(true); removeStored("contentbase-access"); knownToken = next; message("连接令牌已变化；已清理旧私有显示，请连接当前本人空间。", true); }
};
document.querySelectorAll("nav button").forEach(button => { button.onclick = () => { userNavigation += 1; tab(button.dataset.tab); if (button.dataset.tab === "operations") { if (busy) readQueueWhileBusy(); else run(loadOperations); } }; });
$("connect").onclick = () => run(async () => { await page(0, 1); putStored("contentbase-access", $("token").value.trim()); knownToken = $("token").value.trim(); $("connection-state").textContent = "本人空间已连接"; message("本机令牌与当次本人空间已核验；供应商写入和发布仍需逐层验收。"); }, false);
function saveDraft() {
  const id = selectedRecord || $("record-id").value; if (!id || !contextReady) return;
  const old = state.drafts[id] || {sourceTitle:$("draft-title").value, sourceBody:$("draft-body").value};
  state.drafts[id] = {...old, title:$("draft-title").value, body:$("draft-body").value}; state.selected = id; saveState();
}
for (const id of ["draft-title", "draft-body"]) $(id).oninput = saveDraft;
function renderSource(draft, raw = null) {
  $("source-original").textContent = "最新标题：" + draft.latestSourceTitle + "\n最新正文：\n" + draft.latestSourceBody;
  if (raw && Object.hasOwn(raw.fields, "素材原话")) { try { $("source-original").textContent += "\n\n素材原话：\n" + cell(raw.fields["素材原话"]); } catch (_) { $("source-original").textContent += "\n素材原话结构未适配；未当正文使用。"; } }
  $("source-state").textContent = draft.sourceChanged ? "原来源已变化；稿件保留，须本人核对并重新建立计划。" : "完整原记录已核验；摘要 " + draft.sourceFingerprint;
  $("rebase-source").classList.toggle("hidden", !draft.sourceChanged); controls();
}
async function fetchSource(id) {
  const result = await api("/api/records/" + encodeURIComponent(id)); acceptContext(result.identity_fingerprint);
  if (!result.record || result.record.record_id !== id || typeof result.source_fingerprint !== "string" || !/^[a-f0-9]{64}$/.test(result.source_fingerprint)) throw new ApiError("invalid_source_fingerprint");
  const columns = result.field_map; if (!columns?.title || !columns?.body) throw new Error("feishu_field_mapping_missing");
  return {...result, title:cell(result.record.fields[columns.title]), body:cell(result.record.fields[columns.body])};
}
function setLatest(draft, source) {
  draft.latestSourceFingerprint = source.source_fingerprint; draft.latestSourceTitle = source.title; draft.latestSourceBody = source.body;
  draft.sourceChanged = !draft.sourceFingerprint || draft.sourceFingerprint !== source.source_fingerprint;
}
async function chooseRecord(id, prompt = true) {
  if (selectedRecord && selectedRecord !== id && hasUnsaved() && prompt && !window.confirm("当前记录有未提交稿件。保留本标签页草稿并切换记录？取消会留在当前记录。")) { message("已取消切换；原记录与未提交稿件保持不变。"); return; }
  saveDraft(); const source = await fetchSource(id); mapping = source.field_map; selectedRecord = id; state.selected = id;
  let draft = state.drafts[id];
  if (!draft) draft = {title:source.title, body:source.body, sourceTitle:source.title, sourceBody:source.body, sourceFingerprint:source.source_fingerprint};
  setLatest(draft, source); state.drafts[id] = draft;
  $("record-id").value = id; $("publish-record").value = id; $("draft-title").value = draft.title; $("draft-body").value = draft.body;
  $("records").querySelectorAll("button").forEach(button => button.classList.toggle("selected", button.dataset.recordId === id));
  restorePublication(id); saveState(); renderSource(draft, source.record);
  if (draft.sourceChanged) message("最新完整来源已变化；未提交稿件保持原样，先核对原文，再本人重新建立计划。", true);
  else message("完整原记录已读取并核验；当前稿件按本人空间与记录保留，尚未因此写入飞书。");
}
$("reload-source").onclick = () => run(() => chooseRecord(selectedRecord, false));
$("rebase-source").onclick = () => run(async () => {
  saveDraft(); const id = selectedRecord, draft = state.drafts[id]; if (!draft) throw new ApiError("full_record_source_required");
  const previous = draft.latestSourceFingerprint, source = await fetchSource(id); setLatest(draft, source); renderSource(draft, source.record);
  if (source.source_fingerprint !== previous) { saveState(); message("来源又发生变化；已展示最新原文，请核对后再次点击，不自动换摘要。", true); return; }
  const entry = state.requests["draft:" + id];
  if (entry?.attempted) { const operation = await findRequest(entry); if (operation && ["unknown","applying","preparing","submitting"].includes(operation.state)) { message("原请求仍在执行或结果未知；先核验原任务，不重新建立计划或换 key。", true); return; } }
  if (!window.confirm("已核对下方最新完整原文和保留的稿件。以最新来源摘要重新建立计划？稿件正文不会替换，也不会自动批准或写入。")) { message("已取消重新建立来源；保留原摘要和稿件，未批准或写入。"); return; }
  draft.sourceFingerprint = source.source_fingerprint; draft.sourceTitle = source.title; draft.sourceBody = source.body; draft.sourceChanged = false;
  saveState(); renderSource(draft, source.record); message("本人已核对最新来源；稿件保持原样，下一份写计划绑定最新摘要并重新审核。");
});
$("sync").onclick = () => run(async () => {
  saveDraft(); const result = await api("/api/sync", {}); acceptContext(result.identity_fingerprint);
  records = result.records; mapping = result.field_map; const list = $("records"); list.replaceChildren();
  if (!records.length) list.textContent = "本次实测空表；请本人在飞书建立标题、素材原话和阶段明确的一条记录，再读取。";
  for (const record of records) { const button = document.createElement("button"); button.className = "record"; button.dataset.recordId = record.record_id; const title = document.createElement("strong"); try { title.textContent = cell(record.fields[mapping.title]) || "未命名记录"; } catch (_) { title.textContent = "完整读取后查看标题"; } const id = document.createElement("small"); id.textContent = record.record_id; button.append(title, id); button.onclick = () => run(() => chooseRecord(record.record_id)); list.append(button); }
  const previous = records.find(record => record.record_id === state.selected); if (previous) await chooseRecord(previous.record_id, false);
  controls(); const draft = state.drafts[selectedRecord]; if (!draft?.sourceChanged) message("实时读取 " + records.length + " 条列表；选择时另取完整原记录，旧预览不作为编辑来源。");
});

function requestFor(kind, payload) {
  const slot = kind + ":" + payload.record_id, frozen = signature(payload), previous = state.requests[slot];
  if (previous && previous.signature === frozen) return previous;
  const entry = {key:newKey(), kind, signature:frozen, attempted:false, uncertain:false, media:{}}; state.requests[slot] = entry; saveState(); return entry;
}
async function findRequest(entry) {
  if (entry.operation_id) return api("/api/operations/" + encodeURIComponent(entry.operation_id));
  let offset = 0;
  for (let count = 0; count < 100; count++) { const result = await page(offset); const found = result.items.find(operation => operation.request_key === entry.key && operation.kind === entry.kind); if (found) return found; if (!result.has_more) return null; if (!Number.isInteger(result.next_offset) || result.next_offset <= offset) throw new Error("operation_page_invalid"); offset = result.next_offset; }
  throw new ApiError("request_lookup_incomplete");
}
async function sendStable(path, payload, entry) {
  if (entry.attempted) { const existing = await findRequest(entry); if (existing) { entry.operation_id = existing.id; entry.uncertain = false; saveState(); return existing; } }
  entry.attempted = true; entry.uncertain = true; saveState();
  try { const operation = await api(path, {...payload, key:entry.key}); entry.operation_id = operation.id; entry.uncertain = false; saveState(); return operation; } catch (error) { saveState(); throw error; }
}
async function finishPlan(path, kind, payload) { const navigation = userNavigation, entry = requestFor(kind, payload); const operation = await sendStable(path, payload, entry); if (navigation === userNavigation) tab("operations"); await loadOperations(); return operation; }
function reportPlan(operation) { const descriptions = {prepared:"写计划已准备，尚未写入飞书；请审核冻结版本。",approved:"原计划已批准；应用后才写入飞书。",applied:"原计划已完成并保留核验记录；本次没有新建或重复写入。",unknown:"原计划结果未知，可能已写入；先只读核验，不重新执行。",applying:"原计划仍在执行；等待并刷新原任务。"}; message(descriptions[operation.state] || "已找到原计划，状态为 " + operation.state + "；核验原记录，不自动重跑。", operation.state === "unknown"); }
$("draft").onclick = () => run(async () => {
  saveDraft(); const record_id = $("record-id").value, draft = state.drafts[record_id];
  if (!draft?.sourceFingerprint) throw new ApiError("full_record_source_required"); if (draft.sourceChanged) throw new ApiError("source_rebase_required");
  const payload = {record_id, title:$("draft-title").value, body:$("draft-body").value, expected_source:draft.sourceFingerprint};
  if (!record_id || !payload.title.trim() || !payload.body.trim()) throw new Error("请选择记录并填写完整标题和正文");
  try { reportPlan(await finishPlan("/api/draft", "draft", payload)); } catch (error) { if (error.code === "feishu_source_changed") { await chooseRecord(record_id, false); } throw error; }
});
function markDraftApplied(operation) {
  if (operation.kind !== "draft" || operation.state !== "applied") return;
  const plan = operation.data.plan, draft = state.drafts[plan.record_id], latest = operation.data.readback, hash = operation.data.readback_source_fingerprint;
  if (!draft || !mapping.title || !mapping.body || !latest || !hash) return;
  const source = {source_fingerprint:hash, title:cell(latest.fields[mapping.title]), body:cell(latest.fields[mapping.body])};
  const currentTitle = selectedRecord === plan.record_id ? $("draft-title").value : draft.title, currentBody = selectedRecord === plan.record_id ? $("draft-body").value : draft.body;
  draft.title = currentTitle; draft.body = currentBody; setLatest(draft, source);
  if (currentTitle === cell(plan.fields[mapping.title]) && currentBody === cell(plan.fields[mapping.body])) { draft.sourceFingerprint = hash; draft.sourceTitle = source.title; draft.sourceBody = source.body; draft.sourceChanged = false; }
  saveState(); if (selectedRecord === plan.record_id) renderSource(draft, latest);
}

function reviewCurrent(context) {
  return !context.closed && mediaReviews.get(context.id) === context && context.generation === generation && context.scope === fingerprint && context.token === $("token").value.trim() && context.panel.isConnected !== false;
}
function reviewExpired(context) { return !context.review || !Number.isFinite(context.expires) || Date.now() >= context.expires; }
function reviewComplete(context) { return reviewCurrent(context) && !context.securityFailure && !reviewExpired(context) && (context.previewReady || context.exportReady); }
function reviewControls(context) {
  context.load.disabled = busy || context.loading || context.exporting;
  context.cancel.disabled = context.closed;
  context.exportButton.disabled = busy || !reviewCurrent(context) || !context.review || context.securityFailure || context.exporting || reviewExpired(context);
  context.ack.disabled = busy || !reviewComplete(context);
  context.submit.disabled = busy || !reviewComplete(context) || !context.ack.checked;
}
function revokeReviewUrls(context) {
  for (const node of context.nodes) { try { if (node.tagName === "VIDEO") { node.pause(); node.removeAttribute("src"); node.load(); } else node.removeAttribute("src"); } catch (_) {} }
  for (const url of context.urls) URL.revokeObjectURL(url);
  context.urls.clear(); context.nodes = [];
}
function stopReviewWeb(context) {
  context.webGeneration += 1; context.controller?.abort(); for (const cancel of [...context.decodeCancels]) cancel(); context.controller = null; context.loading = false; context.previewReady = false; revokeReviewUrls(context);
}
function disposeReview(context, text = "已取消素材审核；未确认发布。") {
  context.closed = true; stopReviewWeb(context); clearTimeout(context.timer); context.exportReady = false; context.ack.checked = false; context.media.replaceChildren(); context.status.textContent = text; reviewControls(context);
}
function disposeMediaReviews() { for (const context of mediaReviews.values()) disposeReview(context); mediaReviews.clear(); }
function reviewError(context, error) {
  if (!reviewCurrent(context)) return;
  stopReviewWeb(context); context.ack.checked = false;
  context.securityFailure = ["media_review_manifest_invalid","frozen_media_hash_mismatch","frozen_media_size_mismatch","frozen_media_type_mismatch"].includes(error.code || error.message);
  context.status.textContent = errorText(error); context.status.classList.add("error"); reviewControls(context);
}
function assertReview(context, packet) {
  if (!packet || packet.operation_id !== context.id || packet.confirmation_digest !== context.op.confirmation_digest || !/^[a-f0-9]{64}$/.test(packet.media_review_digest || "") || typeof packet.title !== "string" || typeof packet.body !== "string" || !["imageText","video"].includes(packet.kind) || !["cloud","local"].includes(packet.channel) || typeof packet.account_id !== "string" || typeof packet.platform !== "string" || !Array.isArray(packet.media) || !packet.media.length) throw new ApiError("media_review_manifest_invalid");
  for (let index = 0; index < packet.media.length; index++) {
    const item = packet.media[index], route = "/api/operations/" + encodeURIComponent(context.id) + "/media/" + index;
    if (item.index !== index || !["image","video","cover"].includes(item.usage) || typeof item.name !== "string" || !item.name || /[\\/\u0000-\u001f]/.test(item.name) || !Number.isSafeInteger(item.size) || item.size <= 0 || !/^[a-f0-9]{64}$/.test(item.sha256 || "") || item.preview_url !== route || !["image/png","image/jpeg","image/webp","image/gif","video/mp4","video/quicktime"].includes(item.mime)) throw new ApiError("media_review_manifest_invalid");
    if ((item.usage === "video") !== item.mime.startsWith("video/")) throw new ApiError("media_review_manifest_invalid");
  }
  if (packet.kind === "imageText" && packet.media.some(item => item.usage !== "image") || packet.kind === "video" && (packet.media.length !== 2 || packet.media.filter(item => item.usage === "video").length !== 1 || packet.media.filter(item => item.usage === "cover").length !== 1)) throw new ApiError("media_review_manifest_invalid");
  const expires = typeof packet.expires_at === "number" ? packet.expires_at * 1000 : Date.parse(packet.expires_at);
  if (!Number.isFinite(expires) || expires <= Date.now()) throw new ApiError("publish_preflight_expired");
  context.expires = expires;
}
async function frozenBytes(context, item, webGeneration) {
  const response = await fetch(item.preview_url, {headers:{"Authorization":"Bearer " + context.token,"X-Contentbase-Identity":context.scope,...(providerFingerprint ? {"X-Contentbase-Provider":providerFingerprint} : {})}, signal:context.controller.signal, cache:"no-store"});
  if (!reviewCurrent(context) || context.webGeneration !== webGeneration) throw new ApiError("media_review_cancelled");
  if (!response.ok) { let packet; try { packet = await response.json(); } catch (_) { throw new ApiError("frozen_media_fetch_failed"); } if (["client_identity_changed","workspace_operator_changed","workspace_operator_ambiguous"].includes(packet.error?.code)) clearPrivate(true); throw new ApiError(packet.error?.code || "frozen_media_fetch_failed"); }
  const mime = response.headers.get("Content-Type")?.split(";")[0]; if (mime !== item.mime) throw new ApiError("frozen_media_type_mismatch");
  const bytes = await response.arrayBuffer();
  if (!reviewCurrent(context) || context.webGeneration !== webGeneration) throw new ApiError("media_review_cancelled");
  if (bytes.byteLength !== item.size) throw new ApiError("frozen_media_size_mismatch");
  if (!crypto.subtle) throw new ApiError("media_review_hash_unavailable");
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), value => value.toString(16).padStart(2,"0")).join("");
  if (!reviewCurrent(context) || context.webGeneration !== webGeneration) throw new ApiError("media_review_cancelled");
  if (hash !== item.sha256) throw new ApiError("frozen_media_hash_mismatch"); return bytes;
}
async function decodedPreview(context, item, bytes, slot, webGeneration) {
  const node = document.createElement(item.usage === "video" ? "video" : "img");
  if (item.usage === "video") { node.controls = true; node.preload = "auto"; } else { node.alt = (item.index + 1) + " · " + item.name; }
  const url = URL.createObjectURL(new Blob([bytes], {type:item.mime})); context.urls.add(url); context.nodes.push(node);
  const decoded = new Promise((resolve, reject) => {
    let timer, settled = false;
    const finish = error => { if (settled) return; settled = true; clearTimeout(timer); context.decodeCancels.delete(cancel); node.onload = node.onloadeddata = node.onerror = null; error ? reject(error) : resolve(); };
    const cancel = () => finish(new ApiError("media_review_cancelled")); context.decodeCancels.add(cancel);
    const loaded = () => { if (!reviewCurrent(context) || context.webGeneration !== webGeneration) { finish(new ApiError("media_review_cancelled")); return; } if (item.usage !== "video" && !node.naturalWidth) { finish(new ApiError("frozen_media_preview_failed")); return; } finish(); };
    if (item.usage === "video") node.onloadeddata = loaded; else node.onload = loaded;
    node.onerror = () => finish(new ApiError("frozen_media_preview_failed"));
    timer = setTimeout(() => finish(new ApiError("frozen_media_preview_failed")), 20000); timer?.unref?.();
    node.src = url;
  });
  slot.append(node); await decoded;
  if (!reviewCurrent(context) || context.webGeneration !== webGeneration) throw new ApiError("media_review_cancelled");
}
function newReviewContext(op, panel) {
  const context = {id:op.id,op,panel,generation,scope:fingerprint,token:$("token").value.trim(),urls:new Set(),nodes:[],decodeCancels:new Set(),webGeneration:0,closed:false,loading:false,exporting:false,previewReady:false,exportReady:false,securityFailure:false};
  const heading = document.createElement("h4"); heading.textContent = "冻结素材审核 · 尚未标记本人已审";
  context.status = document.createElement("p"); context.status.className = "review-status"; context.status.textContent = "先加载实际冻结素材；只看 payload / 文件名不代表已审。";
  context.summary = document.createElement("pre"); context.summary.className = "review-caption";
  context.media = document.createElement("div"); context.media.className = "review-media";
  const tools = document.createElement("div"); tools.className = "review-tools";
  context.load = document.createElement("button"); context.load.textContent = "加载冻结素材审核";
  context.cancel = document.createElement("button"); context.cancel.textContent = "取消素材审核";
  context.exportButton = document.createElement("button"); context.exportButton.textContent = "导出完整本机审核包";
  tools.append(context.load,context.cancel,context.exportButton);
  const label = document.createElement("label"); label.className = "review-ack"; context.ack = document.createElement("input"); context.ack.type = "checkbox";
  context.ackText = document.createElement("span"); context.ackText.textContent = "本人已按原顺序完整查看全部素材、视频及独立封面，并核对标题 / 正文 / 账号 / 平台 / 通道。"; label.append(context.ack,context.ackText);
  context.submit = document.createElement("button"); context.submit.textContent = "本人确认正式发布"; context.submit.className = "primary";
  panel.append(heading,context.status,context.summary,context.media,tools,label,context.submit);
  context.ack.onchange = () => { if (!reviewComplete(context)) context.ack.checked = false; reviewControls(context); };
  context.cancel.onclick = () => disposeReview(context);
  context.load.onclick = async () => {
    if (busy || context.loading || context.exporting) return;
    const replacement = mediaReviews.get(context.id) !== context || context.closed;
    if (replacement) { panel.replaceChildren(); const next = newReviewContext(op,panel); mediaReviews.set(op.id,next); await loadMediaReview(next); } else await loadMediaReview(context);
  };
  context.exportButton.onclick = () => exportMediaReview(context);
  context.submit.onclick = () => run(async () => {
    if (!reviewComplete(context) || !context.ack.checked) throw new ApiError("explicit_media_review_required");
    if (!window.confirm("本人已经查看实际冻结素材并核对完整文案、目标账号、平台和通道。现在正式投递这份精确版本？")) { context.ack.checked = false; reviewControls(context); message("已取消正式投递；本人素材确认已撤销。"); return; }
    await api("/api/publish/submit", {operation_id:op.id,digest:context.review.confirmation_digest,reviewed_media_digest:context.review.media_review_digest,approved:true});
    disposeReview(context,"已获得受理回执；仍需查询平台结果并本人核验公开作品。"); await loadOperations(); message("蚁小二已受理；实际公开结果仍需另行核验。");
  });
  reviewControls(context); return context;
}
async function loadMediaReview(context) {
  context.loading = true;
  for (const other of mediaReviews.values()) if (other !== context && !other.closed && (other.loading || other.urls.size || other.review)) disposeReview(other,"已切换到另一份素材审核；本份确认已撤销。");
  reviewControls(context);
  try {
    await ensureContext(); if (!reviewCurrent(context)) return;
    stopReviewWeb(context); context.loading = true; context.exportReady = false; context.securityFailure = false; context.ack.checked = false; context.status.classList.remove("error"); context.media.replaceChildren(); context.status.textContent = "正在读取冻结 manifest 与实际素材；还没有标记已审。"; reviewControls(context);
    const packet = await api("/api/operations/" + encodeURIComponent(context.id) + "/review"); if (!reviewCurrent(context)) return;
    assertReview(context,packet); context.review = packet; context.summary.textContent = "标题：" + packet.title + "\n正文：\n" + packet.body + "\n\n平台：" + packet.platform + " · 账号：" + (packet.account_name || packet.account_id) + "（" + packet.account_id + "）\n类型：" + packet.kind + " · 通道：" + packet.channel;
    clearTimeout(context.timer); context.timer = setTimeout(() => { if (reviewCurrent(context)) disposeReview(context,"审核快照已过期；本人确认撤销，请先核验原任务。"); },context.expires-Date.now()); context.timer?.unref?.();
    context.controller = new AbortController(); const webGeneration = context.webGeneration; let loaded = 0, large = 0;
    reviewControls(context);
    for (const item of packet.media) {
      if (!reviewCurrent(context) || context.webGeneration !== webGeneration) return;
      const figure = document.createElement("figure"), caption = document.createElement("figcaption"), slot = document.createElement("div"); slot.className = "media-slot";
      caption.textContent = (item.index + 1) + " · " + (item.usage === "cover" ? "独立封面" : item.usage === "video" ? "视频" : "图片") + " · " + item.name + " · " + item.size + " bytes · SHA-256 " + item.sha256; figure.append(slot,caption); context.media.append(figure);
      if (item.size > 20*1024*1024) { large += 1; slot.textContent = "此文件超过网页 20 MiB 读取上限；导出完整审核包，在本机完整观看后再明确确认。"; continue; }
      const bytes = await frozenBytes(context,item,webGeneration); await decodedPreview(context,item,bytes,slot,webGeneration); loaded += 1;
      context.status.textContent = "已校验并加载 " + loaded + " / " + packet.media.length + " 项；还需要本人完整查看，不自动标记已审。";
    }
    if (!reviewCurrent(context) || context.webGeneration !== webGeneration) return;
    context.loading = false; context.previewReady = large === 0 && loaded === packet.media.length;
    context.status.textContent = context.previewReady ? "全部冻结字节摘要一致，预览已加载；请本人完整查看视频 / 图片 / 封面后明确勾选。" : "含超限素材；网页未审完。可导出完整本机审核包覆盖全部素材，本人观看后明确勾选。"; reviewControls(context);
  } catch (error) { if (reviewCurrent(context) && error.code !== "media_review_cancelled") reviewError(context,error); else if (["workspace_operator_changed","workspace_operator_ambiguous","client_identity_changed"].includes(error.code)) message(errorText(error),true); }
}
async function exportMediaReview(context) {
  if (!reviewCurrent(context) || !context.review || context.securityFailure || reviewExpired(context) || context.exporting || busy) return;
  stopReviewWeb(context); context.media.replaceChildren(); context.ack.checked = false; context.exportReady = false; context.exporting = true; context.status.textContent = "正在导出全部实际冻结素材；导出不代表本人已观看。"; reviewControls(context);
  try {
    const result = await api("/api/operations/" + encodeURIComponent(context.id) + "/review-export", {});
    if (!reviewCurrent(context)) return;
    if (result.operation_id !== context.id || result.media_review_digest !== context.review.media_review_digest || !/^[a-f0-9]{64}$/.test(result.manifest_digest || "") || typeof result.index_path !== "string" || typeof result.manifest_path !== "string" || typeof result.directory !== "string") throw new ApiError("media_review_manifest_invalid");
    context.exportReady = true; context.status.textContent = "已导出完整审核包（未标记已审）。本人在本机打开：" + result.index_path + "\n清单：" + result.manifest_path + "\n完整观看全部原素材和独立封面，再勾选本人确认。"; context.ackText.textContent = "本人已打开上述完整本机审核包，按原顺序观看全部素材、完整视频及独立封面，并核对全部文案 / 目标 / 通道。";
  } catch (error) { if (reviewCurrent(context)) reviewError(context,error); } finally { context.exporting = false; reviewControls(context); }
}
function operationView(op) {
  const article = document.createElement("article"); article.className = "operation";
  const payload = op.data.payload, plan = op.data.plan, form = payload?.publishArgs?.accountForms?.[0]?.contentPublishForm;
  const kinds = {draft:"草稿",published:"公开登记",review:"复盘",publish:"发布"};
  const statuses = {prepared:"待本人审核",approved:"已批准待应用",ready:"待本人发布确认",unknown:"结果未知",applying:"写入中",submitting:"投递中",applied:"已回读核验",submitted:"已受理 · 公开未验证",failed:"失败待处理",preparation_failed:"准备失败",preparation_interrupted:"准备中断"};
  let businessTitle = form?.title;
  if (!businessTitle && plan) { const fields = plan.fields || {}, before = plan.before?.fields || {}; try { businessTitle = cell(fields[mapping.title] ?? fields["标题"] ?? fields.title ?? before[mapping.title] ?? before["标题"] ?? before.title); } catch (_) {} }
  const header = document.createElement("div"); header.className = "operation-heading";
  const title = document.createElement("h3"); title.textContent = businessTitle || kinds[op.kind] || op.kind;
  const meta = document.createElement("small"); meta.className = "operation-meta";
  const created = Number.isFinite(op.created_at) ? new Date(op.created_at * 1000).toLocaleString("zh-CN", {hour12:false}) : "时间未提供";
  meta.textContent = (kinds[op.kind] || op.kind) + " · " + (statuses[op.state] || op.state) + "（" + op.state + "） · " + created;
  header.append(title, meta); article.append(header);
  const history = ["applied","submitted"].includes(op.state);
  if (!history) { const preview = document.createElement("pre"); preview.className = "business-preview"; preview.textContent = JSON.stringify(payload || (plan ? {target_record:plan.record_id, fields:plan.fields} : op.data), null, 2); article.append(preview); }
  const actions = document.createElement("div"); actions.className = "operation-actions";
  function action(label, callback) { const button = document.createElement("button"); button.textContent = label; button.onclick = () => run(callback); actions.append(button); }
  if (op.kind !== "publish" && op.state === "prepared") action("本人审核批准", async () => { if (!window.confirm("我已逐项核对上方完整字段，批准此精确版本。")) { message("已取消，未批准。"); return; } await api("/api/approve", {operation_id:op.id, digest:op.confirmation_digest, approved:true}); await loadOperations(); message("该版本已批准；应用后才会写飞书。"); });
  if (op.kind !== "publish" && op.state === "approved") action("应用到飞书并读回", async () => { const result = await api("/api/apply", {operation_id:op.id}); markDraftApplied(result); await loadOperations(); message("该计划已完成并保留同身份回读证据；重复请求不会再次写入。"); });
  if (op.kind !== "publish" && ["unknown","applying"].includes(op.state)) action("只读核验当前目标字段", async () => { const result = await api("/api/verify-write", {operation_id:op.id}); markDraftApplied(result); await loadOperations(); message("当前目标字段已达到预期；不证明原请求回执或写入者，没有重新写入。"); });
  if (op.kind === "publish" && op.state === "ready") { const panel = document.createElement("section"); panel.className = "media-review-panel"; article.append(panel); const old = mediaReviews.get(op.id); if (old) disposeReview(old); const context = newReviewContext(op,panel); mediaReviews.set(op.id,context); }
  if (op.kind === "publish" && ["submitted","unknown","submitting"].includes(op.state)) action("只读核验原发布任务", async () => { await api("/api/publish/verify", {operation_id:op.id}); await loadOperations(); message("已查询原任务；没有重新投递，公开可见仍由本人核验。"); });
  article.append(actions);
  const technical = document.createElement("details"); technical.className = history ? "history-details" : "technical-details";
  const summary = document.createElement("summary"); summary.textContent = history ? "展开历史版本、原始 JSON 与技术证据" : "查看原始冻结 JSON 与技术证据";
  const evidence = document.createElement("pre"); evidence.className = "compact"; evidence.textContent = JSON.stringify(op, null, 2);
  technical.append(summary, evidence); article.append(technical); return article;
}

async function loadOperations(append = false) {
  const previous = fingerprint; let result = await page(append ? queueOffset : 0);
  if (append && previous && previous !== fingerprint) { append = false; result = await page(0); } const list = $("operation-list"); if (!append) { disposeMediaReviews(); list.replaceChildren(); }
  if (!result.items.length && !append) list.textContent = "没有当前本人空间的技术操作。";
  for (const operation of result.items) list.append(operationView(operation)); queueOffset = result.next_offset; queueHasMore = result.has_more;
  $("more-operations").classList.toggle("hidden", !queueHasMore); controls(); if (!busy) message("技术队列已加载；未知事项仅核验，不重新执行。");
}
$("reload-operations").onclick = () => run(loadOperations);
$("more-operations").onclick = () => run(() => loadOperations(true));
function selectedAccount() { const account = accounts.find(item => item.id === $("publish-account").value); if (!account || account.status !== 1 || account.isLock || account.isFreeze || account.isOperate === false) throw new Error("请选择当前可操作的真实账号"); return account; }
function savePublication() {
  const id = $("publish-record").value; if (!id || !contextReady) return;
  state.publication[id] = {account:$("publish-account").value, kind:$("publish-kind").value, channel:$("publish-channel").value, fields:$("publish-fields").value, url:$("published-url").value, metrics:$("review-metrics").value, review:$("review-body").value, choices}; saveState();
}
function restorePublication(id) {
  const value = state.publication[id] || {}; $("publish-fields").value = value.fields || "{}"; $("published-url").value = value.url || ""; $("review-metrics").value = value.metrics || ""; $("review-body").value = value.review || "";
  $("publish-kind").value = value.kind || "imageText"; $("publish-channel").value = value.channel || "cloud"; if (value.account && accounts.some(account => account.id === value.account)) $("publish-account").value = value.account;
  choices = value.choices || {}; $("choices").textContent = "刷新或切换后须重新核对动态查询快照；素材文件需重新选择原文件。"; $("choice-options").replaceChildren();
}
for (const id of ["published-url","review-metrics","review-body"]) $(id).oninput = savePublication;
$("publish-record").onchange = () => restorePublication($("publish-record").value);
function clearChoices() {
  choices = {}; $("choice-options").replaceChildren(); $("choices").textContent = "账号、类型或查询已变化；请重新查询具体选项。";
  try { const fields = JSON.parse($("publish-fields").value); for (const field of ["music","category"]) for (const suffix of ["_choice","_choice_id","_choice_receipt"]) delete fields[field + suffix]; $("publish-fields").value = JSON.stringify(fields, null, 2); } catch (_) {}
  savePublication();
}
$("publish-account").onchange = clearChoices; $("publish-kind").onchange = clearChoices; $("publish-channel").onchange = savePublication;
$("publish-fields").oninput = () => {
  try { const fields = JSON.parse($("publish-fields").value), snapshot = choices.music; if (snapshot && snapshot.query !== (fields.music_query || "")) { clearChoices(); return; } } catch (_) {}
  savePublication();
};
function clearProvider() {
  disposeMediaReviews();
  providerFingerprint = null; delete state.provider; accounts = []; choices = {}; state.publication = {};
  for (const key of Object.keys(state.requests)) if (state.requests[key].kind === "publish") delete state.requests[key];
  $("publish-account").replaceChildren(); const empty = document.createElement("option"); empty.value = ""; empty.textContent = "重新读取本人蚁小二账号"; $("publish-account").append(empty);
  $("publish-fields").value = "{}"; $("choices").textContent = "蚁小二提供方变化后需要重新查询。"; $("choice-options").replaceChildren(); saveState();
}
$("accounts").onclick = () => run(async () => {
  const previous = $("publish-account").value, result = await api("/api/accounts");
  if (!Array.isArray(result.accounts) || !result.provider_fingerprint) throw new Error("provider_identity_required");
  const previousProvider = providerFingerprint || state.provider; const changed = !!previousProvider && previousProvider !== result.provider_fingerprint;
  if (changed) clearProvider();
  providerFingerprint = result.provider_fingerprint; state.provider = providerFingerprint; saveState(); accounts = result.accounts; const select = $("publish-account"); select.replaceChildren();
  const empty = document.createElement("option"); empty.value = ""; empty.textContent = "请选择本人账号"; select.append(empty);
  for (const account of accounts) { const option = document.createElement("option"); option.value = account.id; option.textContent = account.platformName + " · " + (account.platformAccountName || account.id) + (account.status === 1 ? "" : " · 状态待核验"); option.disabled = account.status !== 1 || !!account.isLock || !!account.isFreeze || account.isOperate === false; select.append(option); }
  if (!changed && accounts.some(account => account.id === previous)) select.value = previous; message("账号来自当前 yxer 提供方；准备和提交继续核验本人及提供方。");
});
for (const [button, field] of [["music","music"],["categories","category"]]) $(button).onclick = () => run(async () => {
  const account = selectedAccount(), fields = parseFields(), kind = $("publish-kind").value, query = field === "music" ? fields.music_query || "" : "";
  const result = await api("/api/choices", {account_id:account.id, kind, field, query});
  if (!result.receipt_id || !Array.isArray(result.options)) throw new Error("dynamic_choice_receipt_required");
  choices[field] = {...result, created:Date.now()}; $("choices").textContent = JSON.stringify(result, null, 2); const container = $("choice-options"); container.replaceChildren();
  for (const option of result.options) { const item = document.createElement("button"); item.textContent = option.label || (option.index + " · " + (option.value?.name || option.id)); item.onclick = () => { if (busy) return; const current = selectedAccount(); if (current.id !== result.account_id || $("publish-kind").value !== result.kind) { clearChoices(); return; } const values = parseFields(); values[field + "_choice_id"] = option.id; values[field + "_choice_receipt"] = result.receipt_id; delete values[field + "_choice"]; $("publish-fields").value = JSON.stringify(values, null, 2); savePublication(); container.querySelectorAll("button").forEach(element => element.classList.toggle("selected", element === item)); message("已绑定这次查询中的完整" + (field === "music" ? "配乐" : "分区") + "对象，不会靠二次查询索引换对象。"); }; container.append(item); }
  savePublication(); controls(); message(result.options.length ? "点击一个具体选项绑定查询快照，再准备发布。" : "这次查询没有可用选项；不补默认对象。");
});
function parseFields() {
  let fields; try { fields = JSON.parse($("publish-fields").value); } catch (_) { throw new ApiError("invalid_platform_json"); } if (!fields || Array.isArray(fields) || typeof fields !== "object") throw new Error("平台字段须为 JSON 对象");
  for (const [name, value] of Object.entries(fields)) { if (["music","category","images","video","cover","coverKey","horizontalCover","accountForms","formType","title","description"].includes(name)) throw new Error("动态或素材字段须经查询与真实上传收据"); if (!((["string","boolean"].includes(typeof value) || typeof value === "number" && Number.isFinite(value)) || Array.isArray(value) && value.every(item => typeof item === "string"))) throw new Error("平台字段仅接受 schema 规定的简单类型"); }
  return fields;
}
function validateChoices(fields, account, kind) {
  for (const field of ["music","category"]) { const selected = fields[field + "_choice_id"] ?? fields[field + "_choice"]; if (selected === undefined) continue; const receipt = fields[field + "_choice_receipt"]; const snapshot = choices[field]; if (!receipt || !snapshot || snapshot.receipt_id !== receipt || snapshot.account_id !== account.id || snapshot.kind !== kind || (field === "music" && snapshot.query !== (fields.music_query || "")) || Date.now() - snapshot.created > 1800000) throw new ApiError("dynamic_choice_receipt_required"); }
  if (account.platformName === "抖音" && kind === "imageText" && !fields.music_choice_id && fields.music_choice === undefined) throw new Error("抖音图文须先查询并选择真实配乐");
  if (account.platformName === "哔哩哔哩" && kind === "video" && ((!fields.category_choice_id && fields.category_choice === undefined) || !fields.tags || !fields.tags.length)) throw new Error("哔哩哔哩视频须选择分区并填写标签");
}
async function mediaHash(file) { const bytes = new Uint8Array(await file.arrayBuffer()); if (!crypto.subtle) throw new Error("浏览器缺少本机文件摘要能力，请使用 CLI"); const sum = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)); return Array.from(sum, value => value.toString(16).padStart(2,"0")).join(""); }
async function upload(file, usage) { const bytes = new Uint8Array(await file.arrayBuffer()); let raw = ""; for (let index = 0; index < bytes.length; index += 8192) raw += String.fromCharCode(...bytes.subarray(index, index + 8192)); return (await api("/api/media", {name:file.name, base64:btoa(raw), usage})).id; }
$("prepare-publish").onclick = () => run(async () => {
  const navigation = userNavigation;
  const account = selectedAccount(), kind = $("publish-kind").value, fields = parseFields(), record = $("publish-record").value, channel = $("publish-channel").value;
  const files = Array.from($("publish-media").files), cover = $("publish-cover").files[0];
  if (!record || !files.length) throw new Error("请选择真实记录和实际素材"); if (!["imageText","video"].includes(kind) || !["cloud","local"].includes(channel)) throw new Error("发布类型或通道无效");
  if (kind === "video" && (files.length !== 1 || !cover)) throw new Error("视频须有一个视频文件和独立封面"); if (kind === "imageText" && (cover || files.length > 20)) throw new Error("图文只选图片，最多 20 张；不另选视频封面"); validateChoices(fields, account, kind);
  const selected = files.map(file => ({file, usage:kind === "imageText" ? "image" : "video"})); if (cover) selected.push({file:cover, usage:"cover"});
  for (const item of selected) { if (!item.file.size || item.file.size > 20 * 1024 * 1024) throw new Error("网页文件上限 20 MiB；较大视频请用 CLI"); const expression = item.usage === "video" ? /\.(mp4|mov)$/i : /\.(png|jpe?g|webp|gif)$/i; if (!expression.test(item.file.name)) throw new Error("素材格式与发布类型不匹配"); }
  const info = []; for (const item of selected) info.push({name:item.file.name, size:item.file.size, usage:item.usage, sha256:await mediaHash(item.file)});
  const intent = {record_id:record, account_id:account.id, platform:account.platformName, kind, channel, fields, files:info}; const entry = requestFor("publish", intent);
  if (entry.attempted) { const existing = await findRequest(entry); if (existing) { entry.operation_id = existing.id; saveState(); if (navigation === userNavigation) tab("operations"); await loadOperations(); message("已找到原发布准备任务；没有重新上传或创建。请核验原状态。"); return; } }
  if (!window.confirm("准备会真实上传所选素材到蚁小二并预检，不会正式发布。记录、账号、类型、通道和文件已冻结；继续？")) { message("已取消上传。"); return; }
  const ids = []; for (let index = 0; index < selected.length; index++) { const key = signature(info[index]); if (!entry.media[key]) { entry.media[key] = await upload(selected[index].file, selected[index].usage); saveState(); } ids.push(entry.media[key]); }
  await sendStable("/api/publish/prepare", {record_id:record, account_id:account.id, platform:account.platformName, kind, channel, media_ids:ids, fields}, entry);
  if (navigation === userNavigation) tab("operations"); await loadOperations(); message("上传及预检已完成，尚未正式发布。同一输入重试保留原 key 与素材 ID；请核对冻结包。");
});
$("register-published").onclick = () => run(async () => { savePublication(); const record_id = $("publish-record").value, url = $("published-url").value; if (!record_id || !/^https:\/\//.test(url)) throw new Error("请选择记录并填写本人已核验的 HTTPS 作品链接"); reportPlan(await finishPlan("/api/published", "published", {record_id, url})); });
$("review").onclick = () => run(async () => { savePublication(); const record_id = $("publish-record").value, body = $("review-body").value; let metrics; try { metrics = JSON.parse($("review-metrics").value); } catch (_) { throw new ApiError("invalid_metrics_json"); } if (!record_id || !body.trim() || !metrics || !["platform","work_id","observed_at","values"].every(key => Object.hasOwn(metrics,key))) throw new Error("请选择记录，提供完整指标来源和复盘结论"); reportPlan(await finishPlan("/api/review", "review", {record_id, metrics, body})); });
$("doctor").onclick = () => run(async () => { const result = await api("/api/doctor", {}); $("doctor-result").textContent = JSON.stringify(result, null, 2); message("真实只读接入检查完成；不代表真实写入、发布或公开可见已通过。"); });
if (typeof window.addEventListener === "function") { window.addEventListener("pagehide",disposeMediaReviews); window.addEventListener("beforeunload", event => { if (hasUnsaved() || Object.values(state.requests).some(item => item.uncertain)) { event.preventDefault(); event.returnValue = ""; } }); }
