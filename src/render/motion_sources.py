"""컷 하나를 움직이는 그림으로 바꾼다.

엔진이 둘이지만 파이프라인은 "씬을 주면 클립이 나온다"만 알면 된다.
- veo: 사진을 넣어 실제로 움직이는 영상을 생성한다 (유료)
- parallax: 깊이를 추정해 카메라가 장면 안으로 들어간다 (무료)

사진을 못 구하면 단색으로 때우지 않고 그대로 실패를 알린다.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from moviepy import ImageClip, VideoFileClip, concatenate_videoclips

from ..models import PhotoCredit, Scene
from ..motion.parallax import make_parallax_clip
from ..visuals.image_collector import ImageCollector
from . import scene_renderers

# 화면 아래 출처 배지에 쓸 이름. 생성 컷은 "우리가 만든 그림"이라는 걸 밝혀야 한다.
# ai_image/veo/fallback 은 disaster(현재 유일하게 쓰는 소재) 렌더 화면에 그대로
# 찍히는 문구라 스페인어로 바꿨다(2026-09-30, 채널 언어 전환). historical_doc/
# map_motion 은 sillok 전용이라 지금은 화면에 안 나온다 — 나중에 되돌릴 수 있게
# 한국어 그대로 둔다.
_CREDIT_NAMES = {
    "historical_doc": "조선왕조실록 원문",
    "map_motion": "대동여지도 (퍼블릭 도메인)",
    "ai_image": "Imagen generada por IA",
    "veo": "Video generado por IA",
    "fallback": "Fondo ilustrativo",
}

VEO_PROMPT = (
    "다큐멘터리 영상. {subject}. 카메라가 아주 천천히 움직이고, 장면 속 사람과 차량이"
    " 자연스럽게 움직인다. 과장된 효과 없이 실제 촬영한 것처럼."
)


# 한 회차 안에서의 상태(2026-10-25). 이미지 생성이 429 로 막히면 회색 대체 화면 대신
# 앞서 성공한 그림을 다시 쓰고, 대체 화면이 몇 컷 들어갔는지 세어 자동 공개를 막는 데 쓴다.
_RECENT_IMAGES: list[Path] = []
_REUSE_COUNT = 0
FALLBACK_SCENES = 0


def reset_run_state() -> None:
    global FALLBACK_SCENES, _REUSE_COUNT
    _RECENT_IMAGES.clear()
    FALLBACK_SCENES = 0
    _REUSE_COUNT = 0


def _pad_to_duration(clip, target_duration: float):
    """생성 영상이 목표 길이보다 짧으면 마지막 프레임을 정지시켜 채운다.

    씬 길이(scene.duration)는 이제 실제 생성 길이(veo.duration_seconds)를 다 쓰도록
    맞춰지는데, Veo 가 요청한 초 수를 프레임 단위로 반올림해 실제로는 몇십 ms 짧게
    돌려줄 수 있다. `with_duration()`으로 길이 메타데이터만 늘리면 존재하지 않는
    프레임을 읽으려다 리더가 깨질 위험이 있어, 마지막 프레임을 진짜 정지 이미지로
    이어붙여 항상 실제 프레임이 있게 한다.
    """
    shortfall = target_duration - clip.duration
    if shortfall <= 0.02:
        return clip
    last_frame = clip.get_frame(max(clip.duration - 0.04, 0))
    freeze = ImageClip(last_frame).with_duration(shortfall)
    return concatenate_videoclips([clip, freeze])


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
    ai_client=None,
    settings=None,
) -> tuple[object, PhotoCredit]:
    """컷 하나를 클립으로. 씬 종류에 따라 만드는 방식이 완전히 갈린다.

    실록 경로(사료·지도·생성)는 웹 사진을 아예 건드리지 않는다 — 조선 사건을 텍스트로
    검색하면 현대 우체국·카페 사진이 끌려 들어오기 때문이다. 문화재 경로(파랄랙스/공식
    영상)는 지금까지처럼 사진을 쓴다.
    """
    if scene.engine in ("historical_doc", "map_motion", "ai_image", "veo"):
        return _build_generated_clip(scene, veo_client, ai_client, size, settings)

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


def _build_generated_clip(scene: Scene, veo_client, ai_client, size: tuple[int, int], settings=None):
    """사진을 검색하지 않는 컷들.

    생성(Veo/이미지)이 막히면 대체 화면(먹빛 플레이트)으로 내려간다. 무료 티어는 영상·
    이미지 할당량이 0 이라 지금은 이 경로를 자주 탄다 — 그래도 톤은 유지된다.
    """
    credit = PhotoCredit(query=scene.scene_type, source_name=_CREDIT_NAMES[scene.engine])

    if scene.engine == "historical_doc":
        return (
            scene_renderers.render_doc_scene(
                scene.duration, size, scene.doc_title, scene.doc_highlight
            ),
            credit,
        )

    if scene.engine == "map_motion":
        return (
            scene_renderers.render_map_scene(
                scene.duration, size, scene.map_region, scene.map_spots
            ),
            credit,
        )

    global FALLBACK_SCENES
    t2v = (
        veo_client is not None
        and getattr(veo_client, "text_to_video", False) is True
        and veo_client.enabled
    )

    # text-to-video 면 Veo 에 시작 이미지가 필요 없다 — 이미지를 먼저 만들고 그게 성공해야
    # Veo 를 부르던 구조 때문에, 이미지 API 가 429 로 막히자 Veo 는 시도도 못 하고 15컷 중
    # 12컷이 회색 대체 화면이 됐다(2026-10-25, run #80). 이미지는 Veo 가 실패할 때만 만든다.
    if scene.engine == "veo" and t2v:
        clip = _veo_clip(veo_client, None, scene, size)
        if clip is not None:
            return clip, PhotoCredit(query=scene.scene_type, source_name=_CREDIT_NAMES["veo"])
        logger.warning("Veo(text-to-video)가 실패해 생성 이미지로 대체합니다")

    # 여기부터는 생성 이미지 컷. 먼저 그림 한 장을 만든다.
    image_path = None
    if ai_client is not None and scene.prompt:
        image_path = ai_client.generate(scene.prompt)

    if image_path is None and scene.engine != "veo" and t2v:
        # 이미지만 막혔으면 정지컷도 Veo 로 만든다(예산 상한 안에서).
        clip = _veo_clip(veo_client, None, scene, size)
        if clip is not None:
            return clip, PhotoCredit(query=scene.scene_type, source_name=_CREDIT_NAMES["veo"])

    if image_path is not None and not t2v and scene.engine == "veo" and veo_client is not None and veo_client.enabled:
        clip = _veo_clip(veo_client, image_path, scene, size)
        if clip is not None:
            return clip, PhotoCredit(query=scene.scene_type, source_name=_CREDIT_NAMES["veo"])
        logger.warning("Veo 클립을 만들지 못해 이 컷은 생성 이미지로 대체합니다")

    if image_path is not None:
        _RECENT_IMAGES.append(image_path)
    elif _RECENT_IMAGES:
        # 생성이 막혔으면 회색 화면 대신 앞에서 성공한 그림을 다른 카메라 무브로 다시 쓴다.
        global _REUSE_COUNT
        _REUSE_COUNT += 1
        image_path = _RECENT_IMAGES[-(_REUSE_COUNT % len(_RECENT_IMAGES)) - 1]
        logger.warning(f"생성이 막혀 앞 장면 그림을 재사용합니다 ({scene.scene_type})")

    if image_path is not None:
        # 정지화를 평평한 줌으로 흉내만 내지 않는다 — 사진 컷에 이미 쓰고 있는
        # 깊이 기반 파랄랙스(make_parallax_clip)를 생성 이미지에도 그대로 태운다.
        # 픽셀마다 깊이가 다르게 움직이므로 원근이 실제로 어긋나며 지나가고, 이게
        # "화면이 정지된 사진처럼 보인다"는 지적의 실제 해법이다 — 색 보정으로는
        # 못 고치는 문제였다. 재난 소재는 사진 콘셉트보다 더 세게 민다.
        s = settings.get if settings is not None else (lambda key, default: default)
        clip = make_parallax_clip(
            image_path,
            scene.duration,
            scene.camera,
            size,
            parallax=s("visuals.ai_image_motion.parallax_amount", 0.10),
            dolly=s("visuals.ai_image_motion.dolly_amount", 0.30),
        )
        return clip, credit

    FALLBACK_SCENES += 1
    logger.warning(f"생성이 막혀 대체 화면을 씁니다 ({scene.scene_type})")
    seed = scene.prompt or scene.image_query_ko or scene.scene_type
    return (
        scene_renderers.render_fallback_plate(scene.duration, size, seed, scene.camera),
        PhotoCredit(query=scene.scene_type, source_name=_CREDIT_NAMES["fallback"]),
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

    return _pad_to_duration(clip.resized(size), scene.duration)


VEO_MOTION_BOOST = (
    ". The scene itself moves violently and massively from the very first frame — this is "
    "the main point, not the camera: the structure visibly cracks, buckles and collapses, "
    "huge chunks break off and fall, water floods in and sweeps things away, fire and smoke "
    "erupt and spread, the ground splits and heaves, debris flies everywhere. Big, continuous, "
    "large-scale physical motion across the whole frame, nothing stays still. A fast camera "
    "move (FPV dive, whip pan or crash zoom) on top of that"
)


def _veo_clip(veo_client, image_path: Path, scene: Scene, size: tuple[int, int]):
    from ..motion.veo_client import VeoError

    # 실록 컷은 대본이 만든 영문 시네마틱 프롬프트를 그대로 쓴다. 문화재 컷은 검색어를
    # 넣어 만든 한국어 프롬프트를 쓴다 — 실사 사진을 자연스럽게 움직이는 게 목적이라 톤이
    # 다르다.
    prompt = scene.prompt or VEO_PROMPT.format(
        subject=scene.image_query_ko or scene.image_query_en
    )
    if scene.prompt:
        # 2026-10-19 사용자 피드백: "움직임이 너무 제한적이라 움직이는 것 같지 않다 —
        # 처음부터 시원하게". 디오라마 정지 이미지에서 출발하는 image-to-video 라 Veo 가
        # 장면을 거의 그대로 두는 경향이 있어, 대본이 뭘 쓰든 강한 움직임 지시를 덧붙인다.
        prompt = f"{prompt}{VEO_MOTION_BOOST}"
    try:
        clip_path = veo_client.animate(image_path, prompt)
    except VeoError as exc:
        logger.warning(f"Veo 생성 실패: {exc}")
        return None
    except Exception as exc:  # 상한 초과 등 예산 쪽 중단도 여기로 온다
        logger.warning(f"Veo 사용 불가: {exc}")
        return None

    clip = VideoFileClip(str(clip_path)).without_audio()
    # Veo 가 요청한 초 수와 다르게 돌려줄 수 있다(모델이 지원하는 길이로 반올림 등 —
    # 실제로 "8초짜리든 5초짜리든 뒷부분이 잘려나간다"는 문제가 있었다). 돈 주고 만든
    # 영상을 자르는 대신, 씬보다 길면 속도를 올려 끝까지 다 보여준다(전체 프레임 유지,
    # 재생 속도만 빨라짐) — 반올림 오차 수준(0.3초 이내)만 자르고 패딩한다.
    if clip.duration > scene.duration + 0.3:
        clip = clip.with_speed_scaled(final_duration=scene.duration)
    else:
        usable = min(clip.duration, scene.duration)
        clip = clip.subclipped(0, usable)
    clip = clip.resized(size)
    return _pad_to_duration(clip, scene.duration)
