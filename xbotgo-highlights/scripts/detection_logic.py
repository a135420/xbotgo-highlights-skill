from dataclasses import dataclass
import math
import statistics
from typing import Dict, List


PROFILES: Dict[str, Dict[str, float]] = {
    "balanced": {
        "percentile": 90.0,
        "padding": 8.0,
        "merge_gap": 3.0,
        "min_segment": 10.0,
        "max_segment": 38.0,
        "limit": 32,
        "ignore_start": 0.0,
        "suppress_gap": 14.0,
    },
    "precise": {
        "percentile": 92.5,
        "padding": 7.0,
        "merge_gap": 2.0,
        "min_segment": 10.0,
        "max_segment": 34.0,
        "limit": 28,
        "ignore_start": 0.0,
        "suppress_gap": 16.0,
    },
    "strict": {
        "percentile": 95.0,
        "padding": 6.0,
        "merge_gap": 1.5,
        "min_segment": 8.0,
        "max_segment": 28.0,
        "limit": 18,
        "ignore_start": 0.0,
        "suppress_gap": 22.0,
    },
    "sevens": {
        "percentile": 92.0,
        "padding": 8.0,
        "merge_gap": 2.5,
        "min_segment": 10.0,
        "max_segment": 36.0,
        "limit": 30,
        "ignore_start": 0.0,
        "suppress_gap": 14.0,
    },
}


@dataclass
class Window:
    start: float
    end: float
    db: float
    onset: float = 0.0
    score: float = 0.0


@dataclass
class Segment:
    start: float
    end: float
    score: float
    reason: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def apply_profile_values(target: object, profile_name: str) -> None:
    profile = PROFILES.get(profile_name)
    if not profile:
        return
    for key, value in profile.items():
        if getattr(target, key, None) is None:
            setattr(target, key, value)


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * p
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return ordered[int(pos)]
    return ordered[lower] * (upper - pos) + ordered[upper] * (pos - lower)


def smooth_scores(windows: List[Window], radius: int = 2) -> None:
    raw = [w.db for w in windows]
    if not raw:
        return

    floor = percentile(raw, 0.15)
    ceiling = percentile(raw, 0.95)
    energy_span = max(1.0, ceiling - floor)

    onsets = []
    for i, window in enumerate(windows):
        history = raw[max(0, i - 8) : i]
        baseline = statistics.median(history) if history else window.db
        window.onset = max(0.0, window.db - baseline)
        onsets.append(window.onset)

    onset_ceiling = max(1.0, percentile(onsets, 0.97))

    for i, window in enumerate(windows):
        lo = max(0, i - radius)
        hi = min(len(raw), i + radius + 1)
        local_db = statistics.mean(raw[lo:hi])
        energy_score = max(0.0, min(100.0, (local_db - floor) / energy_span * 100.0))
        onset_score = max(0.0, min(100.0, window.onset / onset_ceiling * 100.0))
        window.score = energy_score * 0.72 + onset_score * 0.28


def detect_segments(
    windows: List[Window],
    duration: float,
    *,
    percentile_threshold: float,
    min_segment: float,
    max_segment: float,
    padding: float,
    merge_gap: float,
    suppress_gap: float,
    limit: int,
    ignore_start: float,
    ignore_end: float,
) -> List[Segment]:
    if not windows:
        return []

    smooth_scores(windows)
    threshold = percentile([w.score for w in windows], percentile_threshold)
    candidates = [
        w
        for w in windows
        if w.score >= threshold
        and w.db > -55.0
        and w.start >= ignore_start
        and w.end <= max(ignore_start, duration - ignore_end)
    ]

    segments: List[Segment] = []
    current: List[Window] = []
    for window in candidates:
        if not current or window.start <= current[-1].end + merge_gap:
            current.append(window)
            continue
        segments.append(segment_from_windows(current, duration, padding))
        current = [window]
    if current:
        segments.append(segment_from_windows(current, duration, padding))

    merged = merge_segments(segments, merge_gap, duration)
    cleaned = []
    for seg in merged:
        seg.start = max(seg.start, ignore_start)
        seg.end = min(seg.end, duration - ignore_end)
        if seg.duration < min_segment:
            center = (seg.start + seg.end) / 2.0
            seg.start = max(0.0, center - min_segment / 2.0)
            seg.end = min(duration, seg.start + min_segment)
        if seg.duration > max_segment:
            center = (seg.start + seg.end) / 2.0
            seg.start = max(0.0, center - max_segment / 2.0)
            seg.end = min(duration, seg.start + max_segment)
        seg.start = max(seg.start, ignore_start)
        seg.end = min(seg.end, duration - ignore_end)
        if seg.duration >= min_segment:
            cleaned.append(seg)

    selected = select_distinct_segments(cleaned, limit=limit, suppress_gap=suppress_gap)
    selected.sort(key=lambda s: s.start)
    return selected


def select_distinct_segments(
    segments: List[Segment], *, limit: int, suppress_gap: float
) -> List[Segment]:
    selected: List[Segment] = []
    for seg in sorted(segments, key=lambda s: s.score, reverse=True):
        if any(segments_too_close(seg, existing, suppress_gap) for existing in selected):
            continue
        selected.append(seg)
        if limit > 0 and len(selected) >= limit:
            break
    return selected


def segments_too_close(a: Segment, b: Segment, gap: float) -> bool:
    return a.start <= b.end + gap and b.start <= a.end + gap


def segment_from_windows(windows: List[Window], duration: float, padding: float) -> Segment:
    score = statistics.mean([w.score for w in windows])
    peak = max(windows, key=lambda w: w.score)
    start = max(0.0, windows[0].start - padding)
    end = min(duration, windows[-1].end + padding)
    reason = "audio_spike" if peak.onset >= 2.5 else "audio_energy"
    return Segment(start=start, end=end, score=score, reason=reason)


def merge_segments(segments: List[Segment], merge_gap: float, duration: float) -> List[Segment]:
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: s.start)
    merged = [ordered[0]]
    for seg in ordered[1:]:
        last = merged[-1]
        if seg.start <= last.end + merge_gap:
            total_duration = last.duration + seg.duration
            if total_duration > 0:
                last.score = (
                    last.score * last.duration + seg.score * seg.duration
                ) / total_duration
            last.end = min(duration, max(last.end, seg.end))
        else:
            merged.append(seg)
    return merged
