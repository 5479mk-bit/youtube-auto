"""Gemini TTS 내레이션 합성.

edge-tts 와 결정적으로 다른 점은 **스타일을 말로 지시할 수 있다**는 것이다.
"차분한 다큐멘터리 내레이터. 숫자에서 한 박자 쉬고" 같은 프롬프트가 실제로 먹힌다.

무료 티어가 있지만 preview 모델이라 분당 요청 수가 빡빡하다. 요청 사이에 간격을 두고,
한도에 걸리면 조용히 실패를 알려 호출자가 edge-tts 로 내려갈 수 있게 한다.
"""

from __future__ import annotations

import base64
import subprocess
import time
import wave
from pathlib import Path

import requests
from loguru import logger

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
# 응답은 24kHz 16bit 모노 PCM 으로 온다.
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1
MAX_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 20


class GeminiTTS:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        voice: str = "Charon",
        model: str = DEFAULT_MODEL,
        style_prompt: str = "",
        min_request_interval: float = 1.0,
    ):
        self.api_key = api_key
        self.voice = voice
        self.model = model
        self.style_prompt = style_prompt
        self.min_request_interval = min_request_interval
        self.session = requests.Session()
        self._last_request_at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def synthesize(self, text: str, rate: str, out_path: Path) -> bool:
        if not self.enabled:
            return False

        prompt = self._build_prompt(text, rate)
        for attempt in range(MAX_ATTEMPTS):
            self._respect_rate_limit()
            audio = self._request(prompt)
            if audio is not None:
                _pcm_to_mp3(audio, out_path)
                return True
            if attempt < MAX_ATTEMPTS - 1:
                logger.warning(f"Gemini TTS 재시도 ({RETRY_BACKOFF_SECONDS}초 후)")
                time.sleep(RETRY_BACKOFF_SECONDS)
        return False

    def _build_prompt(self, text: str, rate: str) -> str:
        """스타일 지시와 읽을 문장을 함께 넘긴다. 속도는 말로 표현한다."""
        pace = "조금 느리게" if rate.startswith("-") else "보통 속도로"
        style = self.style_prompt or "차분한 다큐멘터리 내레이터처럼"
        return f"{style}, {pace} 읽어줘:\n{text}"

    def _respect_rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self._last_request_at = time.time()

    def _request(self, prompt: str) -> bytes | None:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}}
                },
            },
        }
        try:
            response = self.session.post(
                f"{BASE_URL}/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key},
                json=payload,
                timeout=120,
            )
            if response.status_code == 429:
                logger.warning("Gemini TTS 무료 한도에 걸렸습니다")
                return None
            response.raise_for_status()
            data = (
                response.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
            )
            return base64.b64decode(data)
        except Exception as exc:
            logger.warning(f"Gemini TTS 호출 실패: {exc}")
            return None


def _pcm_to_mp3(pcm: bytes, out_path: Path) -> None:
    """헤더 없는 PCM 이라 wav 로 감싼 뒤 mp3 로 옮긴다."""
    import imageio_ffmpeg

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path = out_path.with_suffix(".wav")
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)

    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(wav_path), str(out_path)],
        check=True,
        capture_output=True,
    )
    wav_path.unlink(missing_ok=True)
