#!/usr/bin/env python3
"""
Local sports highlight generator.

Pipeline:
  match.mp4 -> highlights.csv -> highlights.mp4

The scanner uses audio energy as a practical first signal for highlights:
crowd noise, commentary spikes, whistles, and celebration usually create
short high-energy windows. It is intentionally dependency-light and only
requires Python 3 plus ffmpeg/ffprobe.
"""

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Iterable, List, Optional

from detection_logic import (
    PROFILES,
    Segment,
    Window,
    apply_profile_values,
    detect_segments,
)


CSV_FIELDS = [
    "start",
    "end",
    "duration",
    "score",
    "reason",
    "wall_clock_start",
    "wall_clock_end",
    "wall_clock_range",
]


def die(message: str, code: int = 1) -> None:
    print(f"错误：{message}", file=sys.stderr)
    raise SystemExit(code)


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        die(
            f"找不到 {name}。请先安装 ffmpeg，例如：brew install ffmpeg\n"
            f"安装后重新运行本工具。"
        )
    return path


def run(cmd: List[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            check=True,
            text=capture,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        die(f"命令执行失败：{' '.join(cmd)}\n{stderr}")


def probe_duration(video: Path) -> float:
    ffprobe = require_binary("ffprobe")
    result = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video),
        ]
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def extract_audio_windows(
    video: Path,
    sample_rate: int = 16000,
    window_seconds: float = 0.5,
) -> List[Window]:
    ffmpeg = require_binary("ffmpeg")
    samples_per_window = max(1, int(sample_rate * window_seconds))
    bytes_per_window = samples_per_window * 2
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None

    windows: List[Window] = []
    index = 0
    while True:
        chunk = proc.stdout.read(bytes_per_window)
        if not chunk:
            break
        if len(chunk) < 2:
            break

        sample_count = len(chunk) // 2
        total = 0.0
        for offset in range(0, sample_count * 2, 2):
            sample = int.from_bytes(chunk[offset : offset + 2], "little", signed=True)
            normal = sample / 32768.0
            total += normal * normal

        rms = math.sqrt(total / sample_count) if sample_count else 0.0
        db = 20.0 * math.log10(max(rms, 1e-8))
        start = index * window_seconds
        end = start + (sample_count / sample_rate)
        windows.append(Window(start=start, end=end, db=db))
        index += 1

    _, stderr = proc.communicate()
    if proc.returncode != 0:
        die(f"ffmpeg 提取音频失败：{stderr.decode(errors='replace').strip()}")

    return windows


def parse_wall_clock_start(video: Path) -> Optional[datetime]:
    match = re.search(
        r"(\d{4}-\d{2}-\d{2})[ _-](\d{2})[:-](\d{2})[:-](\d{2})",
        video.stem,
    )
    if not match:
        return None
    date_part, hour, minute, second = match.groups()
    return datetime.strptime(
        f"{date_part} {hour}:{minute}:{second}",
        "%Y-%m-%d %H:%M:%S",
    )


def wall_clock_fields(video_start: Optional[datetime], seg: Segment) -> dict:
    if not video_start:
        return {
            "wall_clock_start": "",
            "wall_clock_end": "",
            "wall_clock_range": "",
        }
    start = video_start + timedelta(seconds=seg.start)
    end = video_start + timedelta(seconds=seg.end)
    return {
        "wall_clock_start": start.strftime("%Y-%m-%d %H:%M:%S"),
        "wall_clock_end": end.strftime("%Y-%m-%d %H:%M:%S"),
        "wall_clock_range": f"{start:%H:%M:%S} - {end:%H:%M:%S}",
    }


