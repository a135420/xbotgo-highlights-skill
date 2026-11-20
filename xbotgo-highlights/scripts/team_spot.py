"""Predict team-specific SoccerNet ball actions in a local video."""
import argparse
import csv
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

from team_setup import RUNTIME, REVISION

FPS = 12.5
WIDTH, HEIGHT = 796, 448
CLIP_SIZE, STEP = 100, 25
LABELS = ("PASS", "DRIVE", "HEADER", "HIGH PASS", "OUT", "CROSS", "THROW IN", "SHOT",
          "BALL PLAYER BLOCK", "PLAYER SUCCESSFUL TACKLE", "FREE KICK", "GOAL")


def load_model(torch, device):
    import timm
    from model.model import TDEEDModel
    from model.modules import FC2Layers

    config = RUNTIME / "upstream/config/SoccerNetBall/SoccerNetBall_baseline.json"
    args = SimpleNamespace(**json.loads(config.read_text()))
    args.crop_dim = None
    # The full checkpoint includes the backbone; skip its redundant ImageNet download.
    create_backbone = timm.create_model
    def local_backbone(name, **options):
        return create_backbone(name, **dict(options, pretrained=False))
    with patch.object(timm, "create_model", local_backbone):
        model = TDEEDModel.Impl(args)
    model._pred_fine = FC2Layers(model._feat_dim, [13, 18])
    model._double_head = True
    weights = torch.load(RUNTIME / "checkpoint_best.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(weights, strict=True)
    return model.eval().to(device)


def classify(model, frames, torch, device):
    from model.modules import process_double_head, process_predictionTeam
    x = torch.from_numpy(frames).permute(0, 3, 1, 2).unsqueeze(0).to(device)
    with torch.inference_mode():
        values, _ = model(x, inference=True)
        # The upstream team postprocessor creates CPU tensors.
        actions = process_double_head(values["im_feat"].cpu(), values["displ_feat"].cpu(), num_classes=13)
        return process_predictionTeam(actions, values["team_feat"].cpu())[0].numpy()


def decode_scores(video, model, torch, np, device):
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video), "-map", "0:v:0",
           "-vf", f"fps={FPS},scale={WIDTH}:{HEIGHT}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    frame_bytes = WIDTH * HEIGHT * 3
    # Five zero frames of leading context match the upstream evaluation dataset.
    pending = np.zeros((5, HEIGHT, WIDTH, 3), dtype=np.uint8)
    offset = -len(pending)
    outputs = []
    nframes = 0
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as process:
        try:
            while True:
                block = process.stdout.read((CLIP_SIZE-len(pending))*frame_bytes)
                count = len(block) // frame_bytes
                if len(block) % frame_bytes:
                    raise ValueError("解码得到不完整的视频帧")
                nframes += count
                extra = np.frombuffer(block, dtype=np.uint8).reshape(count, HEIGHT, WIDTH, 3)
                pending = np.concatenate([pending, extra])
                if len(pending) == 5 and not nframes:
                    break
                valid = len(pending)
                if valid < CLIP_SIZE:
                    # A full previous window already covered this suffix.
                    if outputs and count == 0:
                        break
                    pending = np.pad(pending, ((0, CLIP_SIZE-valid), (0,0), (0,0), (0,0)))
                scores = classify(model, pending, torch, device)
                outputs.append((offset, scores[:valid]))
                if len(outputs) % 30 == 0:
                    print(f"已处理约 {nframes/FPS:.0f} 秒录像", flush=True)
                if valid < CLIP_SIZE:
                    break
                pending = pending[STEP:]
                offset += STEP
        finally:
            process.stdout.close()
        if process.wait() or not nframes:
            raise RuntimeError("原片解码失败，或原片没有可用画面")
    scores = np.zeros((nframes, 25), dtype=np.float32)
    support = np.zeros(nframes, dtype=np.int32)
    for start, prediction in outputs:
        end = min(start + len(prediction), nframes)
        begin = max(0, start)
        region = prediction[begin - start:end - start]
        scores[begin:end] += region
        support[begin:end] += region.sum(axis=1) != 0
    return scores / np.maximum(support[:, None], 1)


