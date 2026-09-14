"""Veo 클립과 파랄랙스 컷의 톤을 하나로 묶는 마감 처리.

두 경로는 태생이 다르다. 한쪽은 생성 영상이고 한쪽은 사진을 움직인 것이라, 그냥 이어
붙이면 컷이 바뀔 때마다 다른 영상처럼 보인다. 같은 그레인·비네트·그레이드를 양쪽에
똑같이 입혀 한 카메라로 찍은 것처럼 만든다.

마스크와 그레인 타일은 한 번만 만들어 재사용한다. 프레임마다 새로 만들면 렌더가 몇 배로
느려진다.
"""

from __future__ import annotations

import numpy as np
from moviepy import VideoClip

GRAIN_TILES = 8
DEFAULT_GRAIN = 6.0
DEFAULT_VIGNETTE = 0.30
# 섀도를 살짝 들어올리고 하이라이트를 차갑게 — 다큐에서 흔한 정리된 톤.
LIFT = np.array([6.0, 6.0, 10.0], dtype=np.float32)
GAIN = np.array([1.01, 1.0, 0.97], dtype=np.float32)


def apply_film_look(
    clip: VideoClip,
    size: tuple[int, int],
    grain: float = DEFAULT_GRAIN,
    vignette: float = DEFAULT_VIGNETTE,
    exposure_breathing: float = 0.02,
) -> VideoClip:
    width, height = size
    mask = _vignette_mask(width, height, vignette)
    tiles = _grain_tiles(width, height, grain)

    def transform(get_frame, t: float) -> np.ndarray:
        frame = get_frame(t).astype(np.float32)

        # 자동노출이 미세하게 도는 느낌. 완전히 일정한 밝기는 실사에 없다.
        breath = 1.0 + exposure_breathing * np.sin(t * 1.7)
        frame = frame * GAIN * breath + LIFT
        frame *= mask
        frame += tiles[int(t * 24) % GRAIN_TILES]

        return np.clip(frame, 0, 255).astype(np.uint8)

    return clip.transform(transform, apply_to=[])


def _vignette_mask(width: int, height: int, strength: float) -> np.ndarray:
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    dx = (xs - width / 2) / (width / 2)
    dy = (ys - height / 2) / (height / 2)
    radius = np.sqrt(dx * dx + dy * dy) / np.sqrt(2)
    mask = 1.0 - strength * np.clip(radius, 0, 1) ** 2
    return mask[:, :, None].astype(np.float32)


def _grain_tiles(width: int, height: int, amount: float) -> list[np.ndarray]:
    rng = np.random.default_rng(7)
    return [
        rng.normal(0.0, amount, size=(height, width, 1)).astype(np.float32)
        for _ in range(GRAIN_TILES)
    ]
