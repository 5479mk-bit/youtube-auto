"""컷 한 장을 다크 시네마틱 화면으로 만드는 렌더러 셋.

조선 사건에는 사진이 없다. 그래서 화면을 네 갈래로 만든다.

- `render_doc_scene`        한지 위에 실록 원문을 얹고 붉은 먹줄이 그어진다
- `render_map_scene`        대동여지도를 파고들며 고을에 핏빛 잉크가 번진다
- `render_ai_image_motion`  생성 정지화에 느린 푸시인 + 밤안개 + 다크 그레이딩
- `render_fallback_plate`   생성이 막혔을 때(무료 티어는 할당량 0) 쓰는 먹빛 배경

ffmpeg 필터(zoompan/drawbox/vignette) 대신 moviepy+numpy 로 만든다. 파이프라인이 이미
moviepy 클립을 이어 붙이는 구조라, 컷마다 mp4 를 중간 파일로 떨구고 다시 읽으면 재인코딩이
컷 수만큼 늘어난다. 하는 일(줌인·밑줄·비네트)은 같다.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import numpy as np
from loguru import logger
from moviepy import ImageClip, VideoClip, VideoFileClip
from PIL import Image, ImageDraw, ImageFont

from .. import assets

# 먹으로 그은 밑줄·잉크 자국 색. 순수한 빨강은 촌스러워서 검붉게 쓴다.
INK_RED = (156, 28, 28)
# 지도에서 각 지역이 있는 자리(가로, 세로 비율). 대동여지도 전도 기준 대략값이다 —
# 측량값이 아니라 "이 근처를 파고들어라"는 연출용 좌표다.
REGION_ANCHORS: dict[str, tuple[float, float]] = {
    "GANGWON": (0.56, 0.47),
    "HANYANG": (0.43, 0.53),
    "GYEONGSANG": (0.60, 0.65),
    "JEOLLA": (0.38, 0.72),
    "PYEONGAN": (0.33, 0.22),
}
# 샘플 기사에 나오는 고을들. 없는 이름은 지역 중심 근처로 흩뿌린다.
SPOT_ANCHORS: dict[str, tuple[float, float]] = {
    "간성": (0.60, 0.43),
    "양양": (0.61, 0.45),
    "강릉": (0.62, 0.47),
    "춘천": (0.50, 0.47),
    "원주": (0.48, 0.52),
    "한양": (0.43, 0.53),
    "서울": (0.43, 0.53),
}


def render_doc_scene(
    duration: float,
    size: tuple[int, int],
    title: str,
    highlight: str,
    body: str = "",
) -> VideoClip:
    """사료 컷: 한지에 원문을 얹고, 강조 구절 아래로 붉은 먹줄이 그어진다."""
    base = _hanji_background(size)
    plate, underline_box = _draw_document(base, size, title, highlight, body)
    frame = np.asarray(plate, dtype=np.uint8)

    x0, y0, x1, y1 = underline_box
    # 먹줄은 컷의 앞부분(35%~85%)에 걸쳐 그어진다. 처음부터 그어져 있으면 눈이 안 간다.
    draw_start, draw_end = 0.35, 0.85

    def make_frame(t: float) -> np.ndarray:
        progress = np.clip((t / duration - draw_start) / (draw_end - draw_start), 0.0, 1.0)
        image = frame.copy()
        if progress > 0 and x1 > x0:
            end = x0 + int((x1 - x0) * progress)
            image[y0:y1, x0:end] = np.array(INK_RED, dtype=np.uint8)
        # 아주 천천히 파고든다(마크로 줌인). 종이는 움직임이 크면 싸구려로 보인다.
        return _zoom(image, 1.0 + 0.05 * (t / max(duration, 0.01)))

    return VideoClip(make_frame, duration=duration)


def render_map_scene(
    duration: float,
    size: tuple[int, int],
    region: str,
    spots: list[str],
) -> VideoClip:
    """지도 컷: 언급된 지역으로 줌인하면서 고을마다 핏빛 잉크가 번진다."""
    anchor = REGION_ANCHORS.get(region.upper(), (0.5, 0.5))
    plate, window = _map_plate(size, anchor)
    frame = np.asarray(plate, dtype=np.uint8)
    points = [_spot_point(spot, window, size, index) for index, spot in enumerate(spots or [""])]

    def make_frame(t: float) -> np.ndarray:
        image = frame.copy()
        ratio = t / max(duration, 0.01)
        for index, point in enumerate(points):
            # 잉크는 하나씩 차례로 번진다. 동시에 찍히면 무슨 일이 일어나는지 안 보인다.
            appear = 0.15 + 0.62 * (index / max(len(points), 1))
            grow = np.clip((ratio - appear) / 0.25, 0.0, 1.0)
            if grow > 0:
                _stamp_ink(image, point, grow)
        return _zoom(image, 1.0 + 0.08 * ratio)

    return VideoClip(make_frame, duration=duration)


def render_ai_image_motion(
    image_path: Path,
    duration: float,
    size: tuple[int, int],
    fog_opacity: float = 0.0,
    saturation: float = 1.0,
    contrast: float = 1.0,
    brightness: float = 0.0,
    vignette: float = 0.0,
    zoom_amount: float = 0.15,
) -> VideoClip:
    """생성 정지화를 컷으로: 느린 푸시인 + (선택적) 색 보정.

    기본값은 그레이딩을 걸지 않는다 — 3D 엔지니어링 단면도(밝은 스튜디오 배경, 붉은
    기술선)를 조선시대/CCTV 콘셉트 시절의 "밤안개 + 채도를 뺀 어두운 톤"으로 덮으면
    생성 이미지가 아무리 역동적이어도 화면이 칙칙하고 정적으로 보인다 — 실제로
    사용자가 지적한 "여전히 정적인 화면"의 원인이 이 하드코딩된 그레이딩이었다.
    사료 콘셉트로 되돌아갈 때는 호출부에서 fog_opacity/saturation/vignette 값을
    설정으로 다시 올리면 된다(코드는 그대로 재사용 가능).
    """
    base = _fit(Image.open(image_path).convert("RGB"), size)
    pixels = np.asarray(base, dtype=np.float32)
    if saturation != 1.0 or contrast != 1.0 or brightness != 0.0 or vignette != 0.0:
        frame = _grade(pixels, saturation=saturation, contrast=contrast, brightness=brightness, vignette=vignette)
    else:
        frame = pixels

    def make_frame(t: float) -> np.ndarray:
        return _zoom(frame.astype(np.uint8), 1.0 + zoom_amount * (t / max(duration, 0.01)))

    clip = VideoClip(make_frame, duration=duration)
    fog = _fog_layer(duration, size, fog_opacity) if fog_opacity > 0 else None
    if fog is None:
        return clip

    from moviepy import CompositeVideoClip

    return CompositeVideoClip([clip, fog], size=size).with_duration(duration)


def render_fallback_plate(
    duration: float,
    size: tuple[int, int],
    seed_text: str,
    camera: str = "push_in",
) -> VideoClip:
    """생성이 막혔을 때 쓰는 대체 화면.

    단색으로 때우지 않는다 — 먹이 번진 듯한 어두운 배경에 안개를 얹어, 최소한 톤은 무너지지
    않게 한다. 엉뚱한 현대 사진을 끌어오는 것보다 이게 낫다.
    """
    plate = _ink_plate(size, seed_text)
    frame = np.asarray(plate, dtype=np.uint8)
    direction = -1.0 if camera == "pull_out" else 1.0

    def make_frame(t: float) -> np.ndarray:
        ratio = t / max(duration, 0.01)
        return _zoom(frame, 1.0 + 0.05 * (ratio if direction > 0 else (1.0 - ratio)))

    clip = VideoClip(make_frame, duration=duration)
    fog = _fog_layer(duration, size, 0.45)
    if fog is None:
        return clip

    from moviepy import CompositeVideoClip

    return CompositeVideoClip([clip, fog], size=size).with_duration(duration)


# ---------------------------------------------------------------- 내부 도구


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _doc_font_path() -> Path | None:
    """사료 컷용 명조. 한자가 있는 쪽을 먼저 쓴다."""
    for path in (assets.FONT_SERIF_HANJA, assets.FONT_MYEONGJO):
        if path.exists():
            return path
    return None


@lru_cache(maxsize=8)
def _cmap(font_path: Path) -> frozenset[int]:
    try:
        from fontTools.ttLib import TTFont

        codes: set[int] = set()
        for table in TTFont(str(font_path), fontNumber=0)["cmap"].tables:
            codes |= set(table.cmap.keys())
        return frozenset(codes)
    except Exception as exc:
        logger.warning(f"폰트 글자표를 읽지 못했습니다({font_path.name}): {exc}")
        return frozenset()


def _font_for(char: str, primary: Path) -> Path:
    """글자 하나를 그릴 폰트.

    PIL 은 폰트에 없는 글자를 **아무것도 안 그린다** — 빈 자리가 생길 뿐 오류도 안 난다.
    처음엔 한자가 통째로 사라진 것도 모르고 지나갔다. 한글은 KR 명조가, 실록의 벽자
    (예: 壼)는 보조 폰트가 담당하도록 글자마다 고른다.
    """
    if ord(char) in _cmap(primary):
        return primary
    if assets.FONT_SERIF_HANJA_EXTRA.exists() and ord(char) in _cmap(
        assets.FONT_SERIF_HANJA_EXTRA
    ):
        return assets.FONT_SERIF_HANJA_EXTRA
    return primary


def _mixed_width(draw: ImageDraw.ImageDraw, text: str, primary: Path, size_px: int) -> float:
    return sum(
        draw.textlength(char, font=_font(_font_for(char, primary), size_px)) for char in text
    )


def _draw_mixed(
    draw: ImageDraw.ImageDraw,
    text: str,
    primary: Path,
    size_px: int,
    origin: tuple[float, float],
    fill: tuple[int, int, int],
) -> float:
    """글자마다 되는 폰트로 그린다. 그린 너비를 돌려준다."""
    x, y = origin
    for char in text:
        font = _font(_font_for(char, primary), size_px)
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font)
    return x - origin[0]


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """세로 프레임을 가득 채우도록 잘라 맞춘다 (비율 유지)."""
    width, height = size
    scale = max(width / image.width, height / image.height)
    resized = image.resize((max(int(image.width * scale), width), max(int(image.height * scale), height)))
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _hanji_background(size: tuple[int, int]) -> Image.Image:
    if assets.TEXTURE_HANJI.exists():
        return _fit(Image.open(assets.TEXTURE_HANJI).convert("RGB"), size)
    logger.warning("한지 텍스처가 없어 단색으로 대체합니다 (./download_assets.sh)")
    return Image.new("RGB", size, (206, 190, 158))


def _draw_document(
    base: Image.Image,
    size: tuple[int, int],
    title: str,
    highlight: str,
    body: str,
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """한지 위에 제목·원문을 앉히고, 밑줄을 그을 사각형 좌표를 같이 돌려준다."""
    width, height = size
    image = base.copy()
    draw = ImageDraw.Draw(image)
    font_path = _doc_font_path()
    if font_path is None:
        logger.warning("명조 폰트가 없어 사료 컷 글자를 건너뜁니다 (./download_assets.sh)")
        return image, (0, 0, 0, 0)

    ink = (38, 30, 24)
    if title:
        title_px = int(width * 0.075)
        title_width = _mixed_width(draw, title, font_path, title_px)
        _draw_mixed(draw, title, font_path, title_px, ((width - title_width) / 2, height * 0.24), ink)

    if not highlight:
        return image, (0, 0, 0, 0)

    # 강조 구절은 크게. 사료 컷의 주인공이다.
    size_px = int(width * 0.14)
    while _mixed_width(draw, highlight, font_path, size_px) > width * 0.80 and size_px > 40:
        size_px = int(size_px * 0.9)

    text_width = _mixed_width(draw, highlight, font_path, size_px)
    x = (width - text_width) / 2
    y = height * 0.42
    _draw_mixed(draw, highlight, font_path, size_px, (x, y), ink)

    if body:
        body_px = int(width * 0.042)
        for index, chunk in enumerate(_wrap(body, 22)[:3]):
            chunk_width = _mixed_width(draw, chunk, font_path, body_px)
            _draw_mixed(
                draw,
                chunk,
                font_path,
                body_px,
                ((width - chunk_width) / 2, height * 0.60 + index * size_px * 0.42),
                (70, 58, 48),
            )

    underline_top = int(y + size_px * 1.22)
    return image, (int(x), underline_top, int(x + text_width), underline_top + max(int(size_px * 0.07), 5))


def _wrap(text: str, per_line: int) -> list[str]:
    return [text[i : i + per_line] for i in range(0, len(text), per_line)]


def _map_plate(
    size: tuple[int, int], anchor: tuple[float, float]
) -> tuple[Image.Image, tuple[float, float, float, float]]:
    """지도를 어둡게 깔고 지목한 지역을 화면 가운데로 잘라낸다.

    자른 창(원본에서 차지하는 비율)을 같이 돌려준다 — 고을 자리를 화면 좌표로 옮기려면
    이 값이 있어야 한다. 이걸 안 넘겨서 처음엔 잉크가 엉뚱한 데 찍혔다.
    """
    width, height = size
    if not assets.MAP_DAEDONG.exists():
        logger.warning("고지도가 없어 먹빛 배경으로 대체합니다 (./download_assets.sh)")
        return _ink_plate(size, "map"), (0.0, 0.0, 1.0, 1.0)

    source = Image.open(assets.MAP_DAEDONG)
    if source.mode == "RGBA":
        # 지도 낱장이 없는 자리가 투명이다. 그냥 RGB 로 바꾸면 흰 덩어리가 돼서 화면이
        # 깨진다 — 어두운 바탕 위에 얹어 그 자리가 어둠으로 보이게 한다.
        backdrop = Image.new("RGB", source.size, (18, 18, 24))
        backdrop.paste(source, mask=source.split()[3])
        source = backdrop
    else:
        source = source.convert("RGB")

    # 지도의 3분의 1 정도를 잘라 써야 고을 이름이 읽힐 만큼 커진다.
    crop_h = int(source.height * 0.34)
    crop_w = int(crop_h * width / height)
    left = int(np.clip(source.width * anchor[0] - crop_w / 2, 0, max(source.width - crop_w, 0)))
    top = int(np.clip(source.height * anchor[1] - crop_h / 2, 0, max(source.height - crop_h, 0)))
    cropped = source.crop((left, top, left + crop_w, top + crop_h)).resize(size)

    # 종이 지도는 밝아서 그대로 쓰면 톤이 깨진다. 눌러서 어둡게.
    plate = _grade(
        np.asarray(cropped, dtype=np.float32), saturation=0.40, contrast=1.10, brightness=-0.42
    )
    window = (
        left / source.width,
        top / source.height,
        crop_w / source.width,
        crop_h / source.height,
    )
    return Image.fromarray(plate.astype(np.uint8)), window


def _spot_point(
    spot: str, window: tuple[float, float, float, float], size: tuple[int, int], index: int
) -> tuple[int, int]:
    """고을 자리. 아는 이름은 제 자리에, 모르는 이름은 화면 안에 흩뿌린다."""
    width, height = size
    known = SPOT_ANCHORS.get(spot)
    if known is not None:
        left, top, span_x, span_y = window
        x = (known[0] - left) / (span_x or 1.0)
        y = (known[1] - top) / (span_y or 1.0)
        return int(np.clip(x, 0.10, 0.90) * width), int(np.clip(y, 0.15, 0.85) * height)

    # 이름을 모르면 자리를 지어내지 않는다 — 대신 화면 안에 고르게 퍼뜨린다.
    seed = int(hashlib.sha1(f"{spot}{index}".encode()).hexdigest()[:6], 16)
    rng = np.random.default_rng(seed)
    return int(rng.uniform(0.25, 0.75) * width), int(rng.uniform(0.3, 0.7) * height)


def _stamp_ink(image: np.ndarray, point: tuple[int, int], grow: float) -> None:
    """붉은 잉크가 번지는 자국 하나. grow 0~1 로 커지며 옅어진다."""
    height, width = image.shape[:2]
    radius = int(width * 0.035 * (0.4 + grow))
    cx, cy = point
    y0, y1 = max(cy - radius, 0), min(cy + radius, height)
    x0, x1 = max(cx - radius, 0), min(cx + radius, width)
    if y1 <= y0 or x1 <= x0:
        return

    ys = np.arange(y0, y1)[:, None] - cy
    xs = np.arange(x0, x1)[None, :] - cx
    dist = np.sqrt(xs**2 + ys**2) / max(radius, 1)
    mask = np.clip(1.0 - dist, 0.0, 1.0) ** 0.6 * min(grow * 1.4, 1.0)

    patch = image[y0:y1, x0:x1].astype(np.float32)
    ink = np.array(INK_RED, dtype=np.float32)[None, None, :]
    image[y0:y1, x0:x1] = (patch * (1 - mask[:, :, None]) + ink * mask[:, :, None]).astype(np.uint8)


def _ink_plate(size: tuple[int, int], seed_text: str) -> Image.Image:
    """먹이 번진 듯한 어두운 배경. 컷마다 다르게 나오도록 글자로 씨앗을 만든다."""
    width, height = size
    seed = int(hashlib.sha1(seed_text.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)

    noise = rng.normal(0.5, 0.25, (height // 8, width // 8))
    blurred = Image.fromarray(np.clip(noise * 255, 0, 255).astype(np.uint8)).filter(
        __import__("PIL.ImageFilter", fromlist=["ImageFilter"]).GaussianBlur(12)
    )
    field = np.asarray(blurred.resize(size), dtype=np.float32) / 255.0
    field = (field - field.mean()) / (field.std() or 1.0)

    ys = np.linspace(-1, 1, height)[:, None]
    xs = np.linspace(-1, 1, width)[None, :]
    vignette = 1.0 - 0.55 * np.clip((xs**2 + ys**2) / 1.8, 0, 1)

    # 아주 어두운 남빛. 검정에 가깝되 완전히 죽지 않게.
    base = np.array((26, 28, 36), dtype=np.float32)[None, None, :]
    pixels = base * (1.0 + field * 0.35)[:, :, None] * vignette[:, :, None]
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


def _grade(
    pixels: np.ndarray,
    saturation: float = 0.70,
    contrast: float = 1.20,
    brightness: float = -0.05,
    vignette: float = 0.42,
) -> np.ndarray:
    """채도/대비/밝기와 비네팅 세기를 조절한다. 사료(어두운 다큐) 콘셉트의 기본값은
    이렇게 강했지만, 3D 엔지니어링 단면도 콘셉트는 호출부(`render_ai_image_motion`)에서
    전부 중립값을 넘겨 이 함수 자체를 사실상 건너뛴다."""
    grey = pixels.mean(axis=2, keepdims=True)
    graded = grey + (pixels - grey) * saturation
    graded = (graded / 255.0 - 0.5) * contrast + 0.5 + brightness
    graded = np.clip(graded * 255.0, 0, 255)

    height, width = graded.shape[:2]
    ys = np.linspace(-1, 1, height)[:, None]
    xs = np.linspace(-1, 1, width)[None, :]
    vignette_field = 1.0 - vignette * np.clip((xs**2 + ys**2) / 1.9, 0, 1)
    return graded * vignette_field[:, :, None]


def _zoom(frame: np.ndarray, scale: float) -> np.ndarray:
    """가운데를 기준으로 조금 확대한다 (푸시인)."""
    if scale <= 1.001:
        return frame
    height, width = frame.shape[:2]
    crop_h, crop_w = int(height / scale), int(width / scale)
    top = (height - crop_h) // 2
    left = (width - crop_w) // 2
    cropped = Image.fromarray(frame[top : top + crop_h, left : left + crop_w])
    return np.asarray(cropped.resize((width, height)), dtype=np.uint8)


def _fog_layer(duration: float, size: tuple[int, int], opacity: float):
    """밤안개 오버레이. 짧으면 잘라 쓰고, 없으면 그냥 건너뛴다."""
    if not assets.OVERLAY_FOG.exists():
        return None
    try:
        fog = VideoFileClip(str(assets.OVERLAY_FOG))
        if fog.duration < duration:
            from moviepy import concatenate_videoclips

            fog = concatenate_videoclips([fog] * (int(duration // fog.duration) + 1))
        return (
            fog.subclipped(0, duration)
            .resized(size)
            .with_opacity(opacity)
            .with_duration(duration)
        )
    except Exception as exc:
        logger.warning(f"안개 오버레이를 쓰지 못했습니다: {exc}")
        return None


def still_from_clip(clip, out_path: Path) -> Path:
    """디버그용: 컷 한가운데 프레임을 그림으로 떨군다."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ImageClip(clip.get_frame(clip.duration / 2)).save_frame(str(out_path))
    return out_path
