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
) -> tuple[Path, tuple[int, int, int, int]]:
    """자막 큐 하나를 PNG 로 그린다.

    반환하는 bbox(x0, y0, w, h)는 이미지를 화면 전체 캔버스가 아니라 텍스트
    블록만 꽉 차게 잘라낸 크기다 — 자막에 "팝인" 애니메이션(스케일)을 줄 때,
    풀프레임 이미지를 그대로 스케일하면 중심이 화면 한가운데로 쏠려 자막이
    엉뚱한 자리로 움직인다. 블록 자신을 기준으로 잘라야 제자리에서 커졌다
    작아지는 자연스러운 팝인이 된다(사용자 지시, 2026-10-01: "자막이 역동적
    이게 타이핑느낌이라던지").
    """
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

    upper = bool(style.get("uppercase", False))
    lines = [text.upper() for text in cue.lines] if upper else list(cue.lines)
    emphasis = [item.upper() for item in cue.emphasis] if upper else cue.emphasis
    active = getattr(cue, "active_word", -1)
    word_offset = 0
    max_line_width = 0.0
    for index, text in enumerate(lines):
        y = baseline + index * line_height
        count = len(text.split())
        if active >= 0:
            _draw_line_with_active_word(
                draw, text, emphasis, font, width, y, style, active - word_offset
            )
        else:
            _draw_line_with_emphasis(draw, text, emphasis, font, width, y, style)
        word_offset += count
        max_line_width = max(max_line_width, draw.textlength(text, font=font))

    # 그림자·팝인 오버슈트(최대 1.15배)가 잘리지 않을 여유를 둔다.
    margin = int(style["font_size"] * 0.6)
    x0 = max(int((width - max_line_width) / 2) - margin, 0)
    y0 = max(baseline - margin, 0)
    x1 = min(int((width + max_line_width) / 2) + margin, width)
    y1 = min(baseline + block_height + margin, height)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cropped = image.crop((x0, y0, x1, y1))
    cropped.save(out_path)
    return out_path, (x0, y0, x1 - x0, y1 - y0)


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

    stroke = int(style.get("stroke_width", 0) or 0)
    for segment, highlighted in segments:
        color = style["emphasis_color"] if highlighted else style["font_color"]
        # 예전엔 외곽선 없이 그림자만 썼다(작은 자막엔 그게 깔끔했다). 2026-10-27부터 자막을
        # 크게 키우면서 실사 영상(하늘·파도·불빛) 위에서도 읽히게 검은 외곽선을 두른다.
        _draw_text_with_shadow(
            draw, (x, y), segment, font, color, shadow_blur=style.get("shadow_blur", 6)
        )
        if stroke > 0:
            draw.text(
                (x, y), segment, font=font, fill=color,
                stroke_width=stroke, stroke_fill=(0, 0, 0, 255),
            )
        x += draw.textlength(segment, font=font)


def _draw_line_with_active_word(
    draw: ImageDraw.ImageDraw,
    text: str,
    emphasis: list[str],
    font: ImageFont.FreeTypeFont,
    width: int,
    y: int,
    style: dict,
    active: int,
) -> None:
    """지금 읽는 단어 뒤에 색 박스를 깔고(흰 글씨), 강조어는 노란색으로 — 레퍼런스 자막 방식."""
    tokens = text.split()
    space = draw.textlength(" ", font=font)
    widths = [draw.textlength(token, font=font) for token in tokens]
    total = sum(widths) + space * max(len(tokens) - 1, 0)
    x = (width - total) / 2
    stroke = int(style.get("stroke_width", 0) or 0)
    emphasis_words = {
        word.strip(".,;:¿?¡!\"'").lower()
        for phrase in emphasis for word in phrase.split()
        if len(word.strip(".,;:¿?¡!\"'")) >= 3
    }
    size = font.size
    for index, (token, token_width) in enumerate(zip(tokens, widths)):
        is_active = index == active
        if is_active:
            pad_x, pad_top, pad_bottom = size * 0.14, size * 0.08, size * 0.22
            draw.rounded_rectangle(
                (x - pad_x, y - pad_top, x + token_width + pad_x, y + size + pad_bottom),
                radius=int(size * 0.18),
                fill=style.get("highlight_box_color", "#E3245B"),
            )
            color = style["font_color"]
        elif token.strip(".,;:¿?¡!\"'").lower() in emphasis_words:
            color = style["emphasis_color"]
        else:
            color = style["font_color"]
        if not is_active:
            _draw_text_with_shadow(
                draw, (x, y), token, font, color, shadow_blur=style.get("shadow_blur", 6)
            )
        draw.text(
            (x, y), token, font=font, fill=color,
            stroke_width=0 if is_active else stroke, stroke_fill=(0, 0, 0, 255),
        )
        x += token_width + space