def candidates(scores, threshold, np):
    events = []
    # Suppress adjacent peaks for the same action, choosing the stronger team head.
    for index, label in enumerate(LABELS):
        sides = scores[:, 1+index*2:3+index*2]
        best = sides.max(axis=1)
        available = np.ones(len(best), dtype=bool)
        for frame in np.argsort(-best, kind="stable"):
            if best[frame] < threshold:
                break
            if not available[frame]:
                continue
            events.append(dict(seconds=float(frame/FPS), label=label,
                               team=("left","right")[int(sides[frame].argmax())],
                               score=float(best[frame])))
            available[max(0, frame-6):frame+7] = False
    return sorted(events, key=lambda row: row["seconds"])


def report(video, events, frames, args):
    from bisect import bisect_right
    parent = args.out_dir or video.parent
    parent.mkdir(exist_ok=True, parents=True)
    output = Path(tempfile.mkdtemp(prefix="TDEED-", dir=parent))
    selected = []
    for row in events:
        if args.our_side:
            side = args.our_side
            if bisect_right(args.switch_at, row["seconds"]) % 2:
                side = {"left":"right", "right":"left"}[side]
            row["ours"] = row["team"] == side
        if row["label"] in ("SHOT", "GOAL") and row.get("ours", True):
            selected.append(row)
    data = dict(source=str(video), revision=REVISION, model="T-DEED team baseline",
                fps=FPS, frames=frames, seconds_origin="source_start", threshold=args.threshold,
                our_side=args.our_side, switch_at=args.switch_at, predictions=events)
    (output/"预测.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
    with (output/"球队动作.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["原片","源内秒数","动作候选","模型球队","我方","分数"])
        for row in events:
            writer.writerow([video.name,row["seconds"],row["label"],row["team"],row.get("ours","未知"),row["score"]])
    buttons = []
    for row in selected:
        t = row["seconds"]
        action = {"SHOT":"疑似射门","GOAL":"疑似进球"}[row["label"]]
        team = {"left":"左队","right":"右队"}[row["team"]]
        buttons.append(f'<p><button onclick="jump({t})">▶ {int(t)//60:02}:{t%60:05.2f}</button> {team} · {action}　{row["score"]:.3f}</p>')
    url = html.escape(quote(os.path.relpath(video, output.resolve())))
    page = f'''<!doctype html><meta charset="utf-8"><title>球队射门与进球候选</title>
<style>body{{max-width:960px;margin:auto;font:17px system-ui}}video{{width:100%}}button{{padding:8px}}</style>
<h1>{html.escape(video.name)}</h1><video controls preload="metadata" src="{url}"></video>
<p>射门与进球候选 {len(selected)} 处。点击从动作前 8 秒播放。球队与结果均需画面复核。</p>
{''.join(buttons)}<script>function jump(t){{let v=document.querySelector('video');v.currentTime=Math.max(0,t-8);v.play();v.scrollIntoView()}}</script>'''
    (output/"看回放.html").write_text(page, encoding="utf8")
    print(output/"看回放.html", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--our-side", choices=["left","right"], help="原片开始时我方对应的模型球队")
    parser.add_argument("--switch-at", action="append", type=float, default=[], metavar="SECONDS",
                        help="在此源内时间交换左右映射，可重复")
    parser.add_argument("--threshold", type=float, default=0.10, help="候选最低联合分数")
    parser.add_argument("--device", choices=["cpu","mps","cuda"], default="cpu")
    args = parser.parse_args()
    if not 0 < args.threshold <= 1:
        parser.error("--threshold 应在 (0,1] 区间")
    if args.switch_at and (not args.our_side or args.switch_at != sorted(set(args.switch_at)) or min(args.switch_at) < 0):
        parser.error("换边时间需要已知初始一方、非负且严格递增")
    video = args.video.resolve(strict=True)
    python = RUNTIME / "venv/bin/python"
    if not python.exists() or not (RUNTIME/"checkpoint_best.pt").is_file():
        parser.error("请先运行同目录的 team_setup.py")
    if Path(sys.prefix) != python.parent.parent:
        os.execv(str(python), [str(python), __file__, *sys.argv[1:]])
    sys.path.insert(0, str(RUNTIME/"upstream"))
    import torch
    import numpy as np
    model = load_model(torch, args.device)
    scores = decode_scores(video, model, torch, np, args.device)
    report(video, candidates(scores, args.threshold, np), len(scores), args)


if __name__ == "__main__":
    main()
