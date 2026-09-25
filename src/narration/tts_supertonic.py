"""로컬 Supertonic 서버 TTS.

사용자가 직접 띄워둔 로컬 Supertonic 서버(HTTP)를 호출한다 — 모델을 파이썬
라이브러리로 직접 불러오지 않는다. 서버가 꺼져 있거나 타임아웃·오류가 나면 그냥
`False` 를 돌려줘 다음 엔진(edge-tts)으로 자연스럽게 폴백시킨다(`tts_engine.py`의
부 단위 폴백 로직에 그대로 올라탄다 — 새 안전장치 불필요).
"""

from __future__ import annotations

from pathlib import Path

import requests
from loguru import logger

DEFAULT_URL = "http://127.0.0.1:5000/api/synthesize"
DEFAULT_TIMEOUT = 30


class SupertonicEngine:
    name = "supertonic"

    def __init__(self, base_url: str = DEFAULT_URL, voice: str = "default", timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url
        self.voice = voice
        self.timeout = timeout
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        # 로컬 서버가 떠 있는지는 요청 시점에만 확실히 알 수 있다 — 여기서 미리
        # 핑을 날리지 않는다(그 자체로 실패 지점이 하나 늘 뿐이다).
        return True

    def synthesize(self, text: str, rate: str, out_path: Path) -> bool:
        try:
            response = self.session.post(
                self.base_url,
                json={"text": text, "voice": self.voice, "speed": _rate_to_speed(rate)},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning(f"[supertonic] 로컬 서버 호출 실패({exc}) — 다음 엔진으로 폴백합니다")
            return False

        if not response.content:
            logger.warning("[supertonic] 응답이 비어 있습니다 — 다음 엔진으로 폴백합니다")
            return False

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)
        return out_path.stat().st_size > 0


def _rate_to_speed(rate: str) -> float:
    """edge-tts 식 "-10%"/"+2%" 문자열을 배율로 바꾼다. cloud_tts._rate_to_multiplier
    와 같은 패턴이다."""
    rate = (rate or "").strip()
    if not rate or rate in ("+0%", "-0%", "0%"):
        return 1.0
    try:
        percent = float(rate.rstrip("%"))
    except ValueError:
        return 1.0
    return max(0.5, min(2.0, 1.0 + percent / 100.0))
