"""컷 하나를 움직이는 그림으로 바꾼다.

엔진이 둘이지만 파이프라인은 "씬을 주면 클립이 나온다"만 알면 된다.
- veo: 사진을 넣어 실제로 움직이는 영상을 생성한다 (유료)
- parallax: 깊이를 추정해 카메라가 장면 안으로 들어간다 (무료)

사진을 못 구하면 단색으로 때우지 않고 그대로 실패를 알린다.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from moviepy import VideoFileClip

from ..models import PhotoCredit, Scene
from ..motion.parallax import make_parallax_clip
from ..visuals.image_collector import ImageCollector

VEO_PROMPT = (
    "다큐멘터리 영상. {subject}. 카메라가 아주 천천히 움직이고, 장면 속 사람과 차량이"
    " 자연스럽게 움직인다. 과장된 효과 없이 실제 촬영한 것처럼."
)


class PhotoMissing(RuntimeError):
    def __init__(self, queries: list[str]):
        super().__init__(f"사진을 구하지 못한 컷: {', '.join(queries)}")
        self.queries = queries


def build_scene_clip(
    scene: Scene,
    collector: ImageCollector,
    veo_client,
    size: tuple[int, int],
    parallax_amount: float,
    dolly_amount: float,
) -> tuple[object, PhotoCredit]:
    found = collector.collect(scene.image_query_ko, scene.image_query_en)
    if found is None:
        raise PhotoMissing([scene.image_query_ko or scene.image_query_en])

    image_path, credit = found

    if scene.engine == "official_video" and scene.official_video_path:
        clip = _official_video_clip(Path(scene.official_video_path), scene, size)
        if clip is not None:
            return clip, credit
        logger.warning("공식 동영상을 쓰지 못해 이 컷은 Veo/파랄랙스로 대체합니다")

    if scene.engine in ("veo", "official_video") and veo_client is not None and veo_client.enabled:
        clip = _veo_clip(veo_client, image_path, scene, size)
        if clip is not None:
            return clip, credit
        logger.warning("Veo 클립을 만들지 못해 이 컷은 파랄랙스로 대체합니다")

    return (
        make_parallax_clip(
            image_path, scene.duration, scene.camera, size, parallax_amount, dolly_amount
        ),
        credit,
    )


def _official_video_clip(path: Path, scene: Scene, size: tuple[int, int]):
    if not path.exists():
        return None
    try:
        clip = VideoFileClip(str(path)).without_audio()
    except Exception as exc:
        logger.warning(f"공식 동영상 로드 실패({path}): {exc}")
        return None

    usable = min(clip.duration, scene.duration)
    clip = clip.subclipped(0, usable)

    # 공식 영상은 16:9 다큐 화면비일 가능성이 높다. 가운데를 기준으로 잘라 세로(9:16)에
    # 맞춘다 — 피사체가 잘릴 수 있어 실제 응답을 보며 크롭 임계값을 다듬어야 한다.
    width, height = clip.size
    target_ratio = size[0] / size[1]
    current_ratio = width / height
    if current_ratio > target_ratio:
        new_width = int(height * target_ratio)
        x1 = max((width - new_width) // 2, 0)
        clip = clip.cropped(x1=x1, width=new_width)
    elif current_ratio < target_ratio:
        new_height = int(width / target_ratio)
        y1 = max((height - new_height) // 2, 0)
        clip = clip.cropped(y1=y1, height=new_height)

    return clip.resized(size)


def _veo_clip(veo_client, image_path: Path, scene: Scene, size: tuple[int, int]):
    from ..motion.veo_client import VeoError

    prompt = VEO_PROMPT.format(subject=scene.image_query_ko or scene.image_query_en)
    try:
        clip_path = veo_client.animate(image_path, prompt)
    except VeoError as exc:
        logger.warning(f"Veo 생성 실패: {exc}")
        return None
    except Exception as exc:  # 상한 초과 등 예산 쪽 중단도 여기로 온다
        logger.warning(f"Veo 사용 불가: {exc}")
        return None

    clip = VideoFileClip(str(clip_path)).without_audio()
    # 생성 길이가 씬보다 짧으면 남는 구간은 마지막 프레임이 아니라 컷 길이에 맞춰 자른다.
    usable = min(clip.duration, scene.duration)
    return clip.subclipped(0, usable).resized(size)
