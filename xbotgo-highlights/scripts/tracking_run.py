"""Track people in a local soccer MP4 with SoccerNet's YOLOX-X + ByteTrack baseline."""
import argparse
import csv
import html
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

from tracking_setup import RUNTIME, REVISION, WEIGHT

SIZE = (896, 1600)
CHUNK = 30


def metadata(video):
    raw = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                   "-show_entries", "stream=width,height:stream_side_data=rotation:format=duration",
                                   "-of", "json", str(video)])
    data = json.loads(raw)
    stream = data["streams"][0]
    width, height = stream["width"], stream["height"]
    rotation = next((r["rotation"] for r in stream.get("side_data_list", []) if "rotation" in r), 0)
    if abs(rotation) % 180 == 90:
        width, height = height, width
    return width, height, float(data["format"]["duration"])


def load_detector(torch, device):
    from yolox.models import YOLOX, YOLOPAFPN, YOLOXHead
    model = YOLOX(YOLOPAFPN(1.33, 1.25), YOLOXHead(1, 1.25))
    for layer in model.modules():
        if isinstance(layer, torch.nn.BatchNorm2d):
            layer.eps = 1e-3
            layer.momentum = 0.03
    checkpoint = torch.load(RUNTIME / WEIGHT, map_location="cpu", weights_only=True)
    model.load_state_dict(checkpoint["model"])
    return model.eval().to(device)


def write_chunk(directory, index, frames):
    data = json.dumps(frames, separators=(",", ":"))
    (directory / f"boxes-{index}.js").write_text(f"loadBoxes({index},{data})", encoding="utf-8")


def track(args, directory, width, height, duration):
    import numpy as np
    import torch
    from yolox.tracker.byte_tracker import BYTETracker
    from yolox.utils import postprocess

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    detector = load_detector(torch, device)
    config = SimpleNamespace(track_thresh=.5, track_buffer=30, match_thresh=.8, mot20=False)
    tracker = BYTETracker(config, frame_rate=args.fps)
    net_h, net_w = SIZE
    scale = min(net_w / width, net_h / height)
    view_w, view_h = max(1, int(width * scale)), max(1, int(height * scale))
    vf = (f"fps={args.fps},scale={view_w}:{view_h},"
          f"pad={net_w}:{net_h}:0:0:color=0x727272")
    command = ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(args.start), "-i", str(args.video),
               "-t", str(duration), "-map", "0:v:0", "-vf", vf, "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    mean = torch.tensor([.485, .456, .406], device=device).view(1,3,1,1)
    std = torch.tensor([.229, .224, .225], device=device).view(1,3,1,1)
    frames, summary = [], {}
    frame_count = 0
    per_chunk = args.fps * CHUNK
    with (directory / "tracks.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["sample","source_seconds","track_id","x","y","width","height","score"])
        with subprocess.Popen(command, stdout=subprocess.PIPE) as reader:
            try:
                with torch.inference_mode():
                    while block := reader.stdout.read(net_w * net_h * 3):
                        frame = np.frombuffer(block, dtype=np.uint8).reshape(net_h,net_w,3)
                        x = torch.from_numpy(frame.copy()).permute(2,0,1).unsqueeze(0).to(device)
                        prediction = detector((x.float()/255.0 - mean)/std)
                        detected = postprocess(prediction.cpu(), 1, .001, .7)[0]
                        if detected is None:
                            detected = np.empty((0,5), dtype=np.float32)
                        targets = tracker.update(detected, [height,width], SIZE)
                        boxes = []
                        seconds = args.start + frame_count / args.fps
                        for target in targets:
                            x0,y0,w,h = map(float,target.tlwh)
                            if h <= 0 or w*h <= 10 or w/h > 1.6:
                                continue
                            x1,y1 = min(width,round(x0+w)),min(height,round(y0+h))
                            x0,y0 = max(0,round(x0)),max(0,round(y0))
                            if x1 <= x0 or y1 <= y0:
                                continue
                            ident = int(target.track_id)
                            w,h = x1-x0,y1-y0
                            boxes.append([ident,x0,y0,w,h])
                            writer.writerow([frame_count,round(seconds,4),ident,x0,y0,w,h,
                                             round(float(target.score),4)])
                            entry = summary.setdefault(ident,[seconds,seconds,0])
                            entry[1] = seconds
                            entry[2] += 1
                        frames.append(boxes)
                        frame_count += 1
                        if frame_count % per_chunk == 0:
                            write_chunk(directory,frame_count//per_chunk-1,frames)
                            frames = []
                            print(f"已处理 {frame_count/args.fps:.0f} 秒", flush=True)
            finally:
                reader.stdout.close()
            if reader.wait():
                raise RuntimeError("原片解码失败")
    if not frame_count:
        raise ValueError("指定区间没有可解码的画面")
    if frames:
        write_chunk(directory,(frame_count-1)//per_chunk,frames)
    return dict(source=str(args.video), start=args.start, fps=args.fps, frames=frame_count,
                width=width, height=height, device=device, tracks=summary, revision=REVISION,
                weights=WEIGHT, analysis_duration=duration)


def review(directory, final, data):
    source = Path(data["source"])
    video_url = html.escape(quote(os.path.relpath(source, final)))
    options = []
    for ident,(start,end,count) in sorted(data["tracks"].items()):
        options.append(f'<option value="{ident}" data-first="{start}">#{ident}　{start:.1f}–{end:.1f} 秒（{count} 帧）</option>')
    template = Path(__file__).with_name("tracking_review.html").read_text(encoding="utf-8")
    for key,value in {"OPTIONS":"".join(options),"VIDEO":video_url,"START":str(data["start"]),
                      "FPS":str(data["fps"]),"FRAMES":str(data["frames"]),
                      "CHUNK":str(CHUNK),"WIDTH":str(data["width"]),
                      "HEIGHT":str(data["height"]),"NAME":html.escape(source.name)}.items():
        template = template.replace("{{"+key+"}}",value)
    (directory/"看轨迹.html").write_text(template,encoding="utf-8")
    (directory/"跟踪摘要.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("video", type=Path)
    p.add_argument("--start", type=float, default=0, help="原片起点，单位秒")
    p.add_argument("--duration", type=float, help="分析时长，默认到原片结尾")
    p.add_argument("--fps", type=int, default=8, help="每秒分析帧数，1 到 60")
    p.add_argument("--out-dir", type=Path, help="结果父目录，默认在原片旁")
    args = p.parse_args()
    args.video = args.video.resolve(strict=True)
    width,height,total = metadata(args.video)
    if not 1 <= args.fps <= 60 or not 0 <= args.start < total:
        p.error("检查 --fps 与 --start 的范围")
    duration = total-args.start if args.duration is None else min(args.duration,total-args.start)
    if not math.isfinite(duration) or duration <= 0:
        p.error("分析时长必须为正数")
    python = RUNTIME / "venv/bin/python"
    if not python.exists() or not (RUNTIME/WEIGHT).exists():
        p.error("请先运行同目录 tracking_setup.py")
    if Path(sys.prefix) != python.parent.parent:
        os.execv(str(python), [str(python), __file__, *sys.argv[1:]])
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    sys.path.insert(0, str(RUNTIME/"ByteTrack"))
    parent = (args.out_dir or args.video.parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    final = parent / ("ByteTrack-"+uuid4().hex[:12])
    with tempfile.TemporaryDirectory(dir=parent) as temp:
        directory = Path(temp)/"result"
        directory.mkdir()
        data = track(args,directory,width,height,duration)
        review(directory,final,data)
        directory.rename(final)
    print(final/"看轨迹.html")


if __name__ == "__main__":
    main()
