"""16:9 -> 9:16 (or any aspect) reformatting.

Modes:
  smart - crop window follows the speaker's face (needs opencv-python)
  crop  - plain center crop
  blur  - shows the whole frame on top of a blurred, zoomed copy of itself
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from cliptool.ffmpeg_utils import MediaInfo
from cliptool.logger import get_logger

log = get_logger("reformat")

Keyframes = list[tuple[float, float]]  # (time, crop x in pixels)


def _even(x: float) -> int:
    return max(2, int(x) // 2 * 2)


def parse_size(size: str) -> tuple[int, int]:
    w, h = size.lower().split("x")
    return int(w), int(h)


def opencv_available() -> bool:
    try:
        import cv2
        return hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data")
    except ImportError:
        return False


def detect_face_track(path: Path, info: MediaInfo, step: float = 0.5) -> list[tuple[float, Optional[float]]]:
    """Sample the video and return (time, face center x as 0..1 or None)."""
    import cv2

    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or info.fps or 30.0
    every = max(1, int(round(fps * step)))
    track: list[tuple[float, Optional[float]]] = []
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % every == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            scale = 360.0 / w if w > 360 else 1.0
            small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                             minSize=(int(small.shape[1] * 0.05),) * 2)
            if len(faces):
                x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                track.append((idx / fps, (x + fw / 2) / small.shape[1]))
            else:
                track.append((idx / fps, None))
        idx += 1
    cap.release()
    return track


def plan_camera(track: list[tuple[float, Optional[float]]], src_w: int, crop_w: int,
                dead_zone: float = 0.08, window: int = 5) -> Keyframes:
    """Turn noisy face positions into a calm 'camera operator' path of crop x keyframes."""
    max_x = src_w - crop_w
    center = max_x / 2
    if not track or all(v is None for _, v in track):
        return [(0.0, center)]
    # fill gaps with the last known position
    filled, last = [], None
    for t, v in track:
        last = v if v is not None else last
        filled.append((t, last))
    first = next(v for _, v in filled if v is not None)
    filled = [(t, v if v is not None else first) for t, v in filled]
    # median smoothing
    vals = [v for _, v in filled]
    smooth = []
    for i in range(len(vals)):
        win = sorted(vals[max(0, i - window // 2): i + window // 2 + 1])
        smooth.append(win[len(win) // 2])
    to_px = lambda v: min(max_x, max(0.0, v * src_w - crop_w / 2))  # noqa: E731
    keys: Keyframes = [(0.0, to_px(smooth[0]))]
    for (t, _), v in zip(filled, smooth):
        if abs(v - (keys[-1][1] + crop_w / 2) / src_w) > dead_zone:
            keys.append((t, to_px(v)))
    return keys


def x_expression(keys: Keyframes, pan: float = 0.35) -> str:
    """ffmpeg expression for crop x that glides between keyframes."""
    expr = f"{keys[0][1]:.1f}"
    for (t0, x0), (t1, x1) in zip(keys, keys[1:]):
        expr += f"+({x1 - x0:.1f})*clip((t-{t1:.2f})/{pan},0,1)"
    return expr


def build_filter(info: MediaInfo, tw: int, th: int, mode: str, in_label: str, out_label: str,
                 keys: Optional[Keyframes] = None, blur: int = 20) -> str:
    """Filter chain that turns `in_label` into a tw x th frame at `out_label`."""
    sw, sh = info.width, info.height
    src_aspect, dst_aspect = sw / sh, tw / th
    if abs(src_aspect - dst_aspect) / dst_aspect < 0.02 or mode == "stretch":
        return f"[{in_label}]scale={tw}:{th},setsar=1[{out_label}]"

    if mode == "blur":
        bw, bh = _even(tw / 4), _even(th / 4)
        return (f"[{in_label}]split=2[{out_label}_b][{out_label}_f];"
                f"[{out_label}_b]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
                f"boxblur={max(1, blur // 2)}:2,scale={tw}:{th},eq=brightness=-0.06[{out_label}_bg];"
                f"[{out_label}_f]scale={tw}:{th}:force_original_aspect_ratio=decrease[{out_label}_fg];"
                f"[{out_label}_bg][{out_label}_fg]overlay=(W-w)/2:(H-h)/2,setsar=1[{out_label}]")

    # crop / smart
    if src_aspect > dst_aspect:  # source is wider: crop the sides
        cw, ch = _even(sh * dst_aspect), _even(sh)
        if mode == "smart" and keys:
            x = f"'{x_expression(keys)}'"
        else:
            x = str(_even((sw - cw) / 2))
        y = "0"
    else:  # source is taller: crop top/bottom, biased upward where faces usually are
        cw, ch = _even(sw), _even(sw / dst_aspect)
        x, y = "0", str(_even((sh - ch) * 0.35))
    return f"[{in_label}]crop={cw}:{ch}:{x}:{y},scale={tw}:{th},setsar=1[{out_label}]"


def analyze_smart(path: Path, info: MediaInfo, tw: int, th: int) -> Optional[Keyframes]:
    if info.width / info.height <= tw / th:
        return None
    if not opencv_available():
        log.info("Smart reframing needs opencv 4.x (pip install \"opencv-python-headless<5\"); "
                 "using center crop.")
        return None
    log.info("Finding faces in %s for smart reframing...", path.name)
    try:
        track = detect_face_track(path, info)
    except Exception as exc:
        log.warning("Face tracking failed (%s); using center crop.", exc)
        return None
    crop_w = _even(info.height * tw / th)
    keys = plan_camera(track, info.width, crop_w)
    found = sum(1 for _, v in track if v is not None)
    log.info("  faces found in %d/%d samples, %d camera moves", found, len(track), len(keys) - 1)
    return keys
