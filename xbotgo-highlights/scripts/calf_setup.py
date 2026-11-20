"""Install the optional CALF runtime outside the skill directory."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

RUNTIME = Path.home() / ".local/share/xbotgo-highlights/calf-v1"
BASE = "https://raw.githubusercontent.com/SoccerNet/sn-spotting/9842826f94e1419580a9d17219c11aca7225f7ce/"
ASSETS = {
    "model.py": (BASE + "Benchmarks/CALF/inference/model.py", "eb70367904ffa09d4297a107e47ab30a72c03aedf27d01d9260f894d46387132"),
    "model.pth.tar": (BASE + "Benchmarks/CALF/models/CALF_benchmark/model.pth.tar", "6a9befb8f30741da53756f89d2df675783ef8fc893457d1a920c1ed900399b4e"),
    "average_512_TF2.pkl": (BASE + "Features/average_512_TF2.pkl", "73c4b1c9e492929552e63edc87b8dc7cecc4fe6747d9106a5e4d56bc2760556e"),
    "pca_512_TF2.pkl": (BASE + "Features/pca_512_TF2.pkl", "dbe1f6e1dcf3a178715656aad19e25c3d90b9f0d71857d09d99c0066421fc810"),
    "resnet.h5": ("https://storage.googleapis.com/tensorflow/keras-applications/resnet/resnet152_weights_tf_dim_ordering_tf_kernels_notop.h5", "a4cdce05b519d72205245c6f7ffd368180c7065d46c7a6bfc27902f0278c828a"),
}

def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, help="Copy previously downloaded official assets")
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in ASSETS.items():
        dest = RUNTIME / name
        if dest.exists() and digest(dest) == expected:
            continue
        with tempfile.TemporaryDirectory(dir=RUNTIME) as directory:
            staged = Path(directory) / name
            if args.seed:
                shutil.copyfile(args.seed / name, staged)
            else:
                subprocess.run(["curl", "-fLsS", "--retry", "2", url, "-o", str(staged)], check=True)
            if digest(staged) != expected:
                raise SystemExit(f"模型文件校验失败：{name}")
            staged.replace(dest)
    env = RUNTIME / "venv"
    if not (env / "bin/python").exists():
        subprocess.run(["uv", "venv", "-p", "3.11", str(env)], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(env / "bin/python"),
                    "torch==2.15.0", "keras==3.15.1", "scikit-learn==1.9.1",
                    "numpy==2.4.6", "h5py==3.16.0"], check=True)
    print(RUNTIME)

if __name__ == "__main__":
    main()
