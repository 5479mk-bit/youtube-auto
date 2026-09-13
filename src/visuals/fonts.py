"""한국어 자막·차트용 폰트 확보.

폰트 파일은 10MB 가까이 되어 저장소에 넣지 않는다. 없으면 처음 실행할 때 한 번
내려받는다.
"""

from __future__ import annotations

from pathlib import Path

import requests
from loguru import logger

from ..settings import RESOURCE_DIR

FONT_PATH = RESOURCE_DIR / "fonts" / "NotoSansKR.ttf"
FONT_URL = (
    "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanskr/"
    "NotoSansKR%5Bwght%5D.ttf"
)


def ensure_korean_font() -> Path:
    if FONT_PATH.exists() and FONT_PATH.stat().st_size > 0:
        return FONT_PATH

    FONT_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.info("한국어 폰트를 내려받습니다 (최초 1회)")
    response = requests.get(FONT_URL, timeout=120)
    response.raise_for_status()
    FONT_PATH.write_bytes(response.content)
    return FONT_PATH
