"""로컬 Supertonic 서버 TTS.

`supertonic serve`(패키지 자체가 제공하는 CLI)로 띄운 로컬 HTTP 서버를 호출한다 —
모델을 파이썬 라이브러리로 직접 불러오지 않는다. 서버가 꺼져 있거나 타임아웃·오류가
나면 그냥 `False` 를 돌려줘 다음 엔진으로 자연스럽게 폴백시킨다(`tts_engine.py`의
부 단위 폴백 로직에 그대로 올라탄다 — 새 안전장치 불필요).

엔드포인트·페이로드는 실제 `supertonic-py` 패키지 문서(github.com/supertone-inc/
supertonic-py) 기준이다 — 예전 버전은 `/api/synthesize`(포트 5000)라는, 실제로는
존재하지 않는 엔드포인트를 가정하고 있었다(그래서 항상 연결 거부로 폴백됐다).
진짜 서버는:
    pip install 'supertonic[serve]'
    supertonic serve --host 127.0.0.1 --port 7788
로 띄우면 POST http://127.0.0.1:7788/v1/tts 가 열린다(.github/workflows/shorts.yml
의 "Supertonic 서버 준비" 스텝이 CI 에서 이 과정을 자동으로 한다).
"""

from __future__ import annotations

from pathlib import Path

import requests
from loguru import logger

DEFAULT_URL = "http://127.0.0.1:7788/v1/tts"
DEFAULT_TIMEOUT = 60


class SupertonicEngine:
    name = "supertonic"

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        voice: str = "M1",
        lang: str = "ko",
        timeout: int = DEFAULT_TIMEOUT,
        steps: int | None = None,
    ):
        self.base_url = base_url
        self.voice = voice
        self.lang = lang
        self.timeout = timeout
        # 합성 정밀도(diffusion steps). 기본값(서버 쪽 낮은 값)은 사람 목소리와
        # 거리가 있어 로보틱하게 들린다 — 64로 올리면 자연스러워지고, 문장 하나에
        # 약 14초 걸린다(10이면 2.4초). 목소리 청취 검증(M1, steps=64)으로 확정됐다.
        self.steps = steps
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        # 로컬 서버가 떠 있는지는 요청 시점에만 확실히 알 수 있다 — 여기서 미리
        # 핑을 날리지 않는다(그 자체로 실패 지점이 하나 늘 뿐이다).
        return True

    def synthesize(self, text: str, rate: str, out_path: Path) -> bool:
        payload = {
            "text": text,
            "voice": self.voice,
            "lang": self.lang,
            "speed": _rate_to_speed(rate),
            "response_format": "wav",
        }
        if self.steps is not None:
            payload["steps"] = self.steps
        try:
            response = self.session.post(
                self.base_url,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning(f"[supertonic] 로컬 서버 호출 실패({exc}) — 다음 엔진으로 폴백합니다")
            return False

        if not response.content:
            logger.warning("[supertonic] 응답이 비어 있습니다 — 다음 엔진으로 폴백합니다")
            return False

        # 서버는 wav 원본 바이트를 돌려준다. out_path 는 오케스트레이션이 정한
        # ".mp3" 확장자지만, 뒤이어 tts_engine._trim_and_pad() 가 ffmpeg 로 무음을
        # 자르며 다시 인코딩하므로(입력 코덱은 확장자가 아니라 내용으로 판별된다)
        # 여기서 별도 변환 없이 그대로 써도 안전하다.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)
        return out_path.stat().st_size > 0


def _rate_to_speed(rate: str) -> float:
    """edge-tts 식 "-10%"/"+2%" 문자열을 배율로 바꾼다. cloud_tts._rate_to_multiplier
    와 같은 패턴이다. Supertonic 문서가 밝힌 안전 범위(0.7~2.0)로 clamp 한다."""
    rate = (rate or "").strip()
    if not rate or rate in ("+0%", "-0%", "0%"):
        return 1.0
    try:
        percent = float(rate.rstrip("%"))
    except ValueError:
        return 1.0
    return max(0.7, min(2.0, 1.0 + percent / 100.0))
