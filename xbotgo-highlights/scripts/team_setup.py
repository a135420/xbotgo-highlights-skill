"""Prepare the local SoccerNet Team Ball Action Spotting baseline."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

RUNTIME = Path.home() / ".local/share/xbotgo-highlights/team-v1"
REVISION = "091fed2fc35c33f7489f3596958a2fe385e37d65"
CHECKSUM = "ed7c558d575eba1918be8f67813571723962eeff807dba68a12a2d794dd1c72a"
DOWNLOAD = "https://drive.usercontent.google.com/download?id=1Dp2tqDIrNwLmfuUJPPhpz1db7DZjxIzd&export=download&confirm=t"


def verify(path):
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == CHECKSUM


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, help="Use a local copy of the official checkpoint")
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    upstream = RUNTIME / "upstream"
    if not upstream.exists():
        with tempfile.TemporaryDirectory(dir=RUNTIME) as tmp:
            stage = Path(tmp) / "source"
            subprocess.run(["git", "init", "-q", str(stage)], check=True)
            subprocess.run(["git", "-C", str(stage), "fetch", "--depth=1",
                            "https://github.com/SoccerNet/sn-teamspotting.git", REVISION], check=True)
            subprocess.run(["git", "-C", str(stage), "checkout", "-q", "--detach", "FETCH_HEAD"], check=True)
            stage.rename(upstream)
    revision = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise SystemExit(f"上游代码版本不匹配：{upstream}")
    checkpoint = RUNTIME / "checkpoint_best.pt"
    if not verify(checkpoint):
        with tempfile.TemporaryDirectory(dir=RUNTIME) as tmp:
            stage = Path(tmp) / checkpoint.name
            if args.weights:
                shutil.copyfile(args.weights, stage)
            else:
                subprocess.run(["curl", "-fLsS", "--retry", "2", DOWNLOAD, "-o", str(stage)], check=True)
            if not verify(stage):
                raise SystemExit("权重校验失败，请使用官方 checkpoint_best.pt")
            stage.replace(checkpoint)
    python = RUNTIME / "venv/bin/python"
    if not python.exists():
        subprocess.run(["uv", "venv", "-p", "3.11", str(RUNTIME / "venv")], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(python), "torch==2.5.0",
                    "torchvision==0.20.0", "timm==1.0.11", "tqdm", "numpy==2.1.2"], check=True)
    print(RUNTIME)


if __name__ == "__main__":
    main()
