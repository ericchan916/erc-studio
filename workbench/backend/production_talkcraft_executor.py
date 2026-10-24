"""Existing owner-bound production job, now executed through pinned Talkcraft."""
import hashlib,json,math,os,pathlib,shutil,time
from production_talkcraft import VERSION,LIMIT_SECONDS,build,prompt,transcript,verify_bundle
from production_director import MUSIC
SWIFT='/usr/bin/swift'

def store(path,value):
 tmp=path.with_name(path.name+'.new');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.chmod(0o600);os.replace(tmp,path)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def _deps(service):
 from production_backend import MODULES,BROWSER
 pins=json.loads((service.template/'package.json').read_text())['dependencies']
 for name in ('remotion','@remotion/cli','react','react-dom'):
  if json.loads((MODULES/name/'package.json').read_text())['version']!=pins[name]:raise RuntimeError('remotion_dependency_version_changed')
 if not pathlib.Path(SWIFT).is_file() or not (service.template/'talkcraft-media.swift').is_file() or not BROWSER.is_file():raise RuntimeError('talkcraft_media_dependency_missing')
 verify_bundle(service.template);return pins

def execute(service,id):
 from production_backend import CLI,FFMPEG,FFPROBE,WHISPER,MODEL,MODULES
 d=service._dir(id);v=service._read(id);src=d/'source.upload';env={k:os.environ[k] for k in ('HOME','PATH','USER','TMPDIR') if k in os.environ};env['PATH']=os.environ.get('PATH','')
 _deps(service);service._update(v,state='transcribing',phase='source_validation',workflowVersion=VERSION)
 probe=json.loads(service._run([FFPROBE,'-v','error','-show_streams','-show_format','-of','json',str(src)],d,30,env=env));streams=probe.get('streams',[])
 if not any(x.get('codec_type')=='video' for x in streams) or not any(x.get('codec_type')=='audio' for x in streams):raise RuntimeError('source_requires_video_and_speech_audio')
 duration=float(probe['format']['duration'])
 if not math.isfinite(duration) or not .5<=duration<=LIMIT_SECONDS:raise RuntimeError('source_duration_unsupported')
 work=d/'work';work.mkdir(mode=0o700);(work/'public').mkdir();(work/'src').mkdir()
 for name in ('package.json','TALKCRAFT_WORKFLOW.md','talkcraft.schema.json','talkcraft-media.swift'):shutil.copy2(service.template/name,work/name)
 for name in ('index.ts','TalkcraftFilm.tsx'):shutil.copy2(service.template/'src'/name,work/'src'/name)
 shutil.copy2(service.template/'src/TalkcraftRoot.tsx',work/'src/Root.tsx');shutil.copytree(service.template/'src/talkcraft-cards',work/'src/talkcraft-cards');(work/'node_modules').symlink_to(MODULES,target_is_directory=True)
 # Original remains source.upload; deterministic output geometry is measured before crop.
 service._update(v,state='transcribing',phase='preparing_source')
 service._run([FFMPEG,'-hide_banner','-loglevel','error','-y','-i',str(src),'-map','0:v:0','-map','0:a:0','-vf','scale=1080:1440:force_original_aspect_ratio=increase,crop=1080:1440','-r','30','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart',str(work/'public/source.mp4')],d,1800,env=env)
 wav=work/'audio.wav';service._run([FFMPEG,'-v','error','-y','-i',str(work/'public/source.mp4'),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)],d,180,env=env)
 service._update(v,state='transcribing',phase='word_alignment');asr=work/'asr'
 service._run([WHISPER,'-m',str(MODEL),'-f',str(wav),'-l','zh','-ojf','-dtw','small','-of',str(asr),'-np'],d,1800,env=env)
 segments=transcript(json.loads((work/'asr.json').read_text()),duration);store(work/'transcript.json',segments)
 service._update(v,state='planning',phase='face_measurement');face,warnings=face_geometry(service,d,work,env);store(work/'face-geometry.json',{'sourceSha256':sha(work/'public/source.mp4'),'geometry':face,'warnings':warnings});service._update(v,state='planning',phase='talkcraft_directing');request=prompt(service.template,v['title'],v['requirement'],segments,duration,{'face':face,'sourceExists':True,'renderWidth':1080,'renderHeight':1440,'fps':30});(work/'director-request.txt').write_text(request)
 out=work/'plan.json';args=[CLI,'exec','--ignore-user-config','--ephemeral','--skip-git-repo-check','-c','model_reasoning_effort="high"','-C',str(work),'-s','read-only','--output-schema',str(work/'talkcraft.schema.json'),'-o',str(out),'-']
 service._run(args,work,1800,input=request,env=env);plan=json.loads(out.read_text())
 try:data,unmet=build(plan,segments,duration)
 except RuntimeError as error:
  if not str(error).startswith('talkcraft_'):raise
  repair=request+'\n上次计划未通过原声/绑定校验：'+str(error)+'\n只修复本次Schema计划，不重启任务：'+json.dumps(plan,ensure_ascii=False)
  service._run(args,work,1800,input=repair,env=env);plan=json.loads(out.read_text());data,unmet=build(plan,segments,duration)
 render(service,d,v,work,plan,data,unmet,env)

