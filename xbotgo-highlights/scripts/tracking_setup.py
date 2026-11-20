"""Prepare the SoccerNet no-ground-truth ByteTrack benchmark for local footage."""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

RUNTIME = Path.home() / ".local/share/xbotgo-highlights/tracking-v1"
REVISION = "d1bf0191adff59bc8fcfeaa0b33d3d1642552a99"
SOURCE_URL = f"https://codeload.github.com/ifzhang/ByteTrack/tar.gz/{REVISION}"
SOURCE_SHA = "a04446567ebd13bb611849029222cd4edcc9ec009bd6f1e92fa5f4281c95359c"
WEIGHT_URL = ("https://drive.usercontent.google.com/download?"
              "id=1HX2_JpMOjOIj1Z9rJjoet9XNy_cCAs5U&export=download&confirm=t&asset=bytetrack")
WEIGHT_SHA = "021d7bc47fe20ae690007454cd2192df6237c2f4446ae92737a79523de89de64"
WEIGHT = "bytetrack_x_mot20.tar"
PACKAGES = ["torch==2.15.0", "torchvision==0.29.2", "opencv-python==5.1.0.95",
            "numpy==2.4.6", "scipy==1.17.1", "lap==0.5.14", "loguru==0.7.3",
            "cython-bbox==0.1.5", "thop==0.1.1.post2209072238"]


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def obtain(url, expected, target, seed=None):
    if target.exists() and digest(target) == expected:
        return
    with tempfile.TemporaryDirectory(dir=RUNTIME) as work:
        temp = Path(work) / target.name
        if seed:
            shutil.copyfile(seed, temp)
        else:
            subprocess.run(["curl", "-fLsS", "--retry", "2", url, "-o", str(temp)], check=True)
        if digest(temp) != expected:
            raise SystemExit(f"下载校验失败：{target.name}")
        temp.replace(target)


def install_source(archive):
    """Keep upstream modules, MIT notice and two dependency compatibility edits locally."""
    dest = RUNTIME / "ByteTrack"
    if (dest / "revision").is_file() and (dest / "revision").read_text() == REVISION:
        return
    with tempfile.TemporaryDirectory(dir=RUNTIME) as work:
        staged = Path(work) / "ByteTrack"
        with tarfile.open(archive) as bundle:
            for item in bundle:
                relative = Path(item.name).relative_to(f"ByteTrack-{REVISION}")
                if relative != Path("LICENSE") and not (relative.parts[:1] == ("yolox",) and relative.suffix == ".py"):
                    continue
                if not item.isfile() or ".." in relative.parts:
                    continue
                path = staged / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(item).read()
                if relative.name in {"byte_tracker.py", "matching.py"}:
                    source = re.sub(rb"\bnp\.float\b", b"float", source)
                if relative.name == "yolo_head.py":
                    source = source.replace(b"dim=1).type(dtype)", b"dim=1).to(outputs)")
                path.write_bytes(source)
        (staged / "revision").write_text(REVISION)
        staged.rename(dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, help="复用本地官方 bytetrack_x_mot20.tar")
    parser.add_argument("--archive", type=Path, help="复用固定提交的 ByteTrack 源码包")
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    archive = RUNTIME / "source.tgz"
    obtain(SOURCE_URL, SOURCE_SHA, archive, args.archive)
    install_source(archive)
    obtain(WEIGHT_URL, WEIGHT_SHA, RUNTIME / WEIGHT, args.weights)
    python = RUNTIME / "venv/bin/python"
    if not python.is_file():
        subprocess.run(["uv", "venv", "-p", "3.11", str(python.parent.parent)], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(python), *PACKAGES], check=True)
    print(RUNTIME)


if __name__ == "__main__":
    main()