def write_csv(
    path: Path,
    segments: Iterable[Segment],
    *,
    video_start: Optional[datetime] = None,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for seg in segments:
            row = {
                "start": f"{seg.start:.3f}",
                "end": f"{seg.end:.3f}",
                "duration": f"{seg.duration:.3f}",
                "score": f"{seg.score:.1f}",
                "reason": seg.reason,
            }
            row.update(wall_clock_fields(video_start, seg))
            writer.writerow(row)


def seconds_to_timecode(seconds: float) -> str:
    millis = int(round((seconds - int(seconds)) * 1000))
    whole = int(seconds)
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def write_json(
    path: Path,
    segments: Iterable[Segment],
    *,
    video_start: Optional[datetime] = None,
) -> None:
    data = []
    for seg in segments:
        item = {
            "start": seconds_to_timecode(seg.start),
            "end": seconds_to_timecode(seg.end),
            "start_seconds": round(seg.start, 3),
            "end_seconds": round(seg.end, 3),
            "duration": round(seg.duration, 3),
            "label": "highlight",
            "score": round(seg.score, 1),
            "reason": seg.reason,
        }
        item.update(wall_clock_fields(video_start, seg))
        data.append(item)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_csv(path: Path) -> List[Segment]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    segments = []
    for row in rows:
        start = float(row["start"])
        end = float(row["end"])
        segments.append(
            Segment(
                start=start,
                end=end,
                score=float(row.get("score") or 0),
                reason=row.get("reason") or "manual",
            )
        )
    return segments


def apply_profile(args: argparse.Namespace) -> None:
    apply_profile_values(args, getattr(args, "profile", "balanced"))


def default_output_dir(input_video: Path, explicit_dir: Optional[str]) -> Path:
    if explicit_dir:
        return Path(explicit_dir).expanduser().resolve()
    return Path.cwd().resolve() / f"{input_video.stem}_highlights"


def prepare_outputs(args: argparse.Namespace, video: Path) -> None:
    out_dir = default_output_dir(video, getattr(args, "out_dir", None))
    out_dir.mkdir(parents=True, exist_ok=True)
    if getattr(args, "csv", None) is None:
        args.csv = str(out_dir / "highlights.csv")
    if getattr(args, "json", None) is None and getattr(args, "write_json", False):
        args.json = str(out_dir / "highlights.json")
    if getattr(args, "output", None) is None:
        args.output = str(out_dir / "highlights.mp4")
    if getattr(args, "preview", None) is None and getattr(args, "write_preview", False):
        args.preview = str(out_dir / "preview.jpg")
    if getattr(args, "review_dir", None) is None and getattr(args, "write_review", False):
        args.review_dir = str(out_dir / "review")


def scan(args: argparse.Namespace) -> None:
    apply_profile(args)
    video = Path(args.input).expanduser().resolve()
    if not video.exists():
        die(f"输入视频不存在：{video}")
    prepare_outputs(args, video)

    duration = probe_duration(video)
    print(f"扫描视频：{video}")
    print(f"视频时长：{duration:.1f} 秒")
    print(
        "扫描参数："
        f"profile={args.profile}, percentile={args.percentile}, padding={args.padding}, "
        f"limit={args.limit}, ignore_start={args.ignore_start}"
    )
    windows = extract_audio_windows(video, window_seconds=args.window)
    segments = detect_segments(
        windows,
        duration,
        percentile_threshold=args.percentile / 100.0,
        min_segment=args.min_segment,
        max_segment=args.max_segment,
        padding=args.padding,
        merge_gap=args.merge_gap,
        suppress_gap=args.suppress_gap,
        limit=args.limit,
        ignore_start=args.ignore_start,
        ignore_end=args.ignore_end,
    )
    video_start = parse_wall_clock_start(video)
    if video_start:
        print(f"识别到原视频真实起始时间：{video_start:%Y-%m-%d %H:%M:%S}")
    output = Path(args.csv).expanduser().resolve()
    write_csv(output, segments, video_start=video_start)
    print(f"已输出：{output}")
    if getattr(args, "json", None):
        json_output = Path(args.json).expanduser().resolve()
        write_json(json_output, segments, video_start=video_start)
        print(f"已输出：{json_output}")
    print(f"片段数：{len(segments)}")


def render(args: argparse.Namespace) -> None:
    apply_profile(args)
    video = Path(args.input).expanduser().resolve()
    prepare_outputs(args, video)
    csv_path = Path(args.csv).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not video.exists():
        die(f"输入视频不存在：{video}")
    if not csv_path.exists():
        die(f"CSV 不存在：{csv_path}")

    ffmpeg = require_binary("ffmpeg")
    segments = read_csv(csv_path)
    if not segments:
        die("CSV 中没有可剪辑片段。")

    with tempfile.TemporaryDirectory(prefix="highlights-") as tmp_name:
        tmp = Path(tmp_name)
        list_path = tmp / "concat.txt"
        clip_paths = []
        for index, seg in enumerate(segments, start=1):
            clip = tmp / f"clip_{index:03d}.mp4"
            clip_paths.append(clip)
            duration = seg.duration
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{seg.start:.3f}",
                "-t",
                f"{duration:.3f}",
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-vf",
                args.video_filter,
                "-preset",
                args.preset,
                "-crf",
                str(args.crf),
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                str(clip),
            ]
            print(f"剪辑片段 {index}/{len(segments)}：{seg.start:.1f}s - {seg.end:.1f}s")
            run(cmd, capture=True)

        with list_path.open("w", encoding="utf-8") as fh:
            for clip in clip_paths:
                escaped = str(clip).replace("'", "'\\''")
                fh.write(f"file '{escaped}'\n")

        concat_cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(output),
        ]
        run(concat_cmd, capture=True)
    print(f"已输出：{output}")
    if getattr(args, "preview", None):
        make_preview(output, Path(args.preview).expanduser().resolve())
    if not getattr(args, "no_split", False):
        split_for_wechat_if_needed(
            output,
            minutes=getattr(args, "split_minutes", 14.0),
            preset=args.preset,
            crf=args.crf,
        )


