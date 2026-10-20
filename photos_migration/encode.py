"""Adaptive HEVC re-encode: keep it only when it is clearly smaller at
visually transparent quality (VMAF), otherwise keep the original.

1. Probe: encode short samples at a few CRFs and measure VMAF + size ratio.
2. Pick the highest CRF that still meets MIN_VMAF; skip if the predicted
   saving is below MIN_SAVING.
3. Full encode, then verify the real file (size and subsampled VMAF).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config

SAMPLE_SECONDS = 6
WHOLE_FILE_BELOW = 25           # seconds: shorter clips are probed whole
CRF_MIN, CRF_START, CRF_MAX = 16, 24, 30
VMAF_MARGIN = 0.5               # probe must beat MIN_VMAF by this much
X265 = ["-c:v", "libx265", "-preset", "medium", "-x265-params", "log-level=error", "-tag:v", "hvc1"]


@dataclass
class EncodeResult:
    use_hevc: bool
    path: Path                  # file to upload (original or HEVC)
    crf: int | None = None
    vmaf: float | None = None
    ratio: float | None = None  # hevc bytes / original bytes
    reason: str = ""


def _run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def color_range(path: Path) -> tuple[bool, bool]:
    """(bitstream says full range, MP4 colr atom says full range)."""
    frame = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#1",
                            "-show_entries", "frame=color_range", "-of", "json", str(path)],
                           capture_output=True, text=True, check=True).stdout
    frames = json.loads(frame).get("frames") or [{}]
    bitstream_full = frames[0].get("color_range") == "pc"
    tag = subprocess.run(["exiftool", "-s3", "-n", "-VideoFullRangeFlag", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    container_full = tag == "1" if tag else bitstream_full
    return bitstream_full, container_full


def vmaf(distorted: Path, reference: Path, subsample: int = 1) -> tuple[float, float]:
    """(harmonic-mean VMAF, 5th-percentile frame VMAF)."""
    log = distorted.with_suffix(".vmaf.json")
    graph = (f"[0:v]setpts=PTS-STARTPTS[d];[1:v]setpts=PTS-STARTPTS[r];"
             f"[d][r]libvmaf=n_threads=14:n_subsample={subsample}:pool=harmonic_mean:log_fmt=json:log_path={log}")
    _run(["-i", str(distorted), "-i", str(reference), "-lavfi", graph, "-f", "null", "-"])
    d = json.loads(log.read_text())
    log.unlink(missing_ok=True)
    frames = sorted(f["metrics"]["vmaf"] for f in d.get("frames", []))
    p5 = frames[len(frames) // 20] if frames else 0.0
    return d["pooled_metrics"]["vmaf"]["harmonic_mean"], p5


def _samples(src: Path, work: Path) -> list[Path]:
    dur = duration(src)
    if dur <= WHOLE_FILE_BELOW:
        ref = work / "sample0.mp4"
        _run(["-i", str(src), "-map", "0:v:0", "-c", "copy", str(ref)])
        return [ref]
    refs = []
    for i, frac in enumerate((0.15, 0.50, 0.85)):
        ref = work / f"sample{i}.mp4"
        start = max(0.0, dur * frac - SAMPLE_SECONDS / 2)
        _run(["-ss", f"{start:.2f}", "-i", str(src), "-t", str(SAMPLE_SECONDS), "-map", "0:v:0", "-c", "copy", str(ref)])
        refs.append(ref)
    return refs


def _shown_video_bytes(path: Path) -> int:
    """Video bytes of frames actually shown: a stream-copied cut starts at the previous
    keyframe, and those pre-roll packets (pts < 0) are never decoded or encoded."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,size",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    total = 0
    for line in out.splitlines():
        pts, _, size = line.partition(",")
        try:
            if float(pts) >= 0:
                total += int(size)
        except ValueError:
            continue
    return total


def _gpmd_index(path: Path) -> int | None:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_tag_string", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return next((st["index"] for st in json.loads(out)["streams"] if st.get("codec_tag_string") == "gpmd"), None)


def _probe(refs: list[Path], crf: int) -> tuple[float, float]:
    """(worst per-sample VMAF, size ratio) for a CRF."""
    worst, out_bytes, in_bytes = 100.0, 0, 0
    for ref in refs:
        out = ref.with_name(f"{ref.stem}.crf{crf}.mp4")
        _run(["-i", str(ref), "-map", "0:v:0", *X265, "-crf", str(crf), str(out)])
        worst = min(worst, vmaf(out, ref, subsample=2)[0])
        out_bytes += out.stat().st_size
        in_bytes += _shown_video_bytes(ref)
        out.unlink()
    return worst, out_bytes / in_bytes


