#!/usr/bin/env python3
"""多原片复核工作台：球门参考审核、事件台账、进度、短片和分类导出。"""
import argparse
import csv
import hashlib
import html
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timezone

from highlight_tool import parse_wall_clock_start, require_binary

VERSION = '1.2.0'
KINDS = ('shot', 'goal', 'save', 'catch', 'claim', 'distribution', 'attack')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def probe(path):
    return json.loads(run([require_binary('ffprobe'), '-v', 'error', '-show_format',
                           '-show_streams', '-of', 'json', str(path)]).stdout)


def fingerprint(path):
    st = path.stat()
    return dict(size=st.st_size, mtime_ns=st.st_mtime_ns)


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('时间和坐标必须是有限数值')
    return value


def box_valid(box):
    if len(box) != 4:
        raise ValueError('门框为归一化 x y width height 四个数')
    x, y, w, h = [finite(n) for n in box]
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > 1.000001 or y+h > 1.000001:
        raise ValueError('门框须位于画面内，坐标范围为0到1')
    return [x, y, w, h]


def source_by_id(data, sid):
    for s in data['sources']:
        if s['id'] == sid:
            return s
    raise ValueError('找不到原片：'+sid)


def point(source, at):
    at = finite(at)
    if not 0 <= at < source['duration']:
        raise ValueError('动作/参考时间超出原片范围')
    return at


