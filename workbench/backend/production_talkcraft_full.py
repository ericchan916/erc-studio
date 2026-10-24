"""Codex + complete upstream Talkcraft. No prescribed scene schema or renderer.
The trusted service owns authentication, source integrity and output validation only.
"""
import hashlib,json,math,os,pathlib,shutil,time
VERSION='video-talkcraft-full-cli-v1'
COMMIT='4cd673df4b7a6a35784a0881df223721789c5e23'
BUNDLE=pathlib.Path.home()/'Library/Application Support/ContentBaseTalkcraft'/COMMIT

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,data):
 temp=p.with_name(p.name+'.new');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2));temp.chmod(0o600);os.replace(temp,p)
def bundle_ready(root=None):
 root=pathlib.Path(root or BUNDLE);meta=json.loads((root/'BUNDLE-MANIFEST.json').read_text())
 if meta.get('commit')!=COMMIT or len(meta.get('files',{}))<900:raise RuntimeError('talkcraft_full_bundle_incomplete')
 for name,checksum in meta['files'].items():
  p=root/name
  if not p.is_file() or p.is_symlink() or sha(p)!=checksum:raise RuntimeError('talkcraft_full_bundle_changed:'+name)
 for name in ('SKILL.md','runtime/check-runtime.sh','runtime/package-lock.json','scripts/render_shots.mjs','workbench/package.json'):
  if not (root/name).is_file():raise RuntimeError('talkcraft_full_component_missing:'+name)
 if len(list((root/'template/cards').glob('*.tsx')))<108:raise RuntimeError('talkcraft_full_cards_missing')
 if not (root/'runtime/node_modules/.bin/remotion').is_file():raise RuntimeError('talkcraft_full_runtime_not_installed')
 return meta

def request(work,job):
 """The skill and user's request own creative decisions; no card allowlist."""
 return ('使用本工程完整的video-talkcraft Skill制作这条真实口播。先完整读取 .agents/skills/video-talkcraft/SKILL.md，按需读取其全部references、108张卡片、scripts、template与workbench，按原Skill实际编写制作工程、渲染、审片和返修；不是只返回JSON分镜。\n'
 '用户最新选择：Codex直接搭配完整Skill自主制作，最终回本人审片。原片在input/source'+pathlib.Path(job['fileName']).suffix.lower()+'，真实需求原文见USER_REQUEST.md。业务标题只用于飞书绑定，不替代原片主题。\n'
 '沿用现有发布画幅1080x1440、30fps；其他视觉、镜头、素材与动效由Skill和实际内容决定，没有固定镜头列表、强制白底、固定圆章或预写renderer。\n'
 '本机已装ffmpeg/ffprobe、whisper-cli与small模型（/Users/USER/resources/YOUR_400EA0739A runtime依赖已克隆到本任务，制作过程中不改共享服务/凭据/其他任务。\n'
 '只在本任务工程内创作与执行。保持input原件，保留制作源码/SHOTBOOK/实际审片证据；不读业务库/其他成员文件，不发布或确认业务，不发消息；没有本人提供的付费素材或外部API凭据时如实列缺失，不调用收费服务。\n'
 '交付到deliverables/final.mp4及deliverables/final-nobgm.mp4（画面相同、后者无背景音乐），音画真实、原声不重合成。最后写deliverables/result.json记录summary和unmet（只说明实际缺失），并保留源码入口与验证记录。服务仅核验这些真实产物，不替你制作。')