def _split_emphasis(text: str, emphasis: list[str]) -> list[tuple[str, bool]]:
    """강조 조각을 기준으로 문장을 (텍스트, 강조여부) 조각들로 나눈다.

    자막이 2~3단어 묶음으로 잘게 나뉘면서(2026-10-27) 강조 구절이 묶음 경계에 걸려 통째로는
    안 맞는 경우가 많다 — 그럴 땐 구절 안의 단어(4글자 이상) 단위로라도 칠한다.
    """
    phrases = [item for item in emphasis if item.strip()]
    if phrases and not any(phrase in text for phrase in phrases):
        words = {
            word.strip(".,;:¿?¡!\"'").lower()
            for phrase in phrases
            for word in phrase.split()
            if len(word.strip(".,;:¿?¡!\"'")) >= 4
        }
        tokens = text.split(" ")
        out: list[tuple[str, bool]] = []
        for index, token in enumerate(tokens):
            piece = token + (" " if index < len(tokens) - 1 else "")
            hit = token.strip(".,;:¿?¡!\"'").lower() in words
            if out and out[-1][1] == hit:
                out[-1] = (out[-1][0] + piece, hit)
            else:
                out.append((piece, hit))
        return out or [(text, False)]

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


def render_center_overlay(
    text: str, out_path: Path, size: tuple[int, int], color: str = "#FFFFFF"
) -> Path:
    """문장 동안 화면 한가운데 크게 박는 숫자/단어("1978", "98m").

    레퍼런스 채널이 핵심 숫자를 굵은 산세리프로 화면 중앙에 띄우는 방식이다. 자막(하단)과
    상단 타이틀 사이, 세로 42% 지점에 둔다.
    """
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    font_size = int(width * 0.2)
    font = _font(font_size)
    while draw.textlength(text, font=font) > width * 0.84 and font_size > 48:
        font_size = int(font_size * 0.9)
        font = _font(font_size)

    text_width = draw.textlength(text, font=font)
    _draw_text_with_shadow(
        draw,
        ((width - text_width) / 2, height * 0.42 - font_size / 2),
        text,
        font,
        color,
        shadow_blur=12,
        shadow_alpha=220,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path


def render_hook_question(
    text: str,
    out_path: Path,
    size: tuple[int, int],
    color: str = "#FFFFFF",
    stat: str = "",
    stat_color: str = "#FFD84D",
) -> Path:
    """첫 3초 화면 정중앙의 큰 질문 문구("¿Sabías esto?") — 2026-10-26 사용자 지시.

    스페인어권 시청자는 짧고 직설적인 질문에 반응한다 — 첫 화면 한가운데 꽂아 이탈을 줄인다.
    굵은 산세리프, 최대 두 줄, 세로 중앙.
    """
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 2026-10-27: 숫자(stat)가 같이 뜨면 질문은 한 단계 작게, 덩어리 전체를 화면 36% 높이에 —
    # 아래쪽(60% 부근)은 첫 문장 자막 자리라 겹치면 안 된다.
    font_size = int(width * (0.115 if stat else 0.13))
    font = _font(font_size)
    lines = _wrap_lines(draw, text, font, width * 0.86)
    while (
        len(lines) > 1 and " ".join(lines) != text or any(
            draw.textlength(line, font=font) > width * 0.86 for line in lines
        )
    ) and font_size > 48:
        font_size = int(font_size * 0.9)
        font = _font(font_size)
        lines = _wrap_lines(draw, text, font, width * 0.86)

    line_height = int(font_size * 1.15)
    center = height * 0.36
    top = center - line_height * len(lines) / 2
    if stat:
        # 2026-10-27: 질문 위에 숫자를 가장 크게(노란색) — 소리 없이 넘기는 사람(모바일 60% 이상)도
        # 0.5초 안에 "얼마나 큰 일인지"를 본다. 숫자 줄은 크게, 단위 낱말("MUERTOS")은 그 아래
        # 한 단계 작게. 숫자+단위+질문 덩어리 전체를 세로 중앙에 둔다.
        words = stat.split()
        number = next((w for w in words if any(ch.isdigit() for ch in w)), words[0])
        unit = " ".join(w for w in words if w is not number)
        rows = []  # (텍스트, 폰트, 높이)
        for text_row, ratio in ((number, 0.22), (unit, 0.10)):
            if not text_row:
                continue
            row_size = int(width * ratio)
            row_font = _font(row_size)
            while draw.textlength(text_row, font=row_font) > width * 0.9 and row_size > 48:
                row_size = int(row_size * 0.9)
                row_font = _font(row_size)
            rows.append((text_row, row_font, int(row_size * 1.12)))
        block = sum(row[2] for row in rows) + int(font_size * 0.3) + line_height * len(lines)
        cursor = center - block / 2
        for text_row, row_font, row_height in rows:
            row_width = draw.textlength(text_row, font=row_font)
            _draw_text_with_shadow(
                draw, ((width - row_width) / 2, cursor), text_row, row_font, stat_color,
                shadow_blur=16, shadow_alpha=235,
            )
            draw.text(
                ((width - row_width) / 2, cursor), text_row, font=row_font, fill=stat_color,
                stroke_width=max(int(row_font.size * 0.06), 3), stroke_fill=(0, 0, 0, 255),
            )
            cursor += row_height
        top = cursor + int(font_size * 0.3)
    for index, line in enumerate(lines):
        line_width = draw.textlength(line, font=font)
        _draw_text_with_shadow(
            draw,
            ((width - line_width) / 2, top + index * line_height),
            line,
            font,
            color,
            shadow_blur=14,
            shadow_alpha=230,
        )
        # 밝은 하늘·파도 위에서도 읽히게 굵은 검은 테두리를 한 번 더 두른다.
        draw.text(
            ((width - line_width) / 2, top + index * line_height),
            line,
            font=font,
            fill=color,
            stroke_width=max(int(font_size * 0.07), 3),
            stroke_fill=(0, 0, 0, 255),
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> list[str]:
    """단어 단위로 최대 두 줄까지 접는다. 한국어는 공백이 성기니 어절 단위로 접어도
    자연스럽다."""
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:2]


def render_title_banner(
    text: str,
    out_path: Path,
    size: tuple[int, int],
    color: str = "#FFFFFF",
    accent_color: str = "#FFD84D",
) -> Path:
    """포스터형 레이아웃의 상단 후킹 문구. 참고 이미지처럼 화면 내내 떠 있는 굵은
    두 줄짜리 타이틀이다 — 첫 2~3초만 반짝이고 사라지는 render_hook_number 와 달리
    영상 전체를 붙잡아야 하는 자리라 따로 만든다.
    """
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    bold = assets.FONT_SANS if assets.FONT_SANS.exists() else None
    font_size = int(width * 0.088)
    font = ImageFont.truetype(str(bold), font_size) if bold else _font(font_size)
    max_width = width * 0.88

    lines = _wrap_lines(draw, text, font, max_width)
    while len(lines) > 2 and font_size > 36:
        font_size = int(font_size * 0.92)
        font = ImageFont.truetype(str(bold), font_size) if bold else _font(font_size)
        lines = _wrap_lines(draw, text, font, max_width)

    line_height = int(font_size * 1.28)
    total_height = line_height * len(lines)
    top = height * 0.07

    for index, line in enumerate(lines):
        # 두 번째 줄이 있으면(핵심 워딩) 강조색을 쓴다 — 참고 이미지의 "최강 피지컬
        # 사나이"처럼 두 번째 줄이 임팩트를 담는 경우가 많다.
        line_color = accent_color if (index == 1 and len(lines) > 1) else color
        line_width = draw.textlength(line, font=font)
        x = (width - line_width) / 2
        y = top + index * line_height
        _draw_text_with_shadow(draw, (x, y), line, font, line_color, shadow_blur=8, shadow_alpha=200)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path


def render_frame_border(
    out_path: Path,
    size: tuple[int, int],
    box: tuple[int, int, int, int],
    color: str = "#E8C97A",
    width_px: int = 4,
    radius: int = 18,
) -> Path:
    """액자 박스 테두리 한 장. 프레임마다 다시 그리지 않고 전체 구간에 얹는다."""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(
        (x1, y1, x2, y2), radius=radius, outline=color, width=width_px
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