def load(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise ValueError('不支持的 session 格式，请使用 init 创建新任务')
    for s in data['sources']:
        if fingerprint(Path(s['file'])) != s['fingerprint']:
            raise ValueError('原片发生变化，请新建任务并重新复核：'+s['file'])
    return data


def save(path, data):
    data['updated_at'] = now()
    atomic_json(path, data)


def init(args):
    files = [Path(p).expanduser().resolve() for p in args.inputs]
    if len(set(files)) != len(files) or any(not p.is_file() for p in files):
        raise ValueError('原片必须存在且不能重复')
    starts = {p: parse_wall_clock_start(p) for p in files}
    ordered = all(starts.values())
    files.sort(key=lambda p: (starts[p], p.name) if ordered else (p.name,))
    if args.session.exists():
        old = load(args.session)
        if [s['file'] for s in old['sources']] != [str(p) for p in files]:
            raise ValueError('已有任务的原片列表不同；请新建 session')
        print('复用已有任务，保留审核、事件和进度'); return
    sources = []
    for i, p in enumerate(files, 1):
        info = probe(p)
        if not any(s['codec_type'] == 'video' for s in info['streams']):
            raise ValueError('缺少视频轨：'+str(p))
        duration = finite(info['format']['duration'])
        if duration <= 0:
            raise ValueError('原片时长必须为正数')
        sources.append(dict(id=f'F{i:02d}', file=str(p), duration=duration,
                            fingerprint=fingerprint(p), reviewed_ranges=[]))
    data = dict(schema_version=1, tool_version=VERSION, created_at=now(),
                order_basis='filename_clock' if ordered else 'filename_requires_review',
                sources=sources, goal_references=[], events=[], history=[])
    save(args.session, data)
    print('已创建 '+str(args.session))


def frame(root, source, at):
    key = digest([source['fingerprint'], source['id'], at])[:20]
    dest = root/'frames'/f'{source["id"]}_{key}.jpg'
    if not dest.is_file():
        dest.parent.mkdir(exist_ok=True, parents=True)
        tmp = dest.with_suffix('.tmp.jpg')
        run([require_binary('ffmpeg'), '-v', 'error', '-y', '-ss', str(at), '-i', source['file'],
             '-frames:v', '1', '-vf', 'scale=1280:-2', str(tmp)])
        os.replace(tmp, dest)
    return dest.relative_to(root).as_posix()


def frames(args, data):
    source = source_by_id(data, args.source)
    for at in args.at:
        at = point(source, at)
        print(f'{source["id"]} {at:g}s: {frame(args.session.parent, source, at)}')


def reference(args, data):
    source = source_by_id(data, args.source)
    at = point(source, args.at)
    rid = args.id or f'G{len(data["goal_references"])+1:03d}'
    refs = data['goal_references']
    if any(r['id'] == rid for r in refs):
        raise ValueError('参考ID已存在；修改请在审核页重新标注并导入')
    refs.append(dict(id=rid, source=source['id'], at=at, end=args.end,
                     label=args.label, team=args.team, keeper=args.keeper,
                     box=box_valid(args.box), image=frame(args.session.parent, source, at),
                     status='pending', review=None))
    save(args.session, data)
    print('已保存待审核球门 '+rid)


def reference_snapshot(data):
    return digest(dict(sources=[{k: s[k] for k in ('id', 'file', 'fingerprint')}
                                for s in data['sources']], references=data['goal_references']))


def goals_page(args, data):
    if not data['goal_references']:
        raise ValueError('先查看 frames 并用 reference 录入两端球门；当前没有定位模型')
    template = Path(__file__).resolve().parent.parent/'assets'/'goal_review.html'
    payload = dict(snapshot=reference_snapshot(data), references=data['goal_references'])
    # JSON嵌入脚本前转义，避免文件名或标签中的HTML终止script。
    encoded = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    dest = args.session.parent/'goal_review.html'
    dest.write_text(template.read_text(encoding='utf-8').replace('__DATA__', encoded), encoding='utf-8')
    print(dest)


def import_goals(args, data):
    review = json.loads(Path(args.file).read_text(encoding='utf-8'))
    if review.get('snapshot') != reference_snapshot(data):
        raise ValueError('审核文件已过期，请重新生成审核页；现有记录未改动')
    refs = data['goal_references']
    decisions = review.get('references', [])
    if len(decisions) != len(refs) or len({r['id'] for r in decisions}) != len(refs):
        raise ValueError('审核参考列表不完整或ID重复')
    by_id = {r['id']: r for r in decisions}
    # 先完整校验，成功后一次性写入；审核文件不能写入事件/素材路径。
    for ref in refs:
        r = by_id.get(ref['id'], {})
        if r.get('status') not in ('approved', 'rejected', 'pending'):
            raise ValueError('审核状态无效')
        if r['status'] == 'approved' and not str(review.get('note', '')).strip():
            raise ValueError('确认球门时须填写审核说明')
        box_valid(r['box'])
    for ref in refs:
        r = by_id[ref['id']]
        if ref['box'] != box_valid(r['box']) or r['status'] != 'approved':
            for e in data['events']:
                if e['goal_ref'] == ref['id'] and e['status'] == 'confirmed':
                    data['history'].append(dict(action='invalidate_event_after_goal_review', previous=dict(e), at=now()))
                    e.update(status='uncertain', goal_status='unknown', goal_evidence='', updated_at=now())
        ref.update(box=box_valid(r['box']), status=r['status'],
                   review=dict(at=now(), note=review.get('note', ''), via='user_review_page'))
    data['history'].append(dict(action='import_goal_review', at=now(), snapshot=review['snapshot']))
    save(args.session, data)
    print('审核已导入；坐标仅对参考帧有效，不会当作全片固定坐标')


def event(args, data):
    source = source_by_id(data, args.source)
    at = point(source, args.at)
    ref = next((r for r in data['goal_references'] if r['id'] == args.goal_ref), None)
    if args.goal_ref != 'unknown' and (ref is None or ref['source'] != args.source):
        raise ValueError('事件须关联同一原片中的球门参考；定位未知时仍可录 uncertain 事件并填写 unknown')
    if args.status == 'confirmed' and (ref is None or ref['status'] != 'approved'):
        raise ValueError('先完成球门审核，再确认事件')
    if not args.reason.strip():
        raise ValueError('须填写动作复核依据')
    if args.goal_status == 'confirmed' and (args.status != 'confirmed' or not (args.goal_evidence or '').strip()):
        raise ValueError('确认进球需 confirmed 事件及独立的 --goal-evidence 球路/门线证据说明')
    eid = args.id or f'E{len(data["events"])+1:04d}'
    old = next((r for r in data['events'] if r['id'] == eid), None)
    r = dict(id=eid, source=args.source, at=at, kind=args.kind, status=args.status,
             goal_status=args.goal_status, goal_evidence=args.goal_evidence or '',
             goal_ref=args.goal_ref, team=args.team, player=args.player,
             reason=args.reason, before=finite(args.before), after=finite(args.after),
             updated_at=now())
    if r['before'] < 0 or r['after'] < 0 or r['before']+r['after'] <= 0:
        raise ValueError('片段前后时长须非负，合计大于0')
    if old:
        data['history'].append(dict(action='edit_event', previous=old, at=now()))
        data['events'][data['events'].index(old)] = r
    else:
        data['events'].append(r)
    save(args.session, data)
    print('已保存事件 '+eid)


def merged_ranges(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1]+0.001:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([start, end])
    return result


def coverage(args, data):
    s = source_by_id(data, args.source)
    start, end = finite(args.start), finite(args.end)
    if not 0 <= start < end <= s['duration']:
        raise ValueError('复核范围超出原片')
    s['reviewed_ranges'].append(dict(start=start, end=end, note=args.note, at=now()))
    save(args.session, data)


def status_report(data):
    result = []
    for s in data['sources']:
        ranges = merged_ranges([(r['start'], r['end']) for r in s['reviewed_ranges']])
        seconds = sum(b-a for a, b in ranges)
        gaps, end = [], 0.0
        for a, b in ranges:
            if a > end+0.001:
                gaps.append([end, a])
            end = b
        if end < s['duration']-0.001:
            gaps.append([end, s['duration']])
        result.append(dict(source=s['id'], file=s['file'], duration=s['duration'],
                           reviewed_seconds=seconds, complete=seconds >= s['duration']-0.001,
                           unreviewed_ranges=gaps,
                           events=sum(e['source'] == s['id'] for e in data['events']),
                           approved_references=sum(r['source'] == s['id'] and r['status'] == 'approved'
                                                   for r in data['goal_references'])))
    return result


def bounds(data, e):
    s = source_by_id(data, e['source'])
    return max(0, e['at']-e['before']), min(s['duration'], e['at']+e['after'])


def encode(root, source, start, end, preview=False):
    width, height, crf = (1280, 720, 25) if preview else (1920, 1080, 20)
    key = digest([VERSION, source['file'], source['fingerprint'], start, end, preview])[:24]
    dest = root/'cache'/f'{key}.mp4'
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp.mp4')
    audio = any(s['codec_type'] == 'audio' for s in probe(source['file'])['streams'])
    cmd = [require_binary('ffmpeg'), '-v', 'error', '-y', '-ss', str(start), '-i', source['file']]
    if not audio:
        cmd += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    cmd += ['-t', str(end-start), '-map', '0:v:0', '-map', '0:a:0' if audio else '1:a:0',
            '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30',
            '-c:v', 'libx264', '-threads', '4', '-preset', 'veryfast', '-crf', str(crf),
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', '-ac', '2',
            '-video_track_timescale', '15360', '-movflags', '+faststart', str(tmp)]
    run(cmd)
    os.replace(tmp, dest)
    return dest


def preview_page(args, data):
    root = args.session.parent
    rows = []
    order = {s['id']: i for i, s in enumerate(data['sources'])}
    for e in sorted(data['events'], key=lambda e: (order[e['source']], e['at'])):
        if e['status'] == 'rejected':
            continue
        start, end = bounds(data, e)
        clip = encode(root, source_by_id(data, e['source']), start, end, preview=True)
        info = html.escape(f'{e["id"]} {e["source"]} {start:.2f}–{end:.2f}s | {e["kind"]} / {e["status"]} / goal={e["goal_status"]} | {e["reason"]}')
        rows.append(f'<article><h2>{info}</h2><video controls preload="metadata" src="{clip.relative_to(root).as_posix()}"></video><p>动作位于片段内 {e["at"]-start:.2f} 秒 · 球门参考 {html.escape(e["goal_ref"])}</p><button onclick="step(this,-1)">前一帧</button><button onclick="step(this,1)">后一帧</button><select onchange="this.closest(\'article\').querySelector(\'video\').playbackRate=Number(this.value)"><option value="1">原速</option><option value="0.5">半速</option><option value="0.25">四分之一速</option></select></article>')
    dest = root/'event_review.html'
    dest.write_text('<!doctype html><meta charset="utf-8"><title>事件连续短片复核</title><style>body{font:16px system-ui;margin:24px;background:#14202b;color:white}video{width:min(100%,1000px)}article{margin-bottom:40px}button,select{padding:10px}h2{font-size:17px}</style><h1>事件连续短片复核</h1><p>短片由台账生成；逐帧按钮按30fps步进。修改结论后用 event --id 更新台账，再导出。</p>'+''.join(rows)+'<script>function step(b,d){let v=b.closest("article").querySelector("video");v.pause();v.currentTime=Math.max(0,Math.min(v.duration,v.currentTime+d/30))}</script>', encoding='utf-8')
    print(dest)


def selected(e, args):
    if e['status'] != args.status or (args.team and e['team'] != args.team) or (args.player and e['player'] != args.player):
        return False
    if args.collection == 'goals':
        return e['goal_status'] == 'confirmed' if args.status == 'confirmed' else e['kind'] == 'goal' and e['goal_status'] == 'unknown'
    if args.collection == 'shots':
        return e['kind'] in ('shot', 'goal')
    if args.collection == 'keeper':
        return e['kind'] in ('save', 'catch', 'claim', 'distribution')
    return True


def export(args, data):
    report = status_report(data)
    if not all(r['complete'] for r in report) and not args.allow_partial:
        raise ValueError('仍有未复核原片范围；先用 status 查看。局部导出请显式加 --allow-partial')
    events = [e for e in data['events'] if selected(e, args)]
    if not events:
        raise ValueError('没有匹配的事件，不生成空合集')
    refs = {r['id']: r for r in data['goal_references']}
    for e in events:
        if args.status == 'confirmed' and refs[e['goal_ref']]['status'] != 'approved':
            raise ValueError('入选事件的球门参考尚未通过审核：'+e['id'])
    order = {s['id']: i for i, s in enumerate(data['sources'])}
    groups = []
    for e in sorted(events, key=lambda e: (order[e['source']], bounds(data, e)[0])):
        start, end = bounds(data, e)
        if groups and groups[-1]['source'] == e['source'] and start <= groups[-1]['end']:
            groups[-1]['end'] = max(end, groups[-1]['end']); groups[-1]['events'].append(e['id'])
        else:
            groups.append(dict(source=e['source'], start=start, end=end, events=[e['id']]))
    out = Path(args.out_dir).expanduser().resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('导出目录非空，请使用新的版本目录；缓存会自动复用')
    out.mkdir(parents=True, exist_ok=True)
    # 使用简单相对文件名合并，不把任意素材文件名拼入ffconcat语法。
    import shutil
    cursor = 0.0
    for i, g in enumerate(groups, 1):
        s = source_by_id(data, g['source'])
        clip = encode(args.session.parent, s, g['start'], g['end'])
        name = f'clip_{i:03d}.mp4'
        shutil.copyfile(clip, out/name)
        duration = float(probe(out/name)['format']['duration'])
        g.update(file=name, compilation_start=cursor, duration=duration, original=s['file'])
        cursor += duration
    listing = out/'concat.txt'
    listing.write_text(''.join(f"file '{g['file']}'\n" for g in groups), encoding='utf-8')
    dest = out/'highlights.mp4'
    run([require_binary('ffmpeg'), '-v', 'error', '-y', '-f', 'concat', '-safe', '1', '-i', str(listing),
         '-c', 'copy', '-movflags', '+faststart', str(dest)])
    run([require_binary('ffmpeg'), '-v', 'error', '-xerror', '-threads', '2', '-i', str(dest), '-f', 'null', '-'])
    with (out/'index.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(['片段', '原片', '开始秒', '结束秒', '合集开始秒', '事件ID'])
        for g in groups:
            w.writerow([g['file'], g['original'], g['start'], g['end'], g['compilation_start'], ','.join(g['events'])])
    result = dict(collection=args.collection, status=args.status, partial=not all(r['complete'] for r in report),
                  coverage=report, events=events, segments=groups, decode_ok=True,
                  output_probe=probe(dest), limitations='抽样复核可能遗漏；完整解码仅证明文件可用，不证明进球判断准确。')
    atomic_json(out/'export.json', result)
    print(json.dumps(dict(output=str(dest), events=len(events), segments=len(groups),
                          duration=result['output_probe']['format']['duration'], partial=result['partial']), ensure_ascii=False))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', required=True, type=lambda s: Path(s).expanduser().resolve())
    sub = p.add_subparsers(dest='command', required=True)
    q = sub.add_parser('init'); q.add_argument('inputs', nargs='+')
    q = sub.add_parser('frames'); q.add_argument('--source', required=True); q.add_argument('--at', nargs='+', type=float, required=True)
    q = sub.add_parser('reference')
    q.add_argument('--id'); q.add_argument('--source', required=True); q.add_argument('--at', type=float, required=True)
    q.add_argument('--end', choices=('A', 'B', 'unknown'), required=True)
    q.add_argument('--box', nargs=4, type=float, required=True); q.add_argument('--label', required=True)
    q.add_argument('--team', default='unknown'); q.add_argument('--keeper', default='unknown')
    sub.add_parser('goals-page')
    q = sub.add_parser('import-goals'); q.add_argument('--file', required=True)
    q = sub.add_parser('event')
    q.add_argument('--id'); q.add_argument('--source', required=True); q.add_argument('--at', required=True, type=float)
    q.add_argument('--kind', choices=KINDS, required=True)
    q.add_argument('--status', choices=('confirmed', 'uncertain', 'rejected'), default='uncertain')
    q.add_argument('--goal-status', choices=('confirmed', 'not_goal', 'unknown'), default='unknown')
    q.add_argument('--goal-evidence'); q.add_argument('--goal-ref', required=True)
    q.add_argument('--team', default='unknown'); q.add_argument('--player', default='unknown')
    q.add_argument('--reason', required=True); q.add_argument('--before', type=float, default=12); q.add_argument('--after', type=float, default=8)
    q = sub.add_parser('coverage'); q.add_argument('--source', required=True)
    q.add_argument('--start', type=float, required=True); q.add_argument('--end', type=float, required=True); q.add_argument('--note', required=True)
    sub.add_parser('status'); sub.add_parser('preview')
    q = sub.add_parser('export'); q.add_argument('--out-dir', required=True)
    q.add_argument('--collection', choices=('all', 'shots', 'goals', 'keeper'), default='all')
    q.add_argument('--status', choices=('confirmed', 'uncertain'), default='confirmed')
    q.add_argument('--team'); q.add_argument('--player'); q.add_argument('--allow-partial', action='store_true')
    return p


def main():
    p = parser(); args = p.parse_args()
    try:
        if args.command == 'init':
            init(args); return
        data = load(args.session)
        if args.command == 'status':
            print(json.dumps(status_report(data), ensure_ascii=False, indent=2)); return
        commands = {'frames': frames, 'reference': reference, 'goals-page': goals_page,
                    'import-goals': import_goals, 'event': event, 'coverage': coverage,
                    'preview': preview_page, 'export': export}
        commands[args.command](args, data)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        message = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        p.exit(1, '错误：'+str(message)+'\n')


if __name__ == '__main__':
    main()
