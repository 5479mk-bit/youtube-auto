"""edge-tts 한국어 내레이션 합성.

키가 필요 없어서 Gemini TTS 가 한도에 걸리거나 실패했을 때 내려오는 자리다.
문장별 속도는 rate 로만 줄 수 있어 톤 제어 폭은 좁다.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from loguru import logger

VOICES = {
    # 신형 모델이라 셋 중에서는 가장 자연스럽다.
    "hyunsu": "ko-KR-HyunsuMultilingualNeural",
    "injoon": "ko-KR-InJoonNeural",
    "sunhi": "ko-KR-SunHiNeural",
}


class EdgeTTS:
    name = "edge"

    def __init__(self, voice: str, pitch: str = "+0Hz"):
        self.voice = VOICES.get(voice, voice)
        self.pitch = pitch

    @property
    def enabled(self) -> bool:
        return True

    def synthesize(self, text: str, rate: str, out_path: Path) -> bool:
        try:
            import edge_tts

            # edge-tts 는 프록시 환경변수를 스스로 읽지 않는다. 프록시 뒤에서는
            # 넘겨주지 않으면 전부 실패한다.
            proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or None

            async def run() -> None:
                communicate = edge_tts.Communicate(
                    text=text, voice=self.voice, rate=rate, pitch=self.pitch, proxy=proxy
                )
                await communicate.save(str(out_path))

            asyncio.run(run())
            return out_path.exists() and out_path.stat().st_size > 0
        except Exception as exc:
            logger.warning(f"edge-tts 합성 실패: {exc}")
            return False
