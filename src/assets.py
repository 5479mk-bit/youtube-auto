"""`assets/` 안의 고정 에셋 경로.

전부 `download_assets.sh` 가 만든다. 코드 어디서도 경로 문자열을 직접 쓰지 않게 여기로
모았다 — 에셋이 없으면 렌더가 조용히 이상해지는 게 아니라 여기서 바로 알 수 있다.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

FONT_MYEONGJO = ASSETS_DIR / "fonts" / "NanumMyeongjo-Bold.ttf"  # 한글 전용 명조
# 사료 컷에 실제로 쓰는 폰트. 나눔명조·프리텐다드에는 한자가 하나도 없어서, 한자를 담은
# 명조가 따로 필요하다.
FONT_SERIF_HANJA = ASSETS_DIR / "fonts" / "NotoSerifKR.ttf"
# 그걸로도 안 되는 벽자용 보조(한글 없음, 한자 3만 자). 글자별로 골라 쓴다.
FONT_SERIF_HANJA_EXTRA = ASSETS_DIR / "fonts" / "NotoSerifSC.ttf"
FONT_SANS = ASSETS_DIR / "fonts" / "Pretendard-Bold.otf"  # 자막체
TEXTURE_HANJI = ASSETS_DIR / "textures" / "hanji.jpg"
MAP_DAEDONG = ASSETS_DIR / "maps" / "daedong_map.png"
OVERLAY_FOG = ASSETS_DIR / "overlays" / "fog.mp4"
AUDIO_DRONE = ASSETS_DIR / "audio" / "dark_ambient_drone.mp3"
# "배경음이 초저음 드론뿐이라 웅장함이 없다"는 지적에 맞춰 추가한 화음 패드(합성,
# 특정 곡 아님) — video_builder._load_bgm 이 드론보다 우선해서 쓴다.
AUDIO_EPIC_PAD = ASSETS_DIR / "audio" / "epic_pad.mp3"
AUDIO_SUB_BOOM = ASSETS_DIR / "audio" / "sub_boom.wav"
AUDIO_PAPER_RUSTLE = ASSETS_DIR / "audio" / "paper_rustle.wav"
AUDIO_BRAAM = ASSETS_DIR / "audio" / "braam.wav"
# "효과음이 sub_boom/braam 뿐이라 밋밋하다"는 지적에 맞춰 추가한 갈래(전부 ffmpeg
# 합성이라 저작권 걱정 없음) — 구조물 균열/물·잔해 쏟아짐/경보.
AUDIO_CRACK = ASSETS_DIR / "audio" / "crack.wav"
AUDIO_WHOOSH = ASSETS_DIR / "audio" / "whoosh.wav"
AUDIO_ALARM = ASSETS_DIR / "audio" / "alarm.wav"

_REQUIRED = (FONT_MYEONGJO, FONT_SERIF_HANJA, FONT_SANS, TEXTURE_HANJI, MAP_DAEDONG, OVERLAY_FOG, AUDIO_DRONE)


def missing() -> list[Path]:
    return [path for path in _REQUIRED if not path.exists()]


def warn_if_missing() -> bool:
    """없는 에셋을 한 번에 알린다. 렌더 시작 전에 부른다."""
    gaps = missing()
    if not gaps:
        return True
    names = ", ".join(path.name for path in gaps)
    logger.warning(f"에셋이 없습니다({names}). ./download_assets.sh 를 먼저 실행하세요")
    return False