def face_geometry(service,d,work,env):
 from production_backend import FFMPEG
 frames=work/'face-samples';frames.mkdir(exist_ok=True)
 service._run([FFMPEG,'-v','error','-y','-i',str(work/'public/source.mp4'),'-vf','fps=0.5','-frames:v','300',str(frames/'%04d.png')],d,240,env=env)
 output=work/'face-measurements.json';service._run([SWIFT,str(work/'talkcraft-media.swift'),'faces',str(frames),str(output)],work,180,env=env)
 measured=json.loads(output.read_text());samples=measured.get('samples',[])
 if not samples:return {'anchorX':.5,'anchorY':.45,'chipScale':.42,'measured':False},['人物未检出；当前为居中回退裁切，请本人核对']
 boxes=[x['face'] for x in samples];x=min(b[0] for b in boxes);y=min(b[1] for b in boxes);right=max(b[0]+b[2] for b in boxes);bottom=max(b[1]+b[3] for b in boxes)
 scale=min(.42,210/max(right-x,bottom-y));return {'anchorX':(x+right)/2160,'anchorY':(y+bottom)/2880,'chipScale':scale,'measured':True,'sampleCount':len(boxes),'unionFace':[x,y,right-x,bottom-y]},[]

def subtitle_spec(data):
 pages=data['captions'];bounds={0,data['totalFrames']}
 for p in pages:bounds.update((p['startFrame'],p['endFrame']))
 white=[]
 for s in data['scenes']:
  bounds.update((s['from'],s['to']))
  if s.get('camera','full' if s['card']=='full' else 'chip')=='full':white.append((s['from'],s['to']))
  else:
   at=max(0,s['cues'][0]['localFrame']-13);stop=min(s['to'],s['from']+at+13);white.append((s['from'],stop));bounds.update((s['from'],stop))
 bounds=sorted(b for b in bounds if 0<=b<=data['totalFrames']);ranges=[]
 for a,b in zip(bounds,bounds[1:]):
  mid=(a+b)/2;index=next((i for i,p in enumerate(pages) if p['startFrame']<=mid<p['endFrame']),None);is_white=any(x<=mid<y for x,y in white)
  ranges.append({'startFrame':a,'endFrame':b,'file':'empty.png' if index is None else '%04d-%s.png'%(index,'white' if is_white else 'ink')})
 return {'pages':pages,'ranges':ranges}

def master(service,d,work,raw,caption_track,target,env):
 from production_backend import FFMPEG
 log=service._run([FFMPEG,'-hide_banner','-i',str(raw),'-af','loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json','-f','null','-'],d,240,env=env)
 # loudnorm JSON is on stderr; this runner preserves receipts but returns stdout only.
 # Use the latest exact command receipt, not a made-up normalization measurement.
 records=[json.loads(x) for x in (d/'command-records.jsonl').read_text().splitlines()];stderr=records[-1]['stderr'];j=json.loads(stderr[stderr.rfind('{'):stderr.rfind('}')+1])
 store(work/(target.stem+'-loudness-input.json'),j)
 af='loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={input_i}:measured_TP={input_tp}:measured_LRA={input_lra}:measured_thresh={input_thresh}:offset={target_offset}:linear=true'.format(**j)
 service._run([FFMPEG,'-v','error','-y','-i',str(raw),'-i',str(caption_track),'-filter_complex','[0:v][1:v]overlay=0:0:format=auto,scale=in_range=pc:out_range=tv,format=yuv420p[v]','-map','[v]','-map','0:a:0','-af',af,'-t',str(json.loads((work/'render-data.json').read_text())['totalFrames']/30),'-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-color_range','tv','-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart',str(target)],d,1800,env=env)

