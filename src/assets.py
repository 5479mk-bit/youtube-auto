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
AUDIO_PAPER_RUSTLE = ASSETS_DIR / "audio" / "paper_rustle.wav"

# ffmpeg 합성 효과음(사인파+노이즈)은 "짜치다"는 지적을 두 번 받고 폐기했다 — 대신
# Mixkit(상업적 이용 포함 완전 무료, 출처 표기 불필요) 라이브러리에서 받은 실제
# 효과음 10개를 태그당 2개씩 묶었다(download_assets.sh 참고). video_builder 가
# 태그 하나가 나올 때마다 풀에서 무작위로 골라 써서, 같은 태그가 여러 번 나와도
# 매번 다른 소리가 난다.
SFX_POOLS: dict[str, list[Path]] = {
    "sub_boom": [
        ASSETS_DIR / "audio" / "sfx_boom_1.mp3",
        ASSETS_DIR / "audio" / "sfx_boom_2.mp3",
    ],
    "crack": [
        ASSETS_DIR / "audio" / "sfx_crack_1.mp3",
        ASSETS_DIR / "audio" / "sfx_crack_2.mp3",
    ],
    "whoosh": [
        ASSETS_DIR / "audio" / "sfx_whoosh_1.mp3",
        ASSETS_DIR / "audio" / "sfx_whoosh_2.mp3",
    ],
    "alarm": [
        ASSETS_DIR / "audio" / "sfx_alarm_1.mp3",
        ASSETS_DIR / "audio" / "sfx_alarm_2.mp3",
    ],
    "braam": [
        ASSETS_DIR / "audio" / "sfx_braam_1.mp3",
        ASSETS_DIR / "audio" / "sfx_braam_2.mp3",
    ],
}

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
