"""Generate local SoccerNet CALF event labels and a seekable video review page."""
import argparse
import csv
import html
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
from urllib.parse import quote
from calf_setup import RUNTIME

LABELS = ("Penalty", "Kick-off", "Goal", "Substitution", "Offside", "Shots on target",
          "Shots off target", "Clearance", "Ball out of play", "Throw-in", "Foul",
          "Indirect free-kick", "Direct free-kick", "Corner", "Yellow card", "Red card",
          "Yellow->red card")

def extract(video, np, keras, torch):
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    net = keras.applications.ResNet152(include_top=False, pooling="avg",
                                        weights=str(RUNTIME / "resnet.h5")).to(device)
    command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video), "-an",
               "-vf", "fps=2,scale=-2:224,crop=224:224", "-pix_fmt", "bgr24",
               "-f", "rawvideo", "pipe:1"]
    features = []
    frame_bytes = 224 * 224 * 3
    with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
        try:
            with torch.inference_mode():
                while block := process.stdout.read(16 * frame_bytes):
                    frames = np.frombuffer(block, dtype=np.uint8).reshape(-1,224,224,3)
                    inputs = keras.applications.resnet.preprocess_input(frames.astype("float32"))
                    tensor = torch.from_numpy(np.ascontiguousarray(inputs)).to(device)
                    features.append(net(tensor, training=False).cpu().numpy())
        finally:
            process.stdout.close()
        if process.wait():
            raise RuntimeError("视频解码失败")
    if not features:
        raise ValueError("视频未产生采样帧")
    return np.concatenate(features)

def predict(features, np, torch):
    from model import ContextAwareModel
    with (RUNTIME / "pca_512_TF2.pkl").open("rb") as stream:
        pca = pickle.load(stream)
    with (RUNTIME / "average_512_TF2.pkl").open("rb") as stream:
        average = pickle.load(stream)
    reduced = (features - average - pca.mean_) @ pca.components_.T
    if pca.whiten:
        reduced /= np.sqrt(pca.explained_variance_)
    tensor = torch.from_numpy(reduced.astype("float32"))
    model = ContextAwareModel(num_classes=17, num_detections=15)
    checkpoint = torch.load(RUNTIME / "model.pth.tar", map_location="cpu", weights_only=True)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    # CALF uses 240-frame windows with 40 context frames on each inner edge.
    size = len(tensor)
    last = max(0, size - 240)
    starts = list(range(0, last, 160)) + [last]
    scores = np.full((size, len(LABELS)), -1.0)
    with torch.inference_mode():
        for offset in starts:
            indices = (torch.arange(240) + offset).clamp(max=size-1)
            _, predictions = model(tensor[indices][None,None])
            left = 0 if offset == 0 else 40
            right = min(size-offset, 240 if offset == last else 200)
            scores[offset+left:offset+right] = -1
            for proposal in predictions[0]:
                frame = int(proposal[1] * 239)
                if left <= frame < right:
                    scores[offset+frame, int(proposal[2:].argmax())] = float(proposal[0])
    events = []
    for col, label in enumerate(LABELS):
        values = scores[:,col].copy()
        while values.max() >= 0:
            frame = int(values.argmax())
            events.append(dict(seconds=frame/2, label=label, score=float(values[frame])))
            values[max(0,frame-20):frame+20] = -1
    return sorted(events, key=lambda row: row["seconds"])

def report(video, parent, events, shots):
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="CALF-", dir=parent))
    labels = {"Goal":"疑似进球"}
    if shots:
        labels.update({"Shots on target":"疑似射正", "Shots off target":"疑似射偏"})
    selected = [e for e in events if e["label"] in labels]
    (output / "预测.json").write_text(json.dumps(dict(source=str(video), fps=2, events=events),
                                                ensure_ascii=False, indent=2), encoding="utf8")
    with (output / "候选.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["原片","源内秒数","模型候选","分数"])
        for e in selected:
            writer.writerow([video.name,e["seconds"],labels[e["label"]],e["score"]])
    buttons = []
    for e in selected:
        t = e["seconds"]
        buttons.append(f'<p><button onclick="jump({t})">▶ {int(t)//60:02}:{t%60:04.1f}</button> {labels[e["label"]]}　{e["score"]:.3f}</p>')
    url = html.escape(quote(os.path.relpath(video, output.resolve())))
    page = f'''<!doctype html><meta charset="utf-8"><title>比赛事件复核</title>
<style>body{{max-width:960px;margin:auto;font:18px system-ui}}video{{width:100%}}button{{padding:8px}}</style>
<h1>{html.escape(video.name)}</h1><video controls preload="metadata" src="{url}"></video>
<p>共 {len(selected)} 个模型候选。点击时间，从动作前 10 秒开始播放。</p>{''.join(buttons)}
<script>function jump(t){{let v=document.querySelector('video');v.currentTime=Math.max(0,t-10);v.play();v.scrollIntoView()}}</script>'''
    (output / "看回放.html").write_text(page, encoding="utf8")
    print(output / "看回放.html", flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--shots", action="store_true", help="同时显示射正和射偏候选")
    args = parser.parse_args()
    video = args.video.resolve(strict=True)
    python = RUNTIME / "venv/bin/python"
    if not python.exists():
        parser.error("请先运行同目录 calf_setup.py")
    if Path(sys.prefix) != python.parent.parent:
        os.execv(str(python), [str(python), __file__, *sys.argv[1:]])
    os.environ["KERAS_BACKEND"] = "torch"
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    import keras
    import numpy as np
    import torch
    sys.path.insert(0, str(RUNTIME))
    features = extract(video, np, keras, torch)
    report(video, args.out_dir or video.parent, predict(features, np, torch), args.shots)

if __name__ == "__main__":
    main()
