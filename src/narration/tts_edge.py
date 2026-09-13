"""edge-tts 한국어 내레이션 합성.

문장 단위로 음원을 만들고 실제 길이를 재서 ScriptLine 에 채워 넣는다. 이 길이가
자막 타임코드와 컷 길이의 기준이 된다. 네트워크가 막힌 환경에서도 파이프라인이
끝까지 돌 수 있도록, 합성이 실패하면 예상 길이만큼의 무음으로 대체한다.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from loguru import logger

from ..models import ScriptPart

# 한국어 내레이션 평균 속도(초당 글자 수). 무음 폴백의 길이 추정에 쓴다.
CHARS_PER_SECOND = 5.5
MIN_DURATION = 1.2


def synthesize_part(part: ScriptPart, voice: str, rate: str, out_dir: Path) -> ScriptPart:
    out_dir.mkdir(parents=True, exist_ok=True)

    for index, line in enumerate(part.lines):
        audio_path = out_dir / f"part{part.part}_line{index:02d}.mp3"
        ok = _synthesize_line(line.text, voice, rate, audio_path)
        if not ok:
            _write_silence(audio_path, _estimate_duration(line.text))

        line.audio_path = str(audio_path)
        line.duration = _measure_duration(audio_path) or _estimate_duration(line.text)

    return part


def _synthesize_line(text: str, voice: str, rate: str, out_path: Path) -> bool:
    try:
        import edge_tts

        # edge-tts 는 프록시 환경변수를 스스로 읽지 않는다. 사내망이나 프록시 뒤에서는
        # 넘겨주지 않으면 전부 무음으로 떨어진다.
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or None

        async def run() -> None:
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, proxy=proxy)
            await communicate.save(str(out_path))

        asyncio.run(run())
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception as exc:
        logger.warning(f"edge-tts 합성 실패, 무음으로 대체합니다: {exc}")
        return False


def _estimate_duration(text: str) -> float:
    return max(len(text) / CHARS_PER_SECOND, MIN_DURATION)


def _write_silence(out_path: Path, duration: float) -> None:
    import imageio_ffmpeg

    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=mono:sample_rate=24000",
            "-t",
            f"{duration:.3f}",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )


def _measure_duration(path: Path) -> float:
    try:
        from moviepy import AudioFileClip

        with AudioFileClip(str(path)) as clip:
            return float(clip.duration)
    except Exception as exc:
        logger.warning(f"오디오 길이 측정 실패: {exc}")
        return 0.0
