#!/usr/bin/env python3
"""为全时间轴复核生成分页图；不自动判断空场或射门。"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile

from highlight_tool import parse_wall_clock_start, probe_duration, require_binary


def read_frame(pipe, size):
    buf = bytearray()
    while len(buf) < size:
        block = pipe.read(size - len(buf))
        if not block:
            break
        buf.extend(block)
    if buf and len(buf) != size:
        raise RuntimeError('抽样帧不完整')
    return bytes(buf)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('inputs', nargs='+', help='本次原视频；可由 shell 展开 MP4 列表')
    p.add_argument('--out-dir', required=True)
    p.add_argument('--interval', type=float, default=2, help='抽样间隔秒数')
    args = p.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0:
        p.error('interval 必须为有限正数')
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        p.error('请先在独立 Python 环境安装 Pillow，见 references/workflow.md')
    files = [Path(x).expanduser().resolve() for x in args.inputs]
    if any(not f.is_file() for f in files) or len(set(files)) != len(files):
        p.error('输入必须是存在且不重复的原视频文件')
    starts = {f: parse_wall_clock_start(f) for f in files}
    clock_order = all(starts.values())
    files.sort(key=(lambda f: (starts[f], f.name)) if clock_order else (lambda f: f.name))
    out = Path(args.out_dir).expanduser().resolve()
    if out.exists() and any(out.iterdir()):
        p.error('请使用空的输出目录，避免覆盖已有复核记录')
    out.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_binary('ffmpeg')
    manifest = dict(interval=args.interval, order_basis='filename_clock' if clock_order else 'filename_requires_review', sources=[], pages=[])
    w, h, columns, cells = 480, 270, 6, 48
    for index, src in enumerate(files, 1):
        duration = probe_duration(src)
        manifest['sources'].append(dict(index=index, file=str(src), duration=duration, wall_clock_start=str(starts[src]) if starts[src] else None))
        frames, times, page_no = [], [], 0
        def flush():
            nonlocal page_no
            if not frames:
                return
            page_no += 1
            sheet = Image.new('RGB', (w * columns, math.ceil(len(frames) / columns) * (h + 25)), '#101820')
            draw = ImageDraw.Draw(sheet)
            for j, (frame, t) in enumerate(zip(frames, times)):
                x, y = j % columns * w, j // columns * (h + 25)
                sheet.paste(frame, (x, y + 25))
                draw.text((x + 8, y + 5), f'F{index} | {int(t)//60:02}:{t%60:06.3f} ({t:.3f}s)', fill='white')
            name = f'F{index:02}_{page_no:03}.jpg'
            sheet.save(out / name, quality=90)
            manifest['pages'].append(dict(source=index, page=page_no, image=name, sample_seconds=list(times), reviewed=False))
            frames.clear(); times.clear()
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(src), '-an', '-vf', f'fps=1/{args.interval}:start_time=0:round=up,scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'], stdout=subprocess.PIPE, stderr=errors)
            n, last = 0, -1.0
            try:
                while True:
                    raw = read_frame(proc.stdout, w * h * 3)
                    if not raw:
                        break
                    t = n * args.interval
                    n += 1
                    if t >= duration:
                        continue
                    frames.append(Image.frombytes('RGB', (w, h), raw));times.append(t);last=t
                    if len(frames) == cells:
                        flush()
                code = proc.wait()
                if code:
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode(errors='replace'))
            finally:
                proc.stdout.close()
                if proc.poll() is None:
                    proc.kill();proc.wait()
        # 短尾部仍提供独立样本，不将不足一个间隔的尾段直接漏掉。
        tail = max(0.0, duration - 0.1)
        if tail > last + 0.05:
            r = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-ss', str(tail), '-i', str(src), '-frames:v', '1', '-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'], check=True, capture_output=True)
            if len(r.stdout) != w * h * 3:
                raise RuntimeError(f'无法读取尾帧：{src.name}')
            frames.append(Image.frombytes('RGB', (w, h), r.stdout));times.append(tail)
        flush()
        print(f'完成 F{index}: {src.name}，{page_no}页', flush=True)
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('时间轴材料已生成；请查看连续画面后记录复核结论。')


if __name__ == '__main__':
    main()
