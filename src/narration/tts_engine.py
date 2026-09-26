"""내레이션 합성 오케스트레이션.

엔진은 갈아끼울 수 있지만 파이프라인이 기대하는 건 하나다. 문장마다 음원 파일과
실제 길이가 채워지는 것. 자막 타임코드와 컷 길이가 전부 이 길이에서 나온다.

품질 좋은 엔진(voxcpm/supertonic/gemini/cloud_tts)이 전부 실패했을 때 어떻게
할지는 `require_quality` 로 정한다. `False`(레거시 동작)면 무음으로 채워서라도
파이프라인을 계속 돌린다. `True`(기본값)면 "무음/기계음으로라도 계속"이 아니라
`TTSFailure` 를 던져 그 회차를 통째로 중단시킨다 — 저품질 목소리로 끝까지
렌더해놓고 나서야 사람이 듣고 실패를 아는 것보다, 렌더 초반에 명확히 실패하는
쪽이 시간·비용을 덜 버린다.
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
_VEO_PAD_SAFETY_MARGIN = 0.2


class TTSEngine(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    def synthesize(self, text: str, rate: str, out_path: Path) -> bool: ...

    # 배치를 지원하면 부 전체를 한 번에 읽는다 (호출 한도가 빡빡한 엔진용).
    supports_batch: bool

    def synthesize_batch(
        self, texts: list[str], out_dir: Path, prefix: str
    ) -> list[Path] | None: ...


class TTSFailure(RuntimeError):
    """품질 엔진이 전부 실패했고, 무음/저품질로라도 계속하지 않기로 한 경우."""


def synthesize_part(
    part: ScriptPart,
    engines: list[TTSEngine],
    out_dir: Path,
    require_quality: bool = True,
    min_veo_line_duration: float = 0.0,
) -> ScriptPart:
    """부 하나를 한 엔진으로 끝까지 읽힌다.

    엔진을 문장마다 갈아타면 한 편 안에서 목소리가 바뀐다. 그래서 폴백은 문장이 아니라
    **부 단위**로 한다. 앞 엔진이 한 문장이라도 실패하면 그 부 전체를 다음 엔진으로 다시
    읽힌다.

    `min_veo_line_duration` 은 scene_type="VEO" 인 문장의 최종 길이(무음 패딩 포함) 하한이다
    — VEO 컷의 화면 길이(`scene_planner.plan_typed_scenes`)가 이 문장의 길이를 그대로
    물려받으므로, 문장이 짧으면(대본 규칙상 15~20자, 약 3~4초) Veo 가 실제로 생성한
    8초짜리 영상도 그만큼만 잘려 쓰인다 — "화면이 대부분 정지 이미지처럼 보인다"는
    문제의 원인이었다. 여기서 무음을 더해 오디오 자체의 길이를 늘리면 자막·씬 길이가
    같은 값에서 자동으로 따라와 싱크가 어긋나지 않는다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    apply_pacing(part)

    for engine in engines:
        if not engine.enabled:
            continue
        paths = _synthesize_all(engine, part, out_dir)
        if paths is not None:
            _finish(part, paths, min_veo_line_duration)
            logger.info(f"{part.part}부 내레이션: {engine.name}")
            return part
        logger.warning(f"{engine.name} 로는 {part.part}부를 끝까지 읽지 못했습니다 — 다음 엔진")

    if require_quality:
        raise TTSFailure(
            f"{part.part}부를 읽을 고품질 TTS 엔진이 전부 실패했습니다 "
            "(audio.require_quality_voice=true) — 무음이나 기계음으로 채우는 대신 "
            "여기서 멈춥니다. Vertex/AI Studio 키·한도, 로컬 서버(voxcpm/supertonic) "
            "상태를 확인하세요."
        )

    logger.warning(f"{part.part}부를 읽을 엔진이 없어 무음으로 채웁니다")
    paths = []
    for index, line in enumerate(part.lines):
        path = out_dir / f"part{part.part}_line{index:02d}.mp3"
        _write_silence(path, _estimate_duration(line.narration))
        paths.append(path)
    _finish(part, paths, min_veo_line_duration)
    return part


