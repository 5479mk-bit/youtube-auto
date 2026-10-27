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

import shutil
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
    tempo: float = 1.0,
) -> ScriptPart:
    """부 하나를 한 엔진으로 끝까지 읽힌다.

    `tempo` 는 음높이를 유지한 채 말 속도를 올린다(ffmpeg atempo). Gemini TTS 는 문장별
    배속 지시를 받지 않아 전체 속도를 여기서 맞춘다(2026-10-27, 1.15배 — 쇼츠 내레이터 속도).

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
            _finish(part, paths, min_veo_line_duration, tempo)
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


_LONG_LINE_CHARS = 35  # 이보다 길면 한 번에 안 읽히고 절 단위로 쪼개 합성한다


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
        if len(line.narration) > _LONG_LINE_CHARS:
            # VEO 문장은 8초를 채우려고 40~70자까지 길어졌는데, 로컬 TTS(diffusion
            # 기반)는 한 번에 그렇게 긴 문장을 읽히면 억양이 깨지고 부자연스러워진다
            # ("long1 너무 부자연스러워" 지적) — 짧은 절 단위로 나눠 각각 자연스럽게
            # 합성한 뒤 이어붙이면, 한 문장처럼 들리면서도 품질은 유지된다.
            ok = _synthesize_chunked(engine, line.narration, line.speech_rate, path)
        else:
            ok = engine.synthesize(line.narration, line.speech_rate, path)
        if not ok:
            return None
        paths.append(path)
    return paths


def _synthesize_chunked(engine: TTSEngine, text: str, rate: str, out_path: Path) -> bool:
    """긴 문장을 절 단위로 쪼개 각각 합성한 뒤 이어붙인다."""
    chunks = _split_for_tts(text, _LONG_LINE_CHARS)
    if len(chunks) <= 1:
        return engine.synthesize(text, rate, out_path)

    chunk_paths = []
    for index, chunk in enumerate(chunks):
        chunk_path = out_path.with_name(f"{out_path.stem}_chunk{index}{out_path.suffix}")
        if not engine.synthesize(chunk, rate, chunk_path):
            return False
        chunk_paths.append(chunk_path)

    _concat_audio(chunk_paths, out_path)
    for chunk_path in chunk_paths:
        chunk_path.unlink(missing_ok=True)
    return out_path.exists() and out_path.stat().st_size > 0


_MIN_CHUNK_CHARS = 8  # 이보다 짧은 절은 혼자 합성하면 억양이 어색해져 옆 절에 붙인다


def _split_for_tts(text: str, max_chars: int) -> list[str]:
    """쉼표 기준으로 절을 나누고, 그래도 긴 절은 어절 경계에서 더 쪼갠다.

    "야," 처럼 한두 글자짜리 절이 혼자 떨어지면 TTS 가 맥락 없이 그 짧은 말만
    합성하다 억양이 뜬금없이 늘어지거나 이상하게 들린다("애~" 처럼 들린다는
    지적) — 너무 짧은 절은 옆 절에 붙여서 항상 어느 정도 맥락 안에서 읽히게 한다.
    """
    clauses = [c.strip() for c in text.split(",") if c.strip()]
    if not clauses:
        return [text]

    merged: list[str] = []
    for clause in clauses:
        if merged and len(merged[-1]) < _MIN_CHUNK_CHARS:
            merged[-1] = f"{merged[-1]} {clause}".strip()
        else:
            merged.append(clause)
    # 마지막 절이 짧게 남았으면 그 앞 절에 붙인다.
    if len(merged) > 1 and len(merged[-1]) < _MIN_CHUNK_CHARS:
        last = merged.pop()
        merged[-1] = f"{merged[-1]} {last}".strip()

    chunks: list[str] = []
    for clause in merged:
        if len(clause) <= max_chars:
            chunks.append(clause)
            continue
        words = clause.split()
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > max_chars and current:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


_CHUNK_BREATH_SECONDS = 0.12  # 절 사이 자연스러운 숨쉬기 틈


def _concat_audio(paths: list[Path], out_path: Path) -> None:
    """절 오디오를 이어붙인다. 절 사이에 아주 짧은 숨 틈을 넣어 이어 말하듯
    자연스럽게 들리게 한다 — 무음 없이 그냥 붙이면 사람이 숨도 안 쉬고 말하는
    것처럼 로봇 같게 들린다."""
    import imageio_ffmpeg

    silence_path = out_path.with_name(f"{out_path.stem}_silence.wav")
    _write_breath_silence(silence_path, _CHUNK_BREATH_SECONDS)

    entries = []
    for index, path in enumerate(paths):
        entries.append(f"file '{path.resolve()}'")
        if index < len(paths) - 1:
            entries.append(f"file '{silence_path.resolve()}'")

    list_file = out_path.with_suffix(".txt")
    list_file.write_text("\n".join(entries), encoding="utf-8")
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    list_file.unlink(missing_ok=True)
    silence_path.unlink(missing_ok=True)


def _write_breath_silence(out_path: Path, duration: float) -> None:
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


def _finish(
    part: ScriptPart, paths: list[Path], min_veo_line_duration: float = 0.0, tempo: float = 1.0
) -> None:
    """문장마다 앞뒤 무음을 정리하고 설계한 쉼을 붙인 뒤 길이를 잰다.

    VEO 문장은 화면(생성 영상) 길이가 이 최종 길이를 그대로 물려받으므로, 목표 하한
    (`min_veo_line_duration`)에 못 미치면 부족한 만큼 무음을 더 붙인다 — 말은 그대로,
    화면이 꺼지지 않고 더 오래 보이는 셈이다.

    부족분은 트림·패딩이 끝난 **뒤** 실제 길이를 재서 계산한다 — 트림 전 원본 길이로
    미리 계산하면, `_trim_and_pad`의 무음 트림이 문장 경계에서 얼마나 깎일지 미리 알
    수 없어(엔진마다 앞뒤에 붙이는 무음 길이가 제각각이다) 목표보다 짧게 끝나는 경우가
    있었다.
    """
    for line, path in zip(part.lines, paths):
        # _trim_and_pad 는 원본 자리를 덮어쓴다 — 틱톡판을 다른 배속으로 다시 다듬을 수 있게
        # 배속 전 원본을 따로 남긴다.
        raw_copy = path.with_name(f"{path.stem}_raw{path.suffix}")
        try:
            shutil.copyfile(path, raw_copy)
            line.raw_audio_path = str(raw_copy)
        except OSError:
            line.raw_audio_path = ""
        new_path = _trim_and_pad(path, line.pause_after, tempo)
        duration = _measure_duration(new_path) or _estimate_duration(line.narration)

        if line.scene_type == "VEO" and min_veo_line_duration > 0:
            shortfall = min_veo_line_duration - duration
            if shortfall > 0:
                extra = shortfall + _VEO_PAD_SAFETY_MARGIN
                new_path = _pad_tail(new_path, extra)
                duration = _measure_duration(new_path) or (duration + extra)

        line.audio_path = str(new_path)
        line.duration = duration


def retimed_copy(
    part: ScriptPart,
    tempo: float,
    min_seconds: float = 0.0,
    min_tempo: float = 0.9,
    max_tail_pad: float = 6.0,
) -> ScriptPart:
    """이미 합성한 부를 다른 배속으로 다시 다듬은 사본(틱톡 확장판용, 2026-10-27).

    TTS 를 다시 부르지 않는다 — `raw_audio_path`(배속 전 원본)에서 ffmpeg 만 다시 돌린다.
    틱톡 크리에이터 보상은 1분 **초과** 영상만 대상이라, `min_seconds` 에 못 미치면 배속을
    `min_tempo` 까지 낮추고, 그래도 모자라면 마지막 문장 뒤를 최대 `max_tail_pad` 초 무음으로
    늘린다(그 이상 모자라면 억지로 늘리지 않고 경고만 — 긴 무음은 이탈만 부른다).
    """
    from dataclasses import replace

    def build(rate: float) -> ScriptPart:
        lines = []
        for line in part.lines:
            source = Path(line.raw_audio_path or line.audio_path)
            copy = replace(line, emphasis=list(line.emphasis))
            new_path = _trim_and_pad(source, line.pause_after, rate, suffix="_tiktok")
            copy.audio_path = str(new_path)
            copy.duration = _measure_duration(new_path) or line.duration
            lines.append(copy)
        return ScriptPart(part=part.part, hook_number=part.hook_number, lines=lines)

    result = build(tempo)
    total = sum(line.duration for line in result.lines)
    if min_seconds and total < min_seconds and tempo > min_tempo:
        slower = max(min_tempo, tempo * total / min_seconds)
        logger.info(f"[tiktok] {total:.1f}초 → {min_seconds:.0f}초를 넘기려고 배속 {slower:.2f}로 낮춥니다")
        result = build(slower)
        total = sum(line.duration for line in result.lines)
    shortfall = min_seconds - total
    if shortfall > 0 and result.lines:
        if shortfall <= max_tail_pad:
            last = result.lines[-1]
            last.audio_path = str(_pad_tail(Path(last.audio_path), shortfall + 0.1))
            last.duration = _measure_duration(Path(last.audio_path)) or last.duration + shortfall + 0.1
        else:
            logger.warning(
                f"[tiktok] 확장판이 {total:.1f}초로 {min_seconds:.0f}초에 크게 못 미칩니다 — "
                "대본의 틱톡 전용 문장이 부족합니다(수익화 대상에서 빠질 수 있음)"
            )
    return result


def _estimate_duration(text: str) -> float:
    return max(len(text) / CHARS_PER_SECOND, MIN_DURATION)


def _trim_and_pad(audio_path: Path, pause: float, tempo: float = 1.0, suffix: str = "") -> Path:
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
    if abs(tempo - 1.0) > 0.01:
        # atempo 는 0.5~2.0 범위에서 음높이를 유지한다. 쉼(apad)은 배속 뒤에 붙여 설계값 그대로.
        filters += f",atempo={min(max(tempo, 0.5), 2.0):.3f}"
    if pause > 0:
        filters += f",apad=pad_dur={pause:.3f}"

    processed = audio_path.with_name(f"{audio_path.stem}_paced{suffix}.wav")
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
    if suffix:
        # 사본용(틱톡판) — 원본·유튜브판 파일은 건드리지 않는다.
        return processed
    final_path = audio_path.with_suffix(".wav")
    processed.replace(final_path)
    if audio_path != final_path and audio_path.exists():
        audio_path.unlink()
    return final_path


def _pad_tail(audio_path: Path, extra_seconds: float) -> Path:
    """이미 트림·패딩이 끝난 음원 뒤에 무음을 더 붙인다(다시 트림하지 않는다).

    VEO 문장의 부족분을 채울 때 쓴다 — `_trim_and_pad`를 다시 부르면 방금 붙인
    무음까지 silenceremove 가 다시 걷어내 버린다.
    """
    import imageio_ffmpeg

    processed = audio_path.with_name(f"{audio_path.stem}_extra.wav")
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-i",
            str(audio_path),
            "-af",
            f"apad=pad_dur={extra_seconds:.3f}",
            "-c:a",
            "pcm_s16le",
            str(processed),
        ],
        check=True,
        capture_output=True,
    )
    processed.replace(audio_path)
    return audio_path


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
