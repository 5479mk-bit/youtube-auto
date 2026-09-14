"""컷·차트·자막·음성을 하나의 세로 쇼츠로 합친다.

레이어 순서는 배경(Ken-Burns) → 어둡게 깔기 → 차트 → 훅 숫자 → 자막 → 배지다.
사진 위에 흰 글씨를 얹어야 하므로 배경을 한 겹 눌러 대비를 확보한다.
"""

from __future__ import annotations

import random
from pathlib import Path

from loguru import logger
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    concatenate_audioclips,
    concatenate_videoclips,
)

from ..models import PhotoCredit, Scene, Script, ScriptPart, StoryCandidate
from ..settings import RESOURCE_DIR
from ..subtitles.subtitle_builder import SubtitleCue
from ..visuals.chart_renderer import render_reveal_frames
from ..visuals.image_collector import ImageCollector
from . import overlays
from .film_look import apply_film_look
from .motion_sources import build_scene_clip

# 사진 위에 흰 자막을 얹으려면 배경을 꽤 눌러야 한다. 다큐 톤과도 맞는다.
BACKGROUND_DIM = 0.42


def build_part(
    script: Script,
    part: ScriptPart,
    candidate: StoryCandidate,
    scenes: list[Scene],
    cues: list[SubtitleCue],
    collector: ImageCollector,
    settings,
    work_dir: Path,
    out_path: Path,
    veo_client=None,
) -> tuple[float, list[PhotoCredit]]:
    width = settings.get("video.width")
    height = settings.get("video.height")
    fps = settings.get("video.fps")
    size = (width, height)

    background, credits = _build_background(scenes, collector, veo_client, settings, size)
    background = apply_film_look(
        background,
        size,
        grain=settings.get("film_look.grain", 6.0),
        vignette=settings.get("film_look.vignette", 0.30),
        exposure_breathing=settings.get("film_look.exposure_breathing", 0.02),
    )
    layers = [background, _dim_layer(size, background.duration)]

    chart_frames = _chart_frames(candidate, part, work_dir, size)
    layers.extend(_chart_layers(scenes, chart_frames, size))
    layers.extend(_hook_layers(part, scenes, work_dir, size, settings))
    layers.extend(_subtitle_layers(cues, work_dir, part.part, size, settings))
    layers.extend(
        _badge_layers(script, part, work_dir, size, background.duration, credits)
    )

    # 이어붙인 클립은 마지막 프레임 시각이 총 길이와 같아질 때 어느 컷에도 속하지 않아
    # 인덱스 오류가 난다. 끝을 아주 살짝 당겨 그 경계를 피한다.
    total_duration = max(background.duration - 1e-3, 0.1)
    video = CompositeVideoClip(layers, size=size).with_duration(total_duration)
    audio = _build_audio(part, settings, background.duration)
    if audio is not None:
        video = video.with_audio(audio)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    video.write_videofile(
        str(out_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        # 지정하지 않으면 moviepy 가 임시 오디오를 현재 디렉터리에 떨군다.
        temp_audiofile=str(work_dir / f"temp_audio_part{part.part}.m4a"),
        logger=None,
    )
    duration = float(video.duration)
    video.close()
    return duration, credits


def _build_background(
    scenes: list[Scene],
    collector: ImageCollector,
    veo_client,
    settings,
    size: tuple[int, int],
):
    parallax_amount = settings.get("parallax.shift", 0.06)
    dolly_amount = settings.get("parallax.dolly", 0.12)

    clips = []
    credits: list[PhotoCredit] = []
    for scene in scenes:
        clip, credit = build_scene_clip(
            scene, collector, veo_client, size, parallax_amount, dolly_amount
        )
        clips.append(clip.with_duration(scene.duration))
        if credit not in credits:
            credits.append(credit)
    return concatenate_videoclips(clips), credits


def _dim_layer(size: tuple[int, int], duration: float):
    return (
        ColorClip(size=size, color=(0, 0, 0))
        .with_opacity(BACKGROUND_DIM)
        .with_duration(duration)
    )


def _chart_frames(
    candidate: StoryCandidate, part: ScriptPart, work_dir: Path, size: tuple[int, int]
) -> list[Path]:
    if not candidate.series:
        return []
    width, height = size
    return render_reveal_frames(
        series=candidate.series,
        unit=candidate.series_unit,
        title=candidate.subject,
        out_dir=work_dir / f"charts_part{part.part}",
        width_px=int(width * 0.86),
        height_px=int(height * 0.34),
        highlight_index=0,
    )


def _chart_layers(scenes: list[Scene], chart_frames: list[Path], size: tuple[int, int]):
    if not chart_frames:
        return []

    width, height = size
    layers = []
    for scene in scenes:
        if not scene.show_chart or scene.chart_step <= 0:
            continue
        frame = chart_frames[min(scene.chart_step, len(chart_frames)) - 1]
        layers.append(
            ImageClip(str(frame))
            .with_start(scene.start)
            .with_duration(scene.duration)
            # 안전 밴드 한가운데. 위아래 UI 에 걸리지 않는 자리다.
            .with_position(("center", int(height * 0.33)))
        )
    return layers


def _hook_layers(
    part: ScriptPart, scenes: list[Scene], work_dir: Path, size: tuple[int, int], settings
):
    if not scenes or not part.hook_number:
        return []

    hook_duration = min(settings.get("video.hook_duration"), scenes[0].duration)
    path = overlays.render_hook_number(
        part.hook_number,
        work_dir / f"hook_part{part.part}.png",
        size,
        color=settings.get("subtitle.emphasis_color"),
    )
    return [ImageClip(str(path)).with_start(0).with_duration(hook_duration)]


def _subtitle_layers(
    cues: list[SubtitleCue], work_dir: Path, part_number: int, size: tuple[int, int], settings
):
    style = settings["subtitle"]
    safe_bottom = settings.get("video.safe_bottom")

    layers = []
    for index, cue in enumerate(cues):
        path = overlays.render_subtitle(
            cue,
            work_dir / f"sub_part{part_number}_{index:02d}.png",
            size,
            style,
            safe_bottom,
        )
        layers.append(
            ImageClip(str(path)).with_start(cue.start).with_duration(max(cue.end - cue.start, 0.1))
        )
    return layers


def _badge_layers(
    script: Script,
    part: ScriptPart,
    work_dir: Path,
    size: tuple[int, int],
    duration: float,
    credits: list[PhotoCredit],
):
    layers = []

    if part.part == 1:
        path = overlays.render_banner(
            "2부에서 계속", work_dir / "banner_cliffhanger.png", size, 58, 0.52
        )
        tail = min(3.0, duration)
        layers.append(
            ImageClip(str(path)).with_start(max(duration - tail, 0)).with_duration(tail)
        )
    else:
        # 2부 말미에 통계 출처와 사진 출처를 함께 남긴다. 공공누리 1유형은 출처 표기가
        # 이용 조건이라 화면에서 빠지면 안 된다.
        path = overlays.render_banner(
            script.source_note, work_dir / "banner_source.png", size, 34, 0.68, "#C3C2B7"
        )
        tail = min(4.0, duration)
        layers.append(
            ImageClip(str(path)).with_start(max(duration - tail, 0)).with_duration(tail)
        )

        photo_note = photo_credit_line(credits)
        if photo_note:
            photo_path = overlays.render_banner(
                photo_note, work_dir / "banner_photo.png", size, 26, 0.72, "#C3C2B7"
            )
            layers.append(
                ImageClip(str(photo_path))
                .with_start(max(duration - tail, 0))
                .with_duration(tail)
            )

    if script.is_sample:
        # 샘플 데이터로 만든 영상이 실제 통계로 오인되지 않도록 항상 표시한다.
        warning = overlays.render_banner(
            "샘플 데이터 · 실제 통계 아님",
            work_dir / "banner_sample.png",
            size,
            30,
            0.265,
            "#E66767",
        )
        layers.append(ImageClip(str(warning)).with_start(0).with_duration(duration))

    return layers


def _build_audio(part: ScriptPart, settings, duration: float):
    clips = [
        AudioFileClip(line.audio_path) for line in part.lines if line.audio_path
    ]
    if not clips:
        return None

    narration = concatenate_audioclips(clips)
    bgm = _load_bgm(settings, duration)
    if bgm is None:
        return narration.with_duration(min(narration.duration, duration))

    return CompositeAudioClip([narration, bgm]).with_duration(
        min(narration.duration, duration)
    )


def _load_bgm(settings, duration: float):
    bgm_dir = RESOURCE_DIR / "bgm"
    tracks = sorted(
        path for path in bgm_dir.glob("*") if path.suffix.lower() in {".mp3", ".m4a", ".wav"}
    )
    if not tracks:
        logger.info("resource/bgm 에 배경음악이 없어 내레이션만 사용합니다")
        return None

    track = random.choice(tracks)
    volume_db = settings.get("audio.bgm_volume_db")
    gain = 10 ** (volume_db / 20)

    clip = AudioFileClip(str(track))
    if clip.duration > duration:
        clip = clip.subclipped(0, duration)
    return clip.with_volume_scaled(gain)


def photo_credit_line(credits: list[PhotoCredit]) -> str:
    """화면과 설명란에 같이 쓰는 사진 출처 한 줄."""
    names = list(dict.fromkeys(credit.source_name for credit in credits if credit.source_name))
    if not names:
        return ""
    return "사진: " + ", ".join(names)
