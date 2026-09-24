"""Google Cloud Text-to-Speech (SSML) 내레이션 합성.

Gemini TTS 와 달리 진짜 SSML 을 받는 전통적인 합성 API 다. 말줄임표(`...`)를
`<break>` 로, 톤을 `<prosody pitch=...>` 로 직접 지시할 수 있다.

Gemini TTS 무료 티어의 하루 10회 한도 때문에 부 전체를 한 번에 읽히고 무음 감지로
문장 경계를 다시 잘라야 했는데(`tts_gemini.py`), Cloud TTS 는 그런 한도가 없어
**문장마다 개별 호출**한다 — 경계를 다시 찾을 필요 자체가 없어 싱크가 어긋나지 않는다.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from loguru import logger

SYNTHESIZE_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
LANGUAGE_CODE = "ko-KR"
BREAK_MS = 500
TTS_COST_KRW = 5  # 문장당 대략적인 추정. 실제 청구액은 GCP 청구서가 정답이다.

_ELLIPSIS = re.compile(r"\.{3,}|…")


class GoogleCloudTTS:
    name = "cloud_tts"

    def __init__(
        self,
        auth,
        voice: str = "ko-KR-Neural2-C",
        pitch_semitones: float = -1.5,
        spend_guard=None,
        cost_krw: int = TTS_COST_KRW,
    ):
        self.auth = auth
        self.voice = voice
        self.pitch_semitones = pitch_semitones
        self.session = requests.Session()
        self.spend_guard = spend_guard
        self.cost_krw = int(cost_krw)

    @property
    def enabled(self) -> bool:
        return self.auth is not None and self.auth.available

    def synthesize(self, text: str, rate: str, out_path: Path) -> bool:
        if self.spend_guard is not None:
            try:
                self.spend_guard.ensure_can_spend(self.cost_krw, "내레이션 한 문장")
            except Exception as exc:
                logger.warning(f"[cloud_tts] {exc}")
                return False

        payload = {
            "input": {"ssml": _build_ssml(text, self.pitch_semitones)},
            "voice": {"languageCode": LANGUAGE_CODE, "name": self.voice},
            "audioConfig": {
                "audioEncoding": "MP3",
                "speakingRate": _rate_to_multiplier(rate),
            },
        }
        try:
            response = self.session.post(
                SYNTHESIZE_URL, headers=self.auth.headers(), json=payload, timeout=60
            )
        except Exception as exc:
            logger.warning(f"[cloud_tts] 호출 실패: {exc}")
            return False

        if not response.ok:
            logger.warning(f"[cloud_tts] 오류 {response.status_code}: {response.text[:200]}")
            return False

        try:
            audio_b64 = response.json()["audioContent"]
        except (KeyError, ValueError) as exc:
            logger.warning(f"[cloud_tts] 응답을 읽지 못했습니다: {exc}")
            return False

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(audio_b64))

        if self.spend_guard is not None:
            self.spend_guard.record(self.cost_krw, "tts", self.voice)
        return True


def _build_ssml(text: str, pitch_semitones: float) -> str:
    """말줄임표를 정적(break)으로, 전체 톤을 지정한 피치로 감싼다."""
    escaped = escape(text)
    with_breaks = _ELLIPSIS.sub(f'<break time="{BREAK_MS}ms"/>', escaped)
    return f'<speak><prosody pitch="{pitch_semitones}st">{with_breaks}</prosody></speak>'


def _rate_to_multiplier(rate: str) -> float:
    """edge-tts 식 "-10%"/"+2%" 문자열을 Cloud TTS 의 speakingRate 배율로 바꾼다."""
    rate = (rate or "").strip()
    if not rate or rate in ("+0%", "-0%", "0%"):
        return 1.0
    try:
        percent = float(rate.rstrip("%"))
    except ValueError:
        return 1.0
    multiplier = 1.0 + percent / 100.0
    return max(0.25, min(4.0, multiplier))
