"""내레이션 합성 오케스트레이션.

엔진은 갈아끼울 수 있지만 파이프라인이 기대하는 건 하나다. 문장마다 음원 파일과
실제 길이가 채워지는 것. 자막 타임코드와 컷 길이가 전부 이 길이에서 나온다.

Gemini 가 한도에 걸리거나 실패하면 edge-tts 로 내려가고, 그것마저 안 되면 예상 길이만큼
무음을 넣어 파이프라인이 멈추지 않게 한다. 다만 무음으로 떨어진 건 로그에 남긴다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from loguru import logger

from ..models import ScriptPart
from .pacing import apply_pacing

# 한국어 내레이션 평균 속도(초당 글자 수). 무음 폴백의 길이 추정에 쓴다.
CHARS_PER_SECOND = 5.5
MIN_DURATION = 1.2


class TTSEngine(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    def synthesize(self, text: str, rate: str, out_path: Path) -> bool: ...


def synthesize_part(part: ScriptPart, engines: list[TTSEngine], out_dir: Path) -> ScriptPart:
    """앞 엔진부터 차례로 시도한다. 문장 뒤에는 pacing 이 정한 쉼을 붙인다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    apply_pacing(part)

    silent_lines = 0
    for index, line in enumerate(part.lines):
        audio_path = out_dir / f"part{part.part}_line{index:02d}.mp3"
        engine_used = _synthesize_with_fallback(
            engines, line.narration, line.speech_rate, audio_path
        )

        if engine_used is None:
            silent_lines += 1
            _write_silence(audio_path, _estimate_duration(line.narration))

        if line.pause_after > 0:
            _append_silence(audio_path, line.pause_after)

        line.audio_path = str(audio_path)
        line.duration = _measure_duration(audio_path) or _estimate_duration(line.narration)

    if silent_lines:
        logger.warning(
            f"{part.part}부에서 {silent_lines}개 문장이 무음으로 채워졌습니다. "
            "내레이션 없이 영상만 나갑니다."
        )
    return part


def _synthesize_with_fallback(
    engines: list[TTSEngine], text: str, rate: str, out_path: Path
) -> str | None:
    for engine in engines:
        if not engine.enabled:
            continue
        if engine.synthesize(text, rate, out_path):
            return engine.name
        logger.warning(f"{engine.name} 실패 — 다음 엔진으로 내려갑니다")
    return None


def _estimate_duration(text: str) -> float:
    return max(len(text) / CHARS_PER_SECOND, MIN_DURATION)


def _append_silence(audio_path: Path, seconds: float) -> None:
    """문장 뒤에 숨 쉴 틈을 붙인다. 이 길이가 그대로 자막 타임코드에 반영된다."""
    import imageio_ffmpeg

    padded = audio_path.with_name(f"{audio_path.stem}_padded.mp3")
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-i",
            str(audio_path),
            "-af",
            f"apad=pad_dur={seconds:.3f}",
            str(padded),
        ],
        check=True,
        capture_output=True,
    )
    padded.replace(audio_path)


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
