"""깊이 기반 2.5D 파랄랙스 — 사진 한 장에서 카메라가 장면 안으로 들어간다.

평면을 확대/이동하는 Ken-Burns 와 다른 점은, 픽셀마다 깊이를 알고 움직인다는 것이다.
가까운 것은 크게, 먼 것은 작게 움직이므로 원근이 실제로 어긋나며 지나간다.

한계는 분명하다. 장면 속 사람이나 차가 걷거나 달리지는 않는다. 움직이는 건 카메라뿐이다.
그래서 시청 이탈이 결정되는 도입부에는 Veo 클립을 쓰고, 나머지를 이걸로 채운다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from loguru import logger
from moviepy import VideoClip
from PIL import Image

from ..models import CameraMove
from ..settings import RESOURCE_DIR

MODEL_PATH = RESOURCE_DIR / "models" / "depth_anything_v2_small.onnx"
MODEL_URL = (
    "https://huggingface.co/onnx-community/depth-anything-v2-small/"
    "resolve/main/onnx/model.onnx"
)
MODEL_INPUT_SIZE = 518
# 깊이에 따라 벌어지는 최대 시차(화면 폭 대비). 너무 키우면 가장자리가 찢어져 보인다.
DEFAULT_PARALLAX = 0.06
DEFAULT_DOLLY = 0.12

_session = None


def ensure_depth_model() -> Path:
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 0:
        return MODEL_PATH

    import requests

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.info("깊이 추정 모델을 내려받습니다 (최초 1회, 약 100MB)")
    response = requests.get(MODEL_URL, timeout=600)
    response.raise_for_status()
    MODEL_PATH.write_bytes(response.content)
    return MODEL_PATH


def _get_session():
    global _session
    if _session is None:
        import onnxruntime as ort

        _session = ort.InferenceSession(
            str(ensure_depth_model()), providers=["CPUExecutionProvider"]
        )
    return _session


def estimate_depth(image: Image.Image) -> np.ndarray:
    """0(멀다)~1(가깝다) 로 정규화한 깊이 맵."""
    session = _get_session()

    resized = image.convert("RGB").resize(
        (MODEL_INPUT_SIZE, MODEL_INPUT_SIZE), Image.BILINEAR
    )
    array = np.asarray(resized, dtype=np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    array = (array - mean) / std
    tensor = array.transpose(2, 0, 1)[None]

    outputs = session.run(None, {session.get_inputs()[0].name: tensor})
    depth = np.squeeze(outputs[0]).astype(np.float32)

    spread = depth.max() - depth.min()
    if spread <= 0:
        return np.zeros_like(depth)
    return (depth - depth.min()) / spread


def make_parallax_clip(
    image_path: Path,
    duration: float,
    camera: CameraMove,
    size: tuple[int, int],
    parallax: float = DEFAULT_PARALLAX,
    dolly: float = DEFAULT_DOLLY,
) -> VideoClip:
    width, height = size
    frame = _load_cover(image_path, width, height)

    with Image.open(image_path) as source:
        depth_small = estimate_depth(source)
    depth = np.asarray(
        Image.fromarray((depth_small * 255).astype(np.uint8)).resize(
            (width, height), Image.BILINEAR
        ),
        dtype=np.float32,
    ) / 255.0

    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    center_x, center_y = width / 2.0, height / 2.0
    flat = frame.reshape(-1, 3)

    def frame_function(t: float) -> np.ndarray:
        progress = 0.0 if duration <= 0 else min(max(t / duration, 0.0), 1.0)
        eased = _ease_in_out(progress)
        src_x, src_y = _sample_grid(
            camera, eased, xs, ys, depth, center_x, center_y, width, height, parallax, dolly
        )

        # 이동량만큼 살짝 확대해서(오버스캔) 화면 밖을 덜 끌어온다 — 움직임을 키우면
        # 가장자리 픽셀이 늘어나 번지는 게 보이기 때문이다(2026-10-19).
        overscan = 1.0 + parallax
        src_x = center_x + (src_x - center_x) / overscan
        src_y = center_y + (src_y - center_y) / overscan

        # 역방향 샘플링이라 구멍이 거의 생기지 않는다. 가장자리는 잘라내지 않고 붙든다.
        xi = np.clip(src_x, 0, width - 1).astype(np.int32)
        yi = np.clip(src_y, 0, height - 1).astype(np.int32)
        return flat[(yi * width + xi).ravel()].reshape(height, width, 3)

    return VideoClip(frame_function=frame_function, duration=duration)


def _sample_grid(
    camera: CameraMove,
    eased: float,
    xs: np.ndarray,
    ys: np.ndarray,
    depth: np.ndarray,
    center_x: float,
    center_y: float,
    width: int,
    height: int,
    parallax: float,
    dolly: float,
):
    if camera in {"push_in", "pull_out", "freeze"}:
        # 깊이에 비례해 배율을 달리 주면 가까운 것이 더 빨리 다가온다 = 돌리 인.
        amount = dolly * (eased if camera == "push_in" else 1.0 - eased)
        if camera == "freeze":
            amount = dolly * 0.5
        scale = 1.0 + amount * (0.35 + depth)
        return center_x + (xs - center_x) / scale, center_y + (ys - center_y) / scale

    shift = parallax * width * (eased - 0.5) * 2.0
    if camera == "pan_lr":
        return xs - shift * depth, ys
    if camera == "pan_rl":
        return xs + shift * depth, ys
    # pan_tb
    shift = parallax * height * (eased - 0.5) * 2.0
    return xs, ys - shift * depth


def _ease_in_out(progress: float) -> float:
    """등속 이동은 기계가 민 것처럼 보인다. 가감속이 있어야 사람이 든 카메라가 된다."""
    return progress * progress * (3.0 - 2.0 * progress)


def _load_cover(image_path: Path, width: int, height: int) -> np.ndarray:
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        source_ratio = image.width / image.height
        target_ratio = width / height

        if source_ratio > target_ratio:
            new_height, new_width = height, int(height * source_ratio)
        else:
            new_width, new_height = width, int(width / source_ratio)

        image = image.resize((new_width, new_height), Image.LANCZOS)
        left = (new_width - width) // 2
        top = (new_height - height) // 2
        return np.asarray(image.crop((left, top, left + width, top + height)))