def review(args: argparse.Namespace) -> None:
    video = Path(args.input).expanduser().resolve()
    csv_path = Path(args.csv).expanduser().resolve()
    if not video.exists():
        die(f"输入视频不存在：{video}")
    if not csv_path.exists():
        die(f"CSV 不存在：{csv_path}")

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    segments = read_csv(csv_path)
    if not segments:
        die("CSV 中没有可复核片段。")

    rows = []
    for index, seg in enumerate(segments, start=1):
        image_name = f"segment_{index:03d}.jpg"
        image_path = out_dir / image_name
        make_segment_sheet(video, seg, image_path)
        rows.append((index, seg, image_name))
        print(
            f"复核图 {index}/{len(segments)}："
            f"{seconds_to_timecode(seg.start)} - {seconds_to_timecode(seg.end)}"
        )

    html_path = out_dir / "review.html"
    write_review_html(html_path, rows)
    print(f"已输出复核页面：{html_path}")


def make_preview(video: Path, output: Path) -> None:
    ffmpeg = require_binary("ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-vf",
            "fps=1/15,scale=320:-1,tile=4x3",
            "-frames:v",
            "1",
            str(output),
        ],
        capture=True,
    )
    print(f"已输出预览图：{output}")


def split_for_wechat_if_needed(video: Path, *, minutes: float, preset: str, crf: int) -> None:
    if minutes <= 0:
        return

    duration = probe_duration(video)
    max_seconds = minutes * 60.0
    if duration <= max_seconds:
        return

    ffmpeg = require_binary("ffmpeg")
    part_count = math.ceil(duration / max_seconds)
    out_dir = video.parent / f"{video.stem}_wechat_parts"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"视频超过 {minutes:g} 分钟，开始拆分为 {part_count} 个微信分段：{out_dir}")

    for index in range(part_count):
        start = index * max_seconds
        length = min(max_seconds, duration - start)
        output = out_dir / f"{video.stem}_part{index + 1:02d}.mp4"
        print(
            f"微信分段 {index + 1}/{part_count}："
            f"{seconds_to_timecode(start)} - {seconds_to_timecode(start + length)}"
        )
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{length:.3f}",
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                preset,
                "-crf",
                str(crf),
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                str(output),
            ],
            capture=True,
        )