def _synthesize_all(engine: TTSEngine, part: ScriptPart, out_dir: Path) -> list[Path] | None:
    """한 엔진으로 부 전체를 만든다. 하나라도 실패하면 None."""
    texts = [line.narration for line in part.lines]

    # 배치를 지원하는 엔진은 한 번만 부른다. Gemini 무료 한도가 하루 10회라 문장마다
    # 부르면 한 편도 못 만든다.
    if getattr(engine, "supports_batch", False):
        return engine.synthesize_batch(texts, out_dir, f"part{part.part}")

    paths = []
    for index, line in enumerate(part.lines):
        path = out_dir / f"part{part.part}_line{index:02d}.mp3"
        if not engine.synthesize(line.narration, line.speech_rate, path):
            return None
        paths.append(path)
    return paths


def _finish(part: ScriptPart, paths: list[Path], min_veo_line_duration: float = 0.0) -> None:
    """문장마다 앞뒤 무음을 정리하고 설계한 쉼을 붙인 뒤 길이를 잰다.

    VEO 문장은 화면(생성 영상) 길이가 이 최종 길이를 그대로 물려받으므로, 목표 하한
    (`min_veo_line_duration`)에 못 미치면 부족한 만큼 무음을 더 붙인다 — 말은 그대로,
    화면이 꺼지지 않고 더 오래 보이는 셈이다.
    """
    for line, path in zip(part.lines, paths):
        pause = line.pause_after
        if line.scene_type == "VEO" and min_veo_line_duration > 0:
            spoken = _measure_duration(path) or _estimate_duration(line.narration)
            shortfall = min_veo_line_duration - (spoken + pause)
            if shortfall > 0:
                # 무음 트림(_trim_and_pad)이 문장 경계에서 몇 ms 를 더 깎아낼 수 있어
                # 여유를 조금 더 얹는다 — 하한을 살짝 밑도는 것보다 조금 넘치는 쪽이 낫다.
                pause += shortfall + _VEO_PAD_SAFETY_MARGIN
        new_path = _trim_and_pad(path, pause)
        line.audio_path = str(new_path)
        line.duration = _measure_duration(new_path) or _estimate_duration(line.narration)


def _estimate_duration(text: str) -> float:
    return max(len(text) / CHARS_PER_SECOND, MIN_DURATION)


def _trim_and_pad(audio_path: Path, pause: float) -> Path:
    """문장 앞뒤의 무음을 걷어낸 뒤 설계한 만큼만 쉼을 붙인다.

    TTS 엔진은 문장마다 앞뒤로 제멋대로 무음을 붙여 보낸다. 그 위에 쉼을 더하면
    문장 사이가 1초 가까이 벌어져 쇼츠에서는 늘어지게 들린다. 한 번 깎고 다시 붙여야
    설계한 호흡이 그대로 나온다.

    출력은 원본 확장자와 무관하게 항상 무손실 WAV(pcm_s16le)다 — 예전엔 이 단계가
    다시 mp3 로 인코딩했는데, TTS 엔진이 이미 mp3(손실)로 준 음원을 여기서 한 번 더
    손실 인코딩하면 파일마다 수십 ms 씩 길이 오차가 쌓여 부 후반부로 갈수록 자막이
    밀리는 원인이 됐다. 이후 길이 측정(`_measure_duration`)도 이 WAV 를 읽는다.
    """
    import imageio_ffmpeg

    trim = (
        f"silenceremove=start_periods=1:start_threshold={SILENCE_THRESHOLD}"
        f":start_silence={KEEP_SILENCE}:detection=peak"
    )
    filters = f"{trim},areverse,{trim},areverse"
    if pause > 0:
        filters += f",apad=pad_dur={pause:.3f}"

    processed = audio_path.with_name(f"{audio_path.stem}_paced.wav")
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-i",
            str(audio_path),
            "-af",
            filters,
            "-c:a",
            "pcm_s16le",
            str(processed),
        ],
        check=True,
        capture_output=True,
    )
    final_path = audio_path.with_suffix(".wav")
    processed.replace(final_path)
    if audio_path != final_path and audio_path.exists():
        audio_path.unlink()
    return final_path


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
