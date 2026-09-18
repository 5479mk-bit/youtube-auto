"""자막·훅 숫자·출처 배지 같은 화면 텍스트를 PNG 로 미리 그린다.

프레임마다 글자를 그리면 렌더가 느려지므로, 큐 하나당 이미지 하나를 만들어
타임라인에 얹는다. 모든 텍스트는 안전 밴드(상단 25%~하단 75%) 안에 들어간다.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .. import assets
from ..subtitles.subtitle_builder import SubtitleCue
from ..visuals.fonts import ensure_korean_font


def _font(size: int) -> ImageFont.FreeTypeFont:
    """자막·배지용 폰트. 있으면 프리텐다드(자막체), 없으면 기존 노토 산스."""
    path = assets.FONT_SANS if assets.FONT_SANS.exists() else ensure_korean_font()
    return ImageFont.truetype(str(path), size)


def _shadow_offsets(blur: int) -> list[tuple[int, int]]:
    """드롭 섀도우를 흉내 낼 오프셋들.

    PIL 에는 그림자가 없어서 같은 글자를 조금씩 밀어 여러 번 옅게 그린다. 외곽선(stroke)과
    달리 글자가 두꺼워지지 않아 촌스럽지 않다.
    """
    return [(dx, dy) for dy in range(1, blur + 1) for dx in (-1, 0, 1)]


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: str,
    shadow_blur: int = 6,
    shadow_alpha: int = 150,
) -> None:
    x, y = position
    for dx, dy in _shadow_offsets(shadow_blur):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, shadow_alpha // shadow_blur))
    draw.text((x, y), text, font=font, fill=fill)


def render_subtitle(
    cue: SubtitleCue,
    out_path: Path,
    size: tuple[int, int],
    style: dict,
    safe_bottom: float,
) -> Path:
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = _font(style["font_size"])

    line_height = int(style["font_size"] * 1.35)
    block_height = line_height * len(cue.lines)
    # 자막 블록의 아래끝을 화면 하단 22~25% 지점에 둔다. 쇼츠 UI(하단 버튼)를 피하면서도
    # 너무 위로 붕 뜨지 않는 자리다. safe_bottom 이 더 크면 그쪽을 따른다.
    bottom_ratio = max(float(style.get("bottom_ratio", 0.24)), float(safe_bottom))
    baseline = int(height * (1 - bottom_ratio)) - block_height

    for index, text in enumerate(cue.lines):
        y = baseline + index * line_height
        _draw_line_with_emphasis(draw, text, cue.emphasis, font, width, y, style)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path


def _draw_line_with_emphasis(
    draw: ImageDraw.ImageDraw,
    text: str,
    emphasis: list[str],
    font: ImageFont.FreeTypeFont,
    width: int,
    y: int,
    style: dict,
) -> None:
    segments = _split_emphasis(text, emphasis)
    total = sum(draw.textlength(segment, font=font) for segment, _ in segments)
    x = (width - total) / 2

    for segment, highlighted in segments:
        color = style["emphasis_color"] if highlighted else style["font_color"]
        # 외곽선은 쓰지 않는다 — 글자가 굵어지고 촌스러워진다. 대신 반투명 그림자로
        # 어떤 배경 위에서도 읽히게 한다.
        _draw_text_with_shadow(
            draw, (x, y), segment, font, color, shadow_blur=style.get("shadow_blur", 6)
        )
        x += draw.textlength(segment, font=font)


def _split_emphasis(text: str, emphasis: list[str]) -> list[tuple[str, bool]]:
    """강조 조각을 기준으로 문장을 (텍스트, 강조여부) 조각들로 나눈다."""
    segments: list[tuple[str, bool]] = [(text, False)]
    for phrase in sorted({item for item in emphasis if item.strip()}, key=len, reverse=True):
        next_segments: list[tuple[str, bool]] = []
        for segment, highlighted in segments:
            if highlighted or phrase not in segment:
                next_segments.append((segment, highlighted))
                continue
            head, _, tail = segment.partition(phrase)
            if head:
                next_segments.append((head, False))
            next_segments.append((phrase, True))
            if tail:
                next_segments.append((tail, False))
        segments = next_segments
    return segments


def render_hook_number(
    number: str, out_path: Path, size: tuple[int, int], color: str = "#FFD84D"
) -> Path:
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 훅 문구는 기록체(명조)로 띄운다 — 다큐 톤의 첫인상이 여기서 정해진다.
    serif = assets.FONT_SERIF_HANJA if assets.FONT_SERIF_HANJA.exists() else None
    font_size = int(width * 0.16)
    font = (
        ImageFont.truetype(str(serif), font_size) if serif else _font(font_size)
    )
    while draw.textlength(number, font=font) > width * 0.82 and font_size > 40:
        font_size = int(font_size * 0.9)
        font = (
            ImageFont.truetype(str(serif), font_size) if serif else _font(font_size)
        )

    text_width = draw.textlength(number, font=font)
    # 화면 중앙보다 살짝 위. 그래야 아래쪽 자막과 겹치지 않는다.
    _draw_text_with_shadow(
        draw,
        ((width - text_width) / 2, height * 0.36),
        number,
        font,
        color,
        shadow_blur=10,
        shadow_alpha=190,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path


def render_banner(
    text: str,
    out_path: Path,
    size: tuple[int, int],
    font_size: int,
    y_ratio: float,
    color: str = "#FFFFFF",
) -> Path:
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = _font(font_size)

    text_width = draw.textlength(text, font=font)
    _draw_text_with_shadow(
        draw, ((width - text_width) / 2, height * y_ratio), text, font, color, shadow_blur=5
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path