def make_segment_sheet(video: Path, segment: Segment, output: Path) -> None:
    ffmpeg = require_binary("ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)
    step = max(1.0, segment.duration / 4.0)
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{segment.start:.3f}",
            "-t",
            f"{segment.duration:.3f}",
            "-i",
            str(video),
            "-vf",
            f"fps=1/{step:.3f},scale=320:-1,tile=3x1",
            "-frames:v",
            "1",
            str(output),
        ],
        capture=True,
    )


def write_review_html(path: Path, rows: List[tuple]) -> None:
    parts = [
        "<!doctype html>",
        "<meta charset='utf-8'>",
        "<title>XbotGo Highlights Review</title>",
        "<style>",
        "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:24px;background:#f6f7f8;color:#1f2328}",
        "table{border-collapse:collapse;width:100%;background:white}",
        "th,td{border-bottom:1px solid #d8dee4;padding:10px;text-align:left;vertical-align:top}",
        "th{background:#f0f2f4}",
        "img{max-width:960px;width:100%;height:auto}",
        ".time{white-space:nowrap;font-variant-numeric:tabular-nums}",
        ".score{font-variant-numeric:tabular-nums}",
        "</style>",
        "<h1>XbotGo Highlights Review</h1>",
        "<table>",
        "<tr><th>#</th><th>Time</th><th>Score</th><th>Reason</th><th>Frames</th></tr>",
    ]
    for index, seg, image_name in rows:
        parts.append(
            "<tr>"
            f"<td>{index:02d}</td>"
            f"<td class='time'>{seconds_to_timecode(seg.start)}<br>{seconds_to_timecode(seg.end)}<br>{seg.duration:.1f}s</td>"
            f"<td class='score'>{seg.score:.1f}</td>"
            f"<td>{seg.reason}</td>"
            f"<td><img src='{image_name}'></td>"
            "</tr>"
        )
    parts.extend(["</table>"])
    path.write_text("\n".join(parts), encoding="utf-8")


