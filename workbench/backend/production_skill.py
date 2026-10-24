"""Native Codex CLI + the complete installed video-use skill, not a scene planner."""
import hashlib,json,os,pathlib,shutil,time,uuid
from fractions import Fraction
from local_codex import LocalCodex,CodexError,owned_file
VERSION='video-use-codex-cli-v1'
CACHE=pathlib.Path(os.environ.get('CONTENTBASE_RESOURCE_851C4505',str(pathlib.Path.home()/'.local/share/content-company-workbench/unconfigured-resources'/'CONTENTBASE_RESOURCE_851C4505')))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def execute(service,id):
 from production_backend import FFPROBE,FFMPEG
 d=service._dir(id);job=service._read(id);src=d/'source.upload';original=sha(src)
 runner=service.codex;cid=str(uuid.uuid5(uuid.NAMESPACE_URL,'studio-production:'+id));work=runner.workspace(job['owner'],cid);source=work/('source'+pathlib.Path(job['fileName']).suffix.lower())
 if not source.exists():shutil.copy2(src,source);source.chmod(0o400)
 if sha(source)!=original:raise RuntimeError('production_source_integrity_failed')
 info=json.loads(service._run([FFPROBE,'-v','error','-show_streams','-show_format','-of','json',str(source)],d,30));v=next((s for s in info['streams'] if s.get('codec_type')=='video'),None);a=next((s for s in info['streams'] if s.get('codec_type')=='audio'),None)
 if not v or not a or not .5<=float(info['format']['duration'])<=600:raise RuntimeError('source_requires_supported_video_and_speech')
 cache=False
 if CACHE.is_file():
  value=json.loads(CACHE.read_text())
  if value.get('source_sha256')==original:
   target=work/'edit/transcripts/source.json';target.parent.mkdir(parents=True,exist_ok=True)
   if not target.exists():shutil.copy2(CACHE,target)
   cache=True
 text=('按本工程完整video-use Skill自主完成真实口播制作，不只返回计划。原片 '+source.name+'，本人需求：'+job['requirement']+'。\n'
       '原片为 '+str(v['width'])+'x'+str(v['height'])+' / '+v['r_frame_rate']+'，默认保持原画幅与帧率，原声不重合成；按语义决定剪辑/补镜/动画，不能把固定图卡或零动效当本人的默认需求。\n'
       +('已有本人明确批准复用的同SHA逐字缓存 edit/transcripts/source.json；它来自Whisper旧缓存，不冒称新火山ASR。直接复用，不上传原声、不要重新转写。' if cache else '没有已批准的逐字缓存；额外ASR或素材服务的凭据/费用条件须如实说明，禁止读取宿主凭据。')+'\n'
       '本机可用隔离FFmpeg/libass与Python依赖。保留源码、EDL、字幕、实际抽帧审片记录。按已提供的需求继续；必需条件缺失才waiting_input，同一任务等本人补充，不重建。\n'
       '完成文件放edit/artifacts/final.mp4与final-nobgm.mp4，两版画面完全相同；无BGM时明确两版音轨相同。另放result.json（summary字符串、unmet字符串数组）和review.md（实际观察，不伪造审片）。真实完整解码后再返回succeeded。最终审美和发布留本人，不发布、不写飞书。')
 if job.get('controls'):text+='\n本人本任务后续补充：'+json.dumps(job['controls'],ensure_ascii=False)
 service._update(job,state='planning',phase='codex_skill_execution',workflowVersion=VERSION)
 try:reply=runner.run(work,text,id,timeout=7200)
 except CodexError:raise
 if reply['state']=='waiting_input':service._update(job,state='waiting_input',phase='waiting_input',summary=reply['text'][:600],unmet=['等待本人补充必要条件'],qualityStatus='needs_input');return
 root=work/'edit/artifacts';checks=[];pictures=[]
 for name,out in [('final.mp4','output.mp4'),('final-nobgm.mp4','output-nobgm.mp4')]:
  p=root/name
  if not p.is_file() or not owned_file(work,p) or os.path.commonpath([str(p.resolve()),str(work.resolve())])!=str(work.resolve()) or not 2000<=p.stat().st_size<=512*1024*1024:raise RuntimeError('codex_skill_output_missing')
  j=json.loads(service._run([FFPROBE,'-v','error','-count_frames','-show_streams','-show_format','-of','json',str(p)],d,120));video=next((x for x in j['streams'] if x.get('codec_type')=='video'),{});audio=next((x for x in j['streams'] if x.get('codec_type')=='audio'),{})
  if video.get('codec_name')!='h264' or audio.get('codec_name')!='aac' or not 0<float(Fraction(video.get('r_frame_rate','0')))<=120 or not all(0<int(video.get(x,0))<=3840 for x in ('width','height')) or not 0<float(j['format']['duration'])<=600:raise RuntimeError('codex_skill_output_contract_failed')
  service._run([FFMPEG,'-v','error','-i',str(p),'-f','null','-'],d,300);checks.append(j);pictures.append(hashlib.sha256(service._run([FFMPEG,'-v','error','-i',str(p),'-map','0:v:0','-f','framemd5','-'],d,300).encode()).hexdigest());shutil.copy2(p,d/out)
 if pictures[0]!=pictures[1] or sha(src)!=original or sha(source)!=original:raise RuntimeError('codex_skill_integrity_failed')
 receipt=root/'result.json';review=root/'review.md'
 if not receipt.is_file() or not review.is_file() or not all(owned_file(work,p) and p.stat().st_size<=256*1024 for p in (receipt,review)):raise RuntimeError('codex_skill_review_or_receipt_missing')
 value=json.loads(receipt.read_text());summary=value.get('summary');unmet=value.get('unmet')
 if not isinstance(summary,str) or len(summary)>2000 or not isinstance(unmet,list) or any(not isinstance(x,str) or len(x)>1000 for x in unmet):raise RuntimeError('codex_skill_receipt_invalid')
 service._update(job,state='succeeded',phase='awaiting_human_review',finishedAt=time.time(),summary=summary[:600],unmet=unmet,qualityStatus='partial' if unmet else 'needs_review',variants=['main','nobgm'],outputBytes=(d/'output.mp4').stat().st_size,outputSha256=sha(d/'output.mp4'),nobgmSha256=sha(d/'output-nobgm.mp4'),runtime={'engine':'codex-cli','skill':'video-use','sourceFormatPreserved':checks[0]['streams'][0].get('width')==v['width'] and checks[0]['streams'][0].get('height')==v['height'] and checks[0]['streams'][0].get('r_frame_rate')==v['r_frame_rate'],'codexModel':runner.model},verification={'fullDecode':True,'samePictures':True,'sourceIntact':True})