def render(service,d,v,work,plan,data,unmet,env):
 from production_backend import FFMPEG,FFPROBE,MODULES,BROWSER
 service._update(v,state='rendering',phase='face_measurement',renderStartedAt=time.time())
 cached=work/'face-geometry.json'
 if cached.is_file():
  info=json.loads(cached.read_text())
  if info['sourceSha256']!=sha(work/'public/source.mp4'):raise RuntimeError('talkcraft_source_changed_after_face_measurement')
  face,warnings=info['geometry'],info['warnings']
 else:face,warnings=face_geometry(service,d,work,env)
 data['face']=face;unmet=list(unmet)+warnings
 if not MUSIC.is_file():raise RuntimeError('production_music_asset_missing')
 shutil.copy2(MUSIC,work/'public/bgm.mp3');music=json.loads(service._run([FFPROBE,'-v','error','-show_entries','format=duration','-of','json',str(MUSIC)],d,30,env=env));data['musicFrames']=max(1,round(float(music['format']['duration'])*30))
 store(work/'render-data.json',data);store(work/'props-nobgm.json',{'bgm':False});store(work/'beats.json',data['beats']);store(work/'SHOTBOOK.json',data['scenes'])
 store(work/'director-receipt.json',{'workflowVersion':VERSION,'planSource':'real Codex readonly schema output','upstreamCommit':verify_bundle(service.template)['commit'],'planSha256':sha(work/'plan.json'),'face':face,'unmet':unmet,'qualityBoundary':'技术校验不是本人审片、商业发布或完整上游工作台验收'})
 remotion=MODULES/'.bin/remotion';raw=work/'raw-main.mp4';second=work/'raw-nobgm.mp4';service._update(v,state='rendering',phase='talkcraft_render')
 for output,props in ((raw,[]),(second,['--props='+str(work/'props-nobgm.json')])):
  service._run([str(remotion),'render','src/index.ts','VoiceEdit',str(output),'--codec','h264','--audio-codec','aac','--pixel-format','yuv420p','--browser-executable='+str(BROWSER),'--concurrency=1','--overwrite']+props,work,3600,env={**env,'HOME':str(work)})
 service._update(v,state='rendering',phase='ordinary_captions');spec=subtitle_spec(data);store(work/'caption-spec.json',spec);png=work/'public/caption-overlays'
 service._run([SWIFT,str(work/'talkcraft-media.swift'),'captions',str(work/'caption-spec.json'),str(png)],work,180,env=env)
 concat=['ffconcat version 1.0'];last='empty.png'
 for part in spec['ranges']:
  last=part['file'];path=png/last
  # All filenames are generated locally; only fixed UUID directories may contain quotes.
  escaped=str(path).replace("'","'\\''");concat.extend(["file '"+escaped+"'",'duration %.9f'%((part['endFrame']-part['startFrame'])/30)])
 concat.append("file '"+str(png/last).replace("'","'\\''")+"'");(work/'captions.ffconcat').write_text('\n'.join(concat)+'\n');caption_track=work/'captions.mov'
 service._run([FFMPEG,'-v','error','-y','-f','concat','-safe','0','-i',str(work/'captions.ffconcat'),'-vf','fps=30','-t',str(data['totalFrames']/30),'-c:v','qtrle','-pix_fmt','argb',str(caption_track)],d,600,env=env)
 service._update(v,state='rendering',phase='audio_mastering');output=d/'output.mp4';nobgm=d/'output-nobgm.mp4'
 for source,target in ((raw,output),(second,nobgm)):master(service,d,work,source,caption_track,target,env);target.chmod(0o600)
 service._update(v,state='rendering',phase='verifying');checks=[];pictures=[]
 for target in (output,nobgm):
  info=json.loads(service._run([FFPROBE,'-v','error','-count_frames','-show_streams','-show_format','-of','json',str(target)],d,120,env=env));video=next((x for x in info['streams'] if x.get('codec_type')=='video'),{});audio=next((x for x in info['streams'] if x.get('codec_type')=='audio'),{})
  if video.get('codec_name')!='h264' or video.get('width')!=1080 or video.get('height')!=1440 or video.get('pix_fmt')!='yuv420p' or int(video.get('nb_read_frames',0))!=data['totalFrames'] or audio.get('codec_name')!='aac' or abs(float(info['format']['duration'])-data['totalFrames']/30)>.1:raise RuntimeError('talkcraft_render_verification_failed')
  # Receipts retain an exact full-file digest, not _run's bounded stdout tail.
  text=service._run([FFMPEG,'-v','error','-i',str(target),'-map','0:v:0','-f','framemd5','-'],d,240,env=env);store(work/(target.stem+'-probe.json'),info);checks.append(info);pictures.append(hashlib.sha256(text.encode()).hexdigest());(work/(target.stem+'.framemd5')).write_text(text)
 if pictures[0]!=pictures[1]:raise RuntimeError('render_variant_picture_mismatch')
 frames=[]
 for i,s in enumerate(data['scenes']):
  for suffix,frame in (('in',s['from']),('middle',(s['from']+s['to'])//2),('out',s['to']-1)):
   p=work/('scene-check-%02d-%s.png'%(i,suffix));service._run([FFMPEG,'-v','error','-y','-ss',str(frame/30),'-i',str(output),'-frames:v','1',str(p)],d,30,env=env);frames.append({'frame':frame,'sha256':sha(p)})
 store(work/'verification.json',{'workflowVersion':VERSION,'width':1080,'height':1440,'frames':data['totalFrames'],'mainSha256':sha(output),'nobgmSha256':sha(nobgm),'picturesIdentical':True,'frameChecks':frames,'status':'technical_pass_human_review_pending'})
 service._update(v,state='succeeded',phase='awaiting_human_review',finishedAt=time.time(),summary=plan['summary'],unmet=unmet,qualityStatus='partial' if unmet else 'needs_review',variants=['main','nobgm'],outputSha256=sha(output),nobgmSha256=sha(nobgm),outputBytes=output.stat().st_size,transcriptSha256=sha(work/'transcript.json'),planSha256=sha(work/'plan.json'),runtime={'codex':'/Users/USER/resources/YOUR_A1FACD7F45','remotionVersion':_deps(service)['remotion'],'engine':'video-talkcraft'})
