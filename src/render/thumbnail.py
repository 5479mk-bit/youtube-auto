"""1부/2부 썸네일. 영상 첫 프레임 위에 핵심 숫자를 얹는다."""

from __future__ import annotations

from pathlib import Path

from moviepy import VideoFileClip
from PIL import Image

from ..models import ScriptPart
from . import overlays


def build_thumbnail(
    video_path: Path,
    part: ScriptPart,
    out_path: Path,
    size: tuple[int, int],
    is_combined: bool = False,
) -> Path:
    with VideoFileClip(str(video_path)) as clip:
        frame = clip.get_frame(min(1.0, clip.duration / 2))

    base = Image.fromarray(frame).convert("RGBA")

    # 합본(현재 유일하게 쓰는 모드)은 "N부" 라벨이 의미가 없다 — 영상이 하나뿐이라
    # 그 표기는 한국어 글자가 유튜브 썸네일(시청자에게 그대로 노출)에 새는 통로였다.
    label = part.hook_number if is_combined else f"{part.hook_number}  |  {part.part}부"
    overlay_path = overlays.render_banner(
        label, out_path.with_name(f"{out_path.stem}_label.png"), size, 84, 0.42
    )
    with Image.open(overlay_path) as overlay:
        base.alpha_composite(overlay.convert("RGBA"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out_path, "JPEG", quality=92)
    return out_path
