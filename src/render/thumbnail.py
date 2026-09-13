"""1부/2부 썸네일. 영상 첫 프레임 위에 핵심 숫자를 얹는다."""

from __future__ import annotations

from pathlib import Path

from moviepy import VideoFileClip
from PIL import Image

from ..models import ScriptPart
from . import overlays


def build_thumbnail(
    video_path: Path, part: ScriptPart, out_path: Path, size: tuple[int, int]
) -> Path:
    with VideoFileClip(str(video_path)) as clip:
        frame = clip.get_frame(min(1.0, clip.duration / 2))

    base = Image.fromarray(frame).convert("RGBA")

    label = f"{part.hook_number}  |  {part.part}부"
    overlay_path = overlays.render_banner(
        label, out_path.with_name(f"{out_path.stem}_label.png"), size, 84, 0.42
    )
    with Image.open(overlay_path) as overlay:
        base.alpha_composite(overlay.convert("RGBA"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out_path, "JPEG", quality=92)
    return out_path