def execute(service,id):
 from production_backend import CLI,FFPROBE
 meta=bundle_ready();d=service._dir(id);job=service._read(id);original=d/'source.upload';before=sha(original)
 env={k:os.environ[k] for k in ('HOME','PATH','USER','TMPDIR') if k in os.environ};env['PATH']='/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin'
 info=json.loads(service._run([FFPROBE,'-v','error','-show_streams','-show_format','-of','json',str(original)],d,30,env=env));duration=float(info['format']['duration'])
 if not math.isfinite(duration) or not .5<=duration<=600 or not all(any(s.get('codec_type')==kind for s in info['streams']) for kind in ('video','audio')):raise RuntimeError('source_requires_supported_video_and_speech')
 work=d/'full-work';work.mkdir(mode=0o700);(work/'input').mkdir();(work/'deliverables').mkdir();skill=work/'.agents/skills/video-talkcraft';skill.parent.mkdir(parents=True)
 shutil.copytree(BUNDLE,skill,symlinks=True,ignore=shutil.ignore_patterns('node_modules','.remotion'))
 # APFS copy-on-write clone: all changes stay inside this job, not the shared runtime.
 service._run(['/bin/cp','-cR',str(BUNDLE/'runtime/node_modules'),str(skill/'runtime/node_modules')],d,180,env=env)
 for link in ('workbench/node_modules',):
  p=skill/link
  if p.is_symlink():p.unlink()
  p.symlink_to('../runtime/node_modules',target_is_directory=True)
 source=work/'input'/('source'+pathlib.Path(job['fileName']).suffix.lower());shutil.copy2(original,source);source.chmod(0o400)
 (work/'USER_REQUEST.md').write_text('# 本人制作需求\n\n'+job['requirement']+'\n\n业务绑定标题：'+job['title']+'\n账号：'+job['account']+'\n原片主题以真实原声为准；人工审片与发布由本人决定。\n')
 text=request(work,job);(work/'TASK.md').write_text(text)
 args=[CLI,'exec','--ignore-user-config','--ephemeral','--skip-git-repo-check','-C',str(work),'-s','workspace-write','-c','approval_policy="never"','-c','sandbox_workspace_write.network_access=true','-c','model_reasoning_effort="high"','-o',str(work/'codex-final-response.md'),'-']
 service._update(job,state='planning',phase='codex_full_skill',workflowVersion=VERSION)
 service._run(args,work,7200,input=text,env=env)
 if sha(original)!=before or sha(source)!=before:raise RuntimeError('original_source_changed_by_generation')
 service._update(job,state='rendering',phase='verifying')
 publish(service,d,job,work,meta,env)

def publish(service,d,job,work,meta,env):
 from production_backend import FFMPEG,FFPROBE
 root=work/'deliverables';images=[];checks=[];sizes=[]
 for name,target in (('final.mp4',d/'output.mp4'),('final-nobgm.mp4',d/'output-nobgm.mp4')):
  p=root/name
  if not p.is_file() or p.is_symlink() or os.path.commonpath([str(p.resolve()),str(work.resolve())])!=str(work.resolve()):raise RuntimeError('full_skill_output_missing_or_external')
  if p.stat().st_size<2000:raise RuntimeError('full_skill_empty_output')
  j=json.loads(service._run([FFPROBE,'-v','error','-count_frames','-show_streams','-show_format','-of','json',str(p)],d,120,env=env));v=next((s for s in j['streams'] if s.get('codec_type')=='video'),{});a=next((s for s in j['streams'] if s.get('codec_type')=='audio'),{})
  if v.get('codec_name')!='h264' or a.get('codec_name')!='aac' or (v.get('width'),v.get('height'))!=(1080,1440) or v.get('r_frame_rate')!='30/1' or not 0<float(j['format']['duration'])<=600:raise RuntimeError('full_skill_output_contract_failed')
  frames=int(v.get('nb_read_frames',0));sizes.append(frames);checks.append(j);images.append(hashlib.sha256(service._run([FFMPEG,'-v','error','-i',str(p),'-map','0:v:0','-f','framemd5','-'],d,300,env=env).encode()).hexdigest());shutil.copy2(p,target);target.chmod(0o600)
 if sizes[0]!=sizes[1] or images[0]!=images[1]:raise RuntimeError('full_skill_variant_picture_mismatch')
 result_path=root/'result.json'
 if not result_path.is_file() or result_path.is_symlink():raise RuntimeError('full_skill_receipt_missing')
 receipt=json.loads(result_path.read_text());summary=receipt.get('summary');unmet=receipt.get('unmet',[])
 if not isinstance(summary,str) or len(summary)>2000 or not isinstance(unmet,list) or len(unmet)>40 or any(not isinstance(x,str) or len(x)>1000 for x in unmet):raise RuntimeError('full_skill_receipt_invalid')
 save(work/'verification.json',{'workflowVersion':VERSION,'upstreamCommit':meta['commit'],'fullBundleFiles':len(meta['files']),'codexWritesAndRenders':True,'outputProbe':checks,'samePictures':True,'frames':sizes[0],'reviewBoundary':'Program verifies real deliverables; human taste/rights approval remains separate.'})
 service._update(job,state='succeeded',phase='awaiting_human_review',finishedAt=time.time(),summary=summary[:600],unmet=unmet,qualityStatus='partial' if unmet else 'needs_review',variants=['main','nobgm'],outputBytes=(d/'output.mp4').stat().st_size,outputSha256=sha(d/'output.mp4'),nobgmSha256=sha(d/'output-nobgm.mp4'),runtime={'engine':'video-talkcraft-full','remotionVersion':str(receipt.get('remotionVersion') or '由实际工程验证')})
