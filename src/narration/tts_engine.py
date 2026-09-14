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
# 문장 앞뒤 무음을 깎을 기준. 말 시작 직전의 숨소리까지 잘리지 않게 조금 남긴다.
SILENCE_THRESHOLD = "-45dB"
KEEP_SILENCE = "0.05"


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

        _trim_and_pad(audio_path, line.pause_after)

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


def _trim_and_pad(audio_path: Path, pause: float) -> None:
    """문장 앞뒤의 무음을 걷어낸 뒤 설계한 만큼만 쉼을 붙인다.

    TTS 엔진은 문장마다 앞뒤로 제멋대로 무음을 붙여 보낸다. 그 위에 쉼을 더하면
    문장 사이가 1초 가까이 벌어져 쇼츠에서는 늘어지게 들린다. 한 번 깎고 다시 붙여야
    설계한 호흡이 그대로 나온다.
    """
    import imageio_ffmpeg

    trim = (
        f"silenceremove=start_periods=1:start_threshold={SILENCE_THRESHOLD}"
        f":start_silence={KEEP_SILENCE}:detection=peak"
    )
    filters = f"{trim},areverse,{trim},areverse"
    if pause > 0:
        filters += f",apad=pad_dur={pause:.3f}"

    processed = audio_path.with_name(f"{audio_path.stem}_paced.mp3")
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-i",
            str(audio_path),
            "-af",
            filters,
            str(processed),
        ],
        check=True,
        capture_output=True,
    )
    processed.replace(audio_path)


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