def print_segments(args: argparse.Namespace) -> None:
    segments = read_csv(Path(args.csv).expanduser().resolve())
    for index, seg in enumerate(segments, start=1):
        print(
            f"{index:02d}. {seconds_to_timecode(seg.start)} -> "
            f"{seconds_to_timecode(seg.end)}  "
            f"{seg.duration:.1f}s  score={seg.score:.1f}  {seg.reason}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="本地比赛高光工具：match.mp4 -> highlights.csv -> highlights.mp4"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_scan_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--profile", choices=sorted(PROFILES), default="sevens", help="检测预设")
        p.add_argument("--window", type=float, default=0.5, help="音频分析窗口秒数")
        p.add_argument("--percentile", type=float, default=None, help="高光阈值百分位")
        p.add_argument("--padding", type=float, default=None, help="片段前后扩展秒数")
        p.add_argument("--merge-gap", type=float, default=None, help="合并间隔秒数")
        p.add_argument("--suppress-gap", type=float, default=None, help="相近候选去重间隔秒数")
        p.add_argument("--min-segment", type=float, default=None, help="最短片段秒数")
        p.add_argument("--max-segment", type=float, default=None, help="最长片段秒数")
        p.add_argument("--limit", type=int, default=None, help="最多保留片段数，0 表示不限")
        p.add_argument("--ignore-start", type=float, default=None, help="忽略开头秒数")
        p.add_argument("--ignore-end", type=float, default=0.0, help="忽略结尾秒数")

    def add_render_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--preset", default="veryfast", help="x264 编码 preset")
        p.add_argument("--crf", type=int, default=23, help="x264 CRF，越小越清晰")
        p.add_argument(
            "--video-filter",
            default="scale='min(1280,iw)':-2",
            help="ffmpeg 视频滤镜，默认宽度压到最高 720p 级别",
        )
        p.add_argument("--preview", default=None, help="可选输出预览图")
        p.add_argument("--split-minutes", type=float, default=14.0, help="超过该分钟数自动拆分")
        p.add_argument("--no-split", action="store_true", help="不自动拆分长视频")

    scan_parser = sub.add_parser("scan", help="自动扫描高光片段并输出 CSV")
    scan_parser.add_argument("-i", "--input", default="match.mp4", help="输入视频")
    scan_parser.add_argument("--out-dir", default=None, help="输出目录，默认按视频名创建")
    scan_parser.add_argument("-c", "--csv", default=None, help="输出 CSV")
    scan_parser.add_argument("-j", "--json", default=None, help="可选输出 JSON")
    scan_parser.set_defaults(write_json=True)
    add_scan_options(scan_parser)
    scan_parser.set_defaults(func=scan)

    render_parser = sub.add_parser("render", help="根据 CSV 剪出高光视频")
    render_parser.add_argument("-i", "--input", default="match.mp4", help="输入视频")
    render_parser.add_argument("--out-dir", default=None, help="输出目录，默认按视频名创建")
    render_parser.add_argument("-c", "--csv", default=None, help="输入 CSV")
    render_parser.add_argument("-o", "--output", default=None, help="输出视频")
    render_parser.add_argument("--profile", choices=sorted(PROFILES), default="sevens", help="检测预设")
    render_parser.set_defaults(write_preview=True)
    add_render_options(render_parser)
    render_parser.set_defaults(func=render)

    all_parser = sub.add_parser("all", help="扫描并直接生成高光视频")
    all_parser.add_argument("-i", "--input", default="match.mp4", help="输入视频")
    all_parser.add_argument("--out-dir", default=None, help="输出目录，默认按视频名创建")
    all_parser.add_argument("-c", "--csv", default=None, help="输出 CSV")
    all_parser.add_argument("-j", "--json", default=None, help="可选输出 JSON")
    all_parser.add_argument("-o", "--output", default=None, help="输出视频")
    all_parser.add_argument("--review-dir", default=None, help="候选片段复核页面输出目录")
    all_parser.set_defaults(write_json=True, write_preview=True, write_review=True)
    add_scan_options(all_parser)
    add_render_options(all_parser)

    def run_all(args: argparse.Namespace) -> None:
        scan(args)
        render(args)
        if getattr(args, "write_review", False):
            review(
                argparse.Namespace(
                    input=args.input,
                    csv=args.csv,
                    out_dir=args.review_dir,
                )
            )

    all_parser.set_defaults(func=run_all)

    auto_parser = sub.add_parser("auto", help="推荐入口：扫描、合成、生成 JSON 和预览图")
    auto_parser.add_argument("input", help="输入视频")
    auto_parser.add_argument("--out-dir", default=None, help="输出目录，默认按视频名创建")
    auto_parser.add_argument("-c", "--csv", default=None, help="输出 CSV")
    auto_parser.add_argument("-j", "--json", default=None, help="输出 JSON")
    auto_parser.add_argument("-o", "--output", default=None, help="输出视频")
    auto_parser.add_argument("--review-dir", default=None, help="候选片段复核页面输出目录")
    auto_parser.set_defaults(write_json=True, write_preview=True, write_review=True)
    add_scan_options(auto_parser)
    add_render_options(auto_parser)
    auto_parser.set_defaults(func=run_all)

    list_parser = sub.add_parser("list", help="打印 CSV 里的片段列表")
    list_parser.add_argument("-c", "--csv", required=True, help="输入 CSV")
    list_parser.set_defaults(func=print_segments)

    review_parser = sub.add_parser("review", help="为 CSV 候选片段生成画面复核页面")
    review_parser.add_argument("-i", "--input", required=True, help="输入视频")
    review_parser.add_argument("-c", "--csv", required=True, help="输入 CSV")
    review_parser.add_argument("--out-dir", required=True, help="复核页面输出目录")
    review_parser.set_defaults(func=review)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
