"""정지 사진에 가상 카메라 무빙을 입힌다.

원본을 출력 해상도보다 약간 크게 한 번만 키워 두고, 프레임마다 잘라낼 창을
움직인다. 창이 줄면 push-in, 늘면 pull-out, 옆으로 밀면 pan 이다.

훅 컷은 흑백으로 시작해 컬러로 물드는데, 첫 3초에 '무언가 시작된다'는 신호를
주기 위한 장치다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from moviepy import VideoClip
from PIL import Image

from ..models import CameraMove

OVERSCAN = 1.18
HOOK_COLOR_FADE_SECONDS = 1.5


def make_clip(
    image_path: Path,
    duration: float,
    camera: CameraMove,
    size: tuple[int, int],
    zoom_amount: float,
    is_hook: bool = False,
) -> VideoClip:
    width, height = size
    source = _load_cover(image_path, int(width * OVERSCAN), int(height * OVERSCAN))
    source_height, source_width = source.shape[:2]

    def frame_function(t: float) -> np.ndarray:
        progress = 0.0 if duration <= 0 else min(max(t / duration, 0.0), 1.0)
        crop_width, crop_height, left, top = _window(
            camera, progress, source_width, source_height, width, height, zoom_amount
        )

        window = source[top : top + crop_height, left : left + crop_width]
        frame = np.asarray(
            Image.fromarray(window).resize((width, height), Image.LANCZOS)
        )

        if is_hook:
            frame = _colorize(frame, t)
        return frame

    return VideoClip(frame_function=frame_function, duration=duration)


def _window(
    camera: CameraMove,
    progress: float,
    source_width: int,
    source_height: int,
    out_width: int,
    out_height: int,
    zoom_amount: float,
) -> tuple[int, int, int, int]:
    """이번 프레임에 잘라낼 창의 크기와 좌상단 좌표."""
    # 창이 작을수록 확대된 그림이 된다. zoom_amount 는 무빙의 진폭.
    if camera == "push_in":
        scale = 1.0 - zoom_amount * progress
    elif camera == "pull_out":
        scale = 1.0 - zoom_amount * (1.0 - progress)
    else:
        scale = 1.0 - zoom_amount * 0.5

    crop_width = min(int(source_width * scale), source_width)
    crop_height = min(int(crop_width * out_height / out_width), source_height)
    crop_width = min(int(crop_height * out_width / out_height), source_width)

    slack_x = source_width - crop_width
    slack_y = source_height - crop_height

    if camera == "pan_lr":
        left, top = int(slack_x * progress), slack_y // 2
    elif camera == "pan_rl":
        left, top = int(slack_x * (1.0 - progress)), slack_y // 2
    elif camera == "pan_tb":
        left, top = slack_x // 2, int(slack_y * progress)
    elif camera == "freeze":
        left, top = slack_x // 2, slack_y // 2
    else:
        left, top = slack_x // 2, slack_y // 2

    return crop_width, crop_height, left, top


def _colorize(frame: np.ndarray, t: float) -> np.ndarray:
    ratio = min(t / HOOK_COLOR_FADE_SECONDS, 1.0)
    if ratio >= 1.0:
        return frame
    gray = frame @ np.array([0.299, 0.587, 0.114])
    gray_rgb = np.repeat(gray[:, :, None], 3, axis=2)
    return (gray_rgb * (1 - ratio) + frame * ratio).astype(np.uint8)


def _load_cover(image_path: Path, width: int, height: int) -> np.ndarray:
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        source_ratio = image.width / image.height
        target_ratio = width / height

        if source_ratio > target_ratio:
            new_height = height
            new_width = int(height * source_ratio)
        else:
            new_width = width
            new_height = int(width / source_ratio)

        image = image.resize((new_width, new_height), Image.LANCZOS)
        left = (new_width - width) // 2
        top = (new_height - height) // 2
        return np.asarray(image.crop((left, top, left + width, top + height)))