def _fmt(tried: dict[int, tuple[float, float]]) -> str:
    return ", ".join(f"crf{c}: vmaf {v:.1f} size {r:.0%}" for c, (v, r) in sorted(tried.items()))


def _search(refs: list[Path]) -> tuple[int | None, dict[int, tuple[float, float]]]:
    """Highest CRF (= smallest file) whose probe VMAF meets the target.

    VMAF falls and size shrinks as CRF rises, so binary-search CRF_MIN..CRF_MAX and
    stop early once a failing CRF is already too big to save MIN_SAVING (any lower
    CRF would be bigger still).
    """
    target = config.MIN_VMAF + VMAF_MARGIN
    tried: dict[int, tuple[float, float]] = {}
    lo, hi, best = CRF_MIN, CRF_MAX, None
    crf = CRF_START
    while lo <= hi:
        score, ratio = tried[crf] = _probe(refs, crf)
        if score >= target:
            best, lo = crf, crf + 1
        else:
            if ratio > 1 - config.MIN_SAVING:
                break
            hi = crf - 1
        crf = (lo + hi) // 2
    return best, tried


def adaptive_encode(src: Path, work: Path) -> EncodeResult:
    """Never raises: any encode/measure failure falls back to the original."""
    work.mkdir(parents=True, exist_ok=True)
    try:
        return _adaptive_encode(src, work)
    except Exception as e:  # noqa: BLE001 - the original is always a valid result
        for f in work.glob(f"{src.stem}.hevc*"):
            f.unlink(missing_ok=True)
        for f in work.glob("sample*"):
            f.unlink(missing_ok=True)
        return EncodeResult(False, src, reason=f"encode error, keeping original: {type(e).__name__}: {str(e)[:150]}")


def _adaptive_encode(src: Path, work: Path) -> EncodeResult:
    refs = _samples(src, work)
    try:
        best, tried = _search(refs)
        if best is None:
            return EncodeResult(False, src, reason=f"no CRF reaches VMAF {config.MIN_VMAF} with {config.MIN_SAVING:.0%} saving "
                                                   f"(probes {_fmt(tried)})")
        crf = best
        pred_vmaf, pred_ratio = tried[crf]
        if pred_ratio > 1 - config.MIN_SAVING:
            return EncodeResult(False, src, crf, pred_vmaf, pred_ratio,
                                reason=f"predicted saving {1 - pred_ratio:.0%} < {config.MIN_SAVING:.0%}")
    finally:
        for r in refs:
            r.unlink(missing_ok=True)

    out = work / f"{src.stem}.hevc.mp4"
    raw = work / f"{src.stem}.hevc.raw.mp4"
    bitstream_full, container_full = color_range(src)
    x265 = list(X265)
    x265[x265.index("log-level=error")] = f"log-level=error:range={'full' if bitstream_full else 'limited'}"
    _run(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", *x265, "-crf", str(crf),
          "-c:a", "copy", "-map_metadata", "0", str(raw)])
    # GoPro files flag full range in the H.264 bitstream but 'Limited' in the MP4
    # colr atom. Players disagree on which one wins, so mirror both exactly by
    # rewriting the container flag on a stream-copy remux.
    # Also carry the GoPro GPMF telemetry track (GPS/gyro) straight from the original.
    gpmd = _gpmd_index(src)
    _run(["-i", str(raw), "-i", str(src), "-map", "0:v", "-map", "0:a?",
          *(["-map", f"1:{gpmd}"] if gpmd is not None else []),
          "-c", "copy", "-map_metadata", "0", "-color_range", "pc" if container_full else "tv",
          "-tag:v", "hvc1", str(out)])
    raw.unlink()
    if color_range(out) != (bitstream_full, container_full):
        out.unlink()
        return EncodeResult(False, src, crf, reason="could not reproduce the source colour-range signalling")
    ratio = out.stat().st_size / src.stat().st_size
    if ratio > 1 - config.MIN_SAVING:
        out.unlink()
        return EncodeResult(False, src, crf, None, ratio, reason=f"real saving {1 - ratio:.0%} too small")
    score, p5 = vmaf(out, src, subsample=4)
    if score < config.MIN_VMAF or p5 < config.MIN_VMAF_P5:
        out.unlink()
        return EncodeResult(False, src, crf, score, ratio, reason=f"real VMAF {score:.1f} (p5 {p5:.1f}) below target")
    return EncodeResult(True, out, crf, score, ratio, reason="ok")
