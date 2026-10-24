#!/usr/bin/env python3
"""Trusted offline association of a finished local pair, never a model run receipt.

No HTTP path accepts local filenames. No Production constructor (restart side
 effects), business writes, config changes, publication or re-rendering here.
"""
import argparse, fcntl, hashlib, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time, uuid
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'backend'))
from production_backend import verify_project, FFMPEG, FFPROBE, MAX_BYTES

VERSION = 'contentbase-oral-studio-v1'

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()

def regular(path, limit=MAX_BYTES):
    path = pathlib.Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise RuntimeError('invalid_local_result_file')
    return path

def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)); path.chmod(0o600)

def check_media(main, nobgm, records, run):
    def command(args):
        p = run(args, capture_output=True, text=True, timeout=300)
        records.append({'args': args, 'stdin': None, 'stdout': p.stdout, 'stderr': p.stderr, 'exit': p.returncode})
        if p.returncode: raise RuntimeError('media_verification_failed:' + pathlib.Path(args[0]).name)
        return p.stdout
    pictures = []
    formats = []
    for path in (main, nobgm):
        data = json.loads(command([FFPROBE, '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', str(path)]))
        videos = [s for s in data['streams'] if s['codec_type'] == 'video']
        audios = [s for s in data['streams'] if s['codec_type'] == 'audio']
        if len(videos) != 1 or len(audios) != 1: raise RuntimeError('result_stream_count_invalid')
        v, a = videos[0], audios[0]
        if (v['width'], v['height'], v['avg_frame_rate'], v['codec_name'], a['codec_name']) != (1080, 1920, '30/1', 'h264', 'aac'):
            raise RuntimeError('result_format_invalid')
        duration = float(data['format']['duration']); frames = int(v['nb_read_frames'])
        if not 0 < duration <= 600 or abs(duration - frames / 30) > .1: raise RuntimeError('result_duration_invalid')
        command([FFMPEG, '-v', 'error', '-xerror', '-i', str(path), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
        pictures.append(command([FFMPEG, '-v', 'error', '-i', str(path), '-map', '0:v:0', '-f', 'framemd5', '-']))
        formats.append({'duration': duration, 'frames': frames, 'width': 1080, 'height': 1920, 'fps': 30})
    if pictures[0] != pictures[1] or formats[0] != formats[1]: raise RuntimeError('variant_picture_mismatch')
    return {**formats[0], 'pictureSha256': hashlib.sha256(pictures[0].encode()).hexdigest()}

def link(config, project, title, account, source, main, nobgm, review, key, audit, run=subprocess.run):
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', key) or account != '示例内容账号': raise RuntimeError('invalid_link_request')
    owners = [m['principal_id'] for m in config.get('members', []) if m.get('handle') == 'owner']
    if len(owners) != 1: raise RuntimeError('trusted_owner_missing')
    owner = owners[0]
    files = {'source': regular(source), 'main': regular(main), 'nobgm': regular(nobgm), 'review': regular(review, 4 * 1024 * 1024)}
    # The fixed CLI user identity revalidates exact record/account/type/title.
    # Read-only even when association is retried.
    verify_project(config, run, project, account, title)
    hashes = {name: digest(path) for name, path in files.items()}
    signature = hashlib.sha256(json.dumps({'owner': owner, 'project': project, 'title': title, 'account': account, 'hashes': hashes, 'version': VERSION}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    state = pathlib.Path(config['state_dir']); root = state / 'production-runs'
    if any(p.is_symlink() for p in (state, *state.parents)): raise RuntimeError('unsafe_state_path')
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink(): raise RuntimeError('unsafe_result_root')
    audit = pathlib.Path(audit); audit.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Registrar-only lock; the live executor never mutates these new terminal IDs.
    lockpath = state / '.production-result-link.lock'
    fd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'contentbase-local-result:' + owner + ':' + key))
        destination = root / id
        if destination.exists():
            if destination.is_symlink() or (destination / 'status.json').is_symlink(): raise RuntimeError('unsafe_existing_result')
            old = json.loads((destination / 'status.json').read_text())
            if old.get('signature') != signature or old.get('sourceKind') != 'linked_local_result': raise RuntimeError('idempotency_conflict')
            for name, filename in [('source', 'source.upload'), ('main', 'output.mp4'), ('nobgm', 'output-nobgm.mp4'), ('review', 'review.md')]:
                if digest(regular(destination / filename)) != hashes[name]: raise RuntimeError('linked_artifact_changed')
            return {'id': id, 'projectId': project, 'state': old['state'], 'reused': True}
        records = []
        with tempfile.TemporaryDirectory(prefix='.production-result-', dir=state) as temp:
            staging = pathlib.Path(temp) / id; staging.mkdir(mode=0o700)
            for name, filename in [('source', 'source.upload'), ('main', 'output.mp4'), ('nobgm', 'output-nobgm.mp4'), ('review', 'review.md')]:
                target = staging / filename; shutil.copyfile(files[name], target); target.chmod(0o600)
                if digest(target) != hashes[name]: raise RuntimeError('copy_verification_failed')
            media = check_media(staging / 'output.mp4', staging / 'output-nobgm.mp4', records, run)
            if any(digest(p) != hashes[name] for name, p in files.items()): raise RuntimeError('original_changed')
            now = time.time()
            status = {'id': id, 'owner': owner, 'projectId': project, 'title': title, 'account': account,
                      'key': 'linked:' + key, 'signature': signature, 'state': 'succeeded',
                      'workflowVersion': VERSION, 'sourceKind': 'linked_local_result', 'phase': 'linked_completed',
                      'fileName': files['source'].name, 'size': files['source'].stat().st_size, 'received': files['source'].stat().st_size,
                      'sourceSha256': hashes['source'], 'sourceFirstChunkSha256': hashlib.sha256(files['source'].read_bytes()[:512 * 1024]).hexdigest(),
                      'outputFileName': files['main'].name, 'outputSha256': hashes['main'], 'outputNoBgmSha256': hashes['nobgm'],
                      'outputBytes': files['main'].stat().st_size, 'variants': ['main', 'nobgm'], 'qualityStatus': 'needs_review',
                      'requirement': '', 'summary': '本机已完成双版成片的持久关联；不是网页重新剪辑。待本人审片，未发布、未回写飞书。',
                      'unmet': [], 'createdAt': now, 'updatedAt': now, 'finishedAt': None, 'linkedAt': now, 'error': None,
                      'media': media, 'provenance': {'paths': {k: str(p) for k, p in files.items()}, 'hashes': hashes, 'review': 'review.md'}}
            write_json(staging / 'verification.json', {'media': media, 'originalHashesBeforeAndAfter': hashes, 'commands': records})
            write_json(staging / 'status.json', status)
            # No partial or unverified result ever enters the visible store.
            staging.rename(destination)
        receipt = {'id': id, 'projectId': project, 'state': 'succeeded', 'reused': False, 'resultPath': str(destination), 'hashes': hashes}
        write_json(audit / 'association.json', receipt)
        return receipt

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('config', 'project', 'title', 'source', 'main', 'nobgm', 'review', 'key', 'audit'): parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    print(json.dumps(link(json.loads(pathlib.Path(args.config).read_text()), args.project, args.title, '示例内容账号', args.source, args.main, args.nobgm, args.review, args.key, args.audit), ensure_ascii=False))
