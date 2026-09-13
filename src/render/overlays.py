"""자막·훅 숫자·출처 배지 같은 화면 텍스트를 PNG 로 미리 그린다.

프레임마다 글자를 그리면 렌더가 느려지므로, 큐 하나당 이미지 하나를 만들어
타임라인에 얹는다. 모든 텍스트는 안전 밴드(상단 25%~하단 75%) 안에 들어간다.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..subtitles.subtitle_builder import SubtitleCue
from ..visuals.fonts import ensure_korean_font


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ensure_korean_font()), size)


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
    # 자막 블록의 아래끝을 하단 안전선 바로 위에 붙인다.
    baseline = int(height * (1 - safe_bottom)) - block_height

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
        draw.text(
            (x, y),
            segment,
            font=font,
            fill=color,
            stroke_width=style["stroke_width"],
            stroke_fill=style["stroke_color"],
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

    font_size = int(width * 0.30)
    font = _font(font_size)
    while draw.textlength(number, font=font) > width * 0.86 and font_size > 40:
        font_size = int(font_size * 0.9)
        font = _font(font_size)

    text_width = draw.textlength(number, font=font)
    # 화면 중앙보다 살짝 위. 그래야 아래쪽 자막과 겹치지 않는다.
    draw.text(
        ((width - text_width) / 2, height * 0.36),
        number,
        font=font,
        fill=color,
        stroke_width=int(font_size * 0.06),
        stroke_fill="#000000",
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
    draw.text(
        ((width - text_width) / 2, height * y_ratio),
        text,
        font=font,
        fill=color,
        stroke_width=max(2, font_size // 12),
        stroke_fill="#000000",
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path
