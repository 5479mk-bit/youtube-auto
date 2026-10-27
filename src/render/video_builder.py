"""컷·차트·자막·음성을 하나의 "포스터형" 세로 쇼츠로 합친다.

풀스크린 영상 위에 글자를 얹던 예전 방식과 달리, 어두운 캔버스 위에 액자 박스로 영상을
앉히고 위에는 후킹 타이틀이 내내 떠 있고 박스 아래에 자막이 붙는다. 레이어 순서는
캔버스 → 액자 영상 → 테두리 → 차트 → 타이틀 → 자막 → 배지다.
"""

from __future__ import annotations

import math
import os
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

from .. import assets
from ..models import PhotoCredit, Scene, Script, ScriptPart, StoryCandidate
from ..settings import RESOURCE_DIR
from ..subtitles.subtitle_builder import SubtitleCue
from ..visuals.chart_renderer import render_reveal_frames
from ..visuals.image_collector import ImageCollector
from . import overlays
from .film_look import apply_film_look
from .motion_sources import build_scene_clip



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
    ai_client=None,
    is_combined: bool = False,
) -> tuple[float, list[PhotoCredit]]:
    width = settings.get("video.width")
    height = settings.get("video.height")
    fps = settings.get("video.fps")
    size = (width, height)

    background, credits = _build_background(
        scenes, collector, veo_client, settings, size, ai_client
    )
    background = apply_film_look(
        background,
        size,
        grain=settings.get("film_look.grain", 0),
        vignette=settings.get("film_look.vignette", 0),
        exposure_breathing=settings.get("film_look.exposure_breathing", 0),
    )

    # 풀스크린 레이아웃: 3D 엔지니어링 단면도 인포그래픽 스타일은 액자 박스 없이
    # 화면 전체를 꽉 채운다. `_build_background`가 이미 `size` 그대로(1080×1920)
    # 클립을 만들어주므로 추가 리사이즈·크롭이 필요 없다.
    total_duration = max(background.duration - 1e-3, 0.1)
    background = _apply_camera_shake(background, scenes, size, settings)
    layers = [background.with_duration(total_duration)]
    layers.extend(_impact_flash_layers(scenes, size, total_duration, settings))
    layers.extend(_title_layers(part, work_dir, size, settings, background.duration))
    layers.extend(_overlay_layers(part, work_dir, size, total_duration, settings))
    layers.extend(_subtitle_layers(cues, work_dir, part.part, size, settings, box=None))
    layers.extend(
        _badge_layers(script, part, work_dir, size, background.duration, credits, is_combined)
    )

    # 이어붙인 클립은 마지막 프레임 시각이 총 길이와 같아질 때 어느 컷에도 속하지 않아
    # 인덱스 오류가 난다. 끝을 아주 살짝 당겨 그 경계를 피한다.
    video = CompositeVideoClip(layers, size=size).with_duration(total_duration)
    audio = _build_audio(part, settings, background.duration, scenes)
    if audio is not None:
        video = video.with_audio(audio)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    video.write_videofile(
        str(out_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        # 러너는 2코어라 medium 으로는 한 편에 10분이 넘게 걸린다. 쇼츠 해상도에서
        # veryfast 와 medium 의 화질 차이는 눈에 띄지 않는다.
        preset=settings.get("video.preset", "veryfast"),
        # 비트레이트를 안 정하면 moviepy가 넉넉하게 잡아 40초짜리가 70MB를 넘기도
        # 한다 — 텔레그램 봇 API 는 50MB 가 상한이라 그러면 검수 전송이 통째로
        # 실패한다(413). 캡을 걸어 40초 기준 20MB 안쪽으로 들어오게 한다.
        bitrate=settings.get("video.bitrate", "3000k"),
        audio_bitrate=settings.get("video.audio_bitrate", "128k"),
        threads=settings.get("video.threads", 0) or os.cpu_count() or 2,
        # 지정하지 않으면 moviepy 가 임시 오디오를 현재 디렉터리에 떨군다.
        temp_audiofile=str(work_dir / f"temp_audio_part{part.part}.m4a"),
        logger=None,
    )
    duration = float(video.duration)
    video.close()
    _normalize_loudness(out_path, settings)
    return duration, credits


def _normalize_loudness(path: Path, settings) -> None:
    """최종 음량을 쇼츠·틱톡 기준(-14 LUFS)에 맞춘다(2026-10-27).

    레퍼런스 영상들(틱톡 수십만~60만 회)은 평균 -13dB 로 꽉 차 있고 목소리 밑에 음악이 계속 깔려
    있다. 피드에서 앞뒤 영상보다 소리가 작으면 그것만으로 약해 보인다. 영상 스트림은 그대로 복사하고
    오디오만 다시 인코딩한다. 실패하면 원본을 그대로 둔다.
    """
    target = settings.get("audio.loudness_lufs", -14)
    if not target:
        return
    import subprocess

    import imageio_ffmpeg

    tmp = path.with_name(f"{path.stem}_loud{path.suffix}")
    try:
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(path),
                "-c:v", "copy",
                "-af", f"loudnorm=I={float(target):.1f}:TP=-1.5:LRA=11",
                "-ar", "44100",
                "-c:a", "aac", "-b:a", str(settings.get("video.audio_bitrate", "128k")),
                "-movflags", "+faststart",
                str(tmp),
            ],
            check=True,
            capture_output=True,
        )
        tmp.replace(path)
    except Exception as exc:
        logger.warning(f"음량 정규화 실패 — 원본 음량 그대로 둡니다: {exc}")
        tmp.unlink(missing_ok=True)


def _build_background(
    scenes: list[Scene],
    collector: ImageCollector,
    veo_client,
    settings,
    size: tuple[int, int],
    ai_client=None,
):
    parallax_amount = settings.get("parallax.shift", 0.06)
    dolly_amount = settings.get("parallax.dolly", 0.12)

    clips = []
    credits: list[PhotoCredit] = []
    for scene in scenes:
        clip, credit = build_scene_clip(
            scene, collector, veo_client, size, parallax_amount, dolly_amount, ai_client, settings
        )
        clips.append(clip.with_duration(scene.duration))
        if credit not in credits:
            credits.append(credit)
    return concatenate_videoclips(clips), credits


# 임팩트가 큰 sfx 태그가 붙은 컷에만 카메라 셰이크를 건다 — 매 컷마다 흔들면
# 멀미 나는 화면이 된다(사용자 지시, 2026-09-30: "화면이 미세하게 흔들리는
# 카메라 셰이크 효과").
_SHAKE_SFX = {"sub_boom", "crack", "alarm", "braam"}


def _apply_camera_shake(clip, scenes: list[Scene], size: tuple[int, int], settings):
    """임팩트 sfx 컷이 시작되는 순간 화면을 짧게(약 0.3초) 흔들고 빠르게 잦아든다.

    배경 클립 전체를 살짝(기본 5%) 확대해둔 뒤, 그 여유 공간 안에서만 위치를
    흔든다 — 그래야 흔들려도 프레임 가장자리에 빈 틈이 안 보인다.
    """
    if not settings.get("video.camera_shake", True):
        return clip

    windows = [
        (scene.start, scene.start + min(scene.duration, 0.35))
        for scene in scenes
        if scene.sfx in _SHAKE_SFX
    ]
    if not windows:
        return clip

    amplitude = settings.get("video.camera_shake_px", 10)
    margin = settings.get("video.camera_shake_margin", 1.05)
    base_x = -size[0] * (margin - 1) / 2
    base_y = -size[1] * (margin - 1) / 2

    def _position(t: float) -> tuple[float, float]:
        for start, end in windows:
            if start <= t < end:
                local = t - start
                span = max(end - start, 1e-3)
                decay = max(0.0, 1.0 - local / span)  # 시작이 가장 세고 빠르게 잦아든다
                dx = amplitude * decay * math.sin(2 * math.pi * 22 * local)
                dy = amplitude * decay * math.cos(2 * math.pi * 27 * local)
                return (base_x + dx, base_y + dy)
        return (base_x, base_y)

    return clip.resized(margin).with_position(_position)


_FLASH_SFX = {"crack", "braam"}


def _impact_flash_layers(scenes: list[Scene], size: tuple[int, int], total: float, settings):
    """무너지는 순간(crack/braam)에 흰 플래시를 아주 짧게 친다(2026-10-27).

    쇼츠는 1~2초마다 '패턴 인터럽트'(화면이 확 바뀌는 순간)가 있어야 손가락이 멈춘다.
    흔들림(_apply_camera_shake)과 같은 순간에 겹쳐 충격을 키운다. 너무 잦으면 피로하니
    붕괴·클라이맥스 효과음에만.
    """
    if not settings.get("video.impact_flash", True):
        return []
    seconds = settings.get("video.impact_flash_seconds", 0.12)
    opacity = settings.get("video.impact_flash_opacity", 0.55)
    layers = []
    for scene in scenes:
        if scene.sfx in _FLASH_SFX and scene.start < total - seconds:
            layers.append(
                ColorClip(size=size, color=(255, 255, 255))
                .with_duration(seconds)
                .with_start(scene.start)
                .with_opacity(opacity)
            )
    return layers


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _frame_box(size: tuple[int, int], settings) -> tuple[int, int, int, int]:
    """액자 박스의 절대 좌표(x1, y1, x2, y2). 위는 후킹 타이틀, 아래는 자막 자리로
    비워두고 그 사이에 영상을 앉힌다."""
    width, height = size
    top = settings.get("video.frame_top", 0.22)
    bottom = settings.get("video.frame_bottom", 0.78)
    margin_x = settings.get("video.frame_margin_x", 0.06)
    x1 = int(width * margin_x)
    x2 = int(width * (1 - margin_x))
    y1 = int(height * top)
    y2 = int(height * bottom)
    return x1, y1, x2, y2


def _canvas_layer(size: tuple[int, int], duration: float, settings):
    color = _hex_to_rgb(settings.get("video.frame_bg_color", "#0B0B10"))
    return ColorClip(size=size, color=color).with_duration(duration)


def _boxed_clip(clip, box: tuple[int, int, int, int]):
    """영상을 액자 박스 크기로 cover-fit 크롭·리사이즈해 그 자리에 앉힌다.

    `motion_sources._official_video_clip` 의 16:9→9:16 크롭과 같은 방식이다 — 너비를
    박스에 맞춰 줄인 뒤 세로 중앙을 박스 높이만큼 잘라낸다.
    """
    x1, y1, x2, y2 = box
    box_w, box_h = x2 - x1, y2 - y1
    width, height = clip.size
    scale = box_w / width
    new_width, new_height = box_w, int(height * scale)
    resized = clip.resized((new_width, new_height))
    if new_height > box_h:
        top = (new_height - box_h) // 2
        resized = resized.cropped(y1=top, height=box_h)
    return resized.with_position((x1, y1))


def _frame_border_layer(
    size: tuple[int, int],
    box: tuple[int, int, int, int],
    duration: float,
    work_dir: Path,
    part_number: int,
    settings,
):
    path = overlays.render_frame_border(
        work_dir / f"frame_border_part{part_number}.png",
        size,
        box,
        color=settings.get("video.frame_border_color", "#E8C97A"),
        width_px=settings.get("video.frame_border_width", 4),
        radius=settings.get("video.frame_border_radius", 18),
    )
    return ImageClip(str(path)).with_duration(duration)


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


def _title_layers(
    part: ScriptPart, work_dir: Path, size: tuple[int, int], settings, duration: float
):
    """포스터형 상단 타이틀. render_hook_number 와 달리 반짝 나왔다 사라지지 않고
    파트 내내 떠 있다 — 참고 이미지의 헤더 문구 자리다."""
    if not part.hook_number:
        return []

    # 2026-10-26: 첫 3초는 질문 문구를 화면 정중앙에 크게(이탈 방지), 그 뒤로는 상단 배너로.
    # 같은 문구가 두 곳에 동시에 뜨지 않게 배너는 질문이 사라진 뒤에 나온다.
    hook_seconds = min(float(settings.get("video.hook_question_seconds", 3.0) or 0), duration)
    layers = []
    if hook_seconds > 0:
        question = overlays.render_hook_question(
            part.hook_number,
            work_dir / f"hook_question_part{part.part}.png",
            size,
            stat=part.hook_stat,
            stat_color=settings.get("subtitle.emphasis_color", "#FFD84D") or "#FFD84D",
        )
        layers.append(ImageClip(str(question)).with_start(0).with_duration(hook_seconds))

    path = overlays.render_title_banner(
        part.hook_number,
        work_dir / f"title_part{part.part}.png",
        size,
        color=settings.get("video.frame_title_color", "#FFFFFF"),
        accent_color=settings.get("subtitle.emphasis_color"),
    )
    if duration - hook_seconds > 0:
        layers.append(
            ImageClip(str(path)).with_start(hook_seconds).with_duration(duration - hook_seconds)
        )
    return layers


def _overlay_pop_scale(t: float, pop_seconds: float = 0.25) -> float:
    """숫자/단어 오버레이가 뜨는 순간 커졌다 살짝 작아지며 자리 잡는 "팝인" 곡선.

    0→60%(0.6배까지) 급격히 커지고, 나머지 40%(0.15배 오버슈트) 구간에서 1.0배로
    가라앉는다 — 틱톡/릴스에서 흔한 kinetic typography 연출(사용자 지시,
    2026-09-30). `t` 는 이 오버레이 클립 자신의 로컬 시간(시작=0)이다.
    """
    if t >= pop_seconds:
        return 1.0
    progress = t / pop_seconds
    if progress < 0.6:
        return 0.6 + (1.15 - 0.6) * (progress / 0.6)
    settle = (progress - 0.6) / 0.4
    return 1.15 - 0.15 * settle


def _overlay_layers(
    part: ScriptPart, work_dir: Path, size: tuple[int, int], total: float, settings=None
):
    """문장에 overlay(핵심 숫자/단어)가 있으면 그 문장이 읽히는 동안 화면 중앙에 띄운다.

    화면에 나타나는 순간 커졌다 자리 잡는 팝인 애니메이션을 준다(설정으로 끌 수 있다).
    """
    kinetic = settings is None or settings.get("video.kinetic_overlay", True)
    layers = []
    cursor = 0.0
    for index, line in enumerate(part.lines):
        start, cursor = cursor, cursor + max(line.duration, 0.0)
        if not line.overlay or start >= total:
            continue
        path = overlays.render_center_overlay(
            line.overlay, work_dir / f"overlay_part{part.part}_{index:02d}.png", size
        )
        length = max(min(cursor, total) - start, 0.1)
        clip = ImageClip(str(path)).with_start(start).with_duration(length)
        if kinetic:
            clip = clip.resized(_overlay_pop_scale).with_position("center")
        layers.append(clip)
    return layers


def _subtitle_layers(
    cues: list[SubtitleCue],
    work_dir: Path,
    part_number: int,
    size: tuple[int, int],
    settings,
    box: tuple[int, int, int, int] | None = None,
):
    style = settings["subtitle"]
    # 자막은 이제 풀스크린 영상 위가 아니라 액자 박스 바로 아래, 캔버스 위에 앉는다.
    # bottom_ratio 는 "화면 하단에서부터"의 비율이라 박스 아래끝을 그 기준으로 환산한다.
    if box is not None:
        _, _, _, box_bottom_px = box
        gap = size[1] * 0.03
        safe_bottom = 1 - (box_bottom_px + gap) / size[1]
    else:
        safe_bottom = settings.get("video.safe_bottom")

    dynamic = settings.get("subtitle.dynamic_pop", True)
    layers = []
    for index, cue in enumerate(cues):
        path, (bx, by, bw, bh) = overlays.render_subtitle(
            cue,
            work_dir / f"sub_part{part_number}_{index:02d}.png",
            size,
            style,
            safe_bottom,
        )
        clip = ImageClip(str(path)).with_start(cue.start).with_duration(
            max(cue.end - cue.start, 0.1)
        )
        # 단어 하이라이트로 쪼갠 큐는 묶음의 첫 단어에서만 튀어 오른다(단어마다 튀면 어지럽다).
        if dynamic and getattr(cue, "active_word", -1) <= 0:
            center_x, center_y = bx + bw / 2, by + bh / 2

            def _pos(t: float, cx=center_x, cy=center_y, w=bw, h=bh) -> tuple[float, float]:
                scale = _subtitle_pop_scale(t)
                return (cx - w * scale / 2, cy - h * scale / 2)

            clip = clip.resized(_subtitle_pop_scale).with_position(_pos)
        else:
            # 잘라낸 PNG 라 제자리(bx, by)에 놓아야 한다 — 안 그러면 화면 왼쪽 위(0, 0)에 붙는다.
            clip = clip.with_position((bx, by))
        layers.append(clip)
    return layers


def _subtitle_pop_scale(t: float, pop_seconds: float = 0.14) -> float:
    """자막 큐가 뜨는 순간 살짝 튀어오르듯 커졌다 자리 잡는 짧은 팝인.

    오버레이 숫자(`_overlay_pop_scale`)보다 훨씬 작고 빠르다 — 자막은 1초 안팎으로
    자주 바뀌므로 큰 모션이면 어지럽다. 0.82배에서 시작해 1.06배로 살짝 오버슈트한
    뒤 1.0배로 가라앉는다. 글자가 톡톡 튀어 들어오는 "타이핑" 리듬을 준다(사용자
    지시, 2026-10-01).
    """
    if t >= pop_seconds:
        return 1.0
    progress = t / pop_seconds
    if progress < 0.65:
        return 0.82 + (1.06 - 0.82) * (progress / 0.65)
    settle = (progress - 0.65) / 0.35
    return 1.06 - 0.06 * settle


def _badge_layers(
    script: Script,
    part: ScriptPart,
    work_dir: Path,
    size: tuple[int, int],
    duration: float,
    credits: list[PhotoCredit],
    is_combined: bool = False,
):
    """말미 배지(다음 편 안내 또는 출처 표기)를 붙인다.

    `_merge_parts()`(합본 렌더)는 1·2부를 이어 붙인 뒤에도 `part.part`를 1로 둔다
    (화면 상단 타이틀은 1부 hook_number 를 그대로 쓰기 위해서다) — 그래서 이 함수가
    "part.part == 1 이면 다음 편 안내"라는 옛 가정을 그대로 쓰면, 합본(사실상 유일한
    영상)의 끝에도 "2부에서 계속"이 찍힌다(실제로 있었던 사고 — 나레이션 텍스트가
    아니라 이 배지 그래픽이 원인이었다). `is_combined`가 참이면 애초에 다음 편이
    없으므로 그 분기를 절대 타지 않는다.
    """
    layers = []

    if part.part == 1 and not is_combined:
        path = overlays.render_banner(
            "2부에서 계속", work_dir / "banner_cliffhanger.png", size, 58, 0.52
        )
        tail = min(3.0, duration)
        layers.append(
            ImageClip(str(path)).with_start(max(duration - tail, 0)).with_duration(tail)
        )
    else:
        # 합본이면 이 영상 말미에, 2부작이면 2부 말미에 통계 출처와 사진 출처를
        # 함께 남긴다. 공공누리 1유형은 출처 표기가 이용 조건이라 화면에서 빠지면
        # 안 된다.
        path = overlays.render_banner(
            script.source_note, work_dir / "banner_source.png", size, 34, 0.68, "#C3C2B7"
        )
        tail = min(4.0, duration)
        layers.append(
            ImageClip(str(path)).with_start(max(duration - tail, 0)).with_duration(tail)
        )

        # 스페인어 채널(재난)에 한국어 "사진:" 이 찍히던 것 — 출처 문구 언어를 따라간다.
        prefix = "Fotos: " if script.source_note.startswith("Fuente") else "사진: "
        photo_note = photo_credit_line(credits, prefix=prefix)
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


def _build_audio(part: ScriptPart, settings, duration: float, scenes: list[Scene] | None = None):
    """내레이션 + 배경 드론 + 효과음(더킹)을 한 트랙으로 섞는다."""
    clips = [
        AudioFileClip(line.audio_path) for line in part.lines if line.audio_path
    ]
    if not clips:
        return None

    narration = concatenate_audioclips(clips)
    total = min(narration.duration, duration)

    layers = [narration]
    sfx_layers, duck_windows = _sfx_layers(scenes or [], settings, total)
    bed = _load_bgm(settings, duration)
    if bed is not None:
        # 사용자 지시(2026-09-30): 효과음이 터지는 동안 배경음을 살짝 눌러줘서
        # ("더킹") 타격음이 더 또렷하게 튀어나오게 한다 — 시네마틱 믹싱의 기본기다.
        bed = _duck(bed, duck_windows, settings.get("audio.sfx_duck_db", -4))
        layers.append(bed)
    layers.extend(sfx_layers)

    if len(layers) == 1:
        return narration.with_duration(total)
    return CompositeAudioClip(layers).with_duration(total)


def _duck(clip, windows: list[tuple[float, float]], duck_db: float):
    """`windows` 구간 동안만 clip 볼륨을 duck_db 만큼 더 낮춘다(그 밖은 그대로)."""
    if not windows or duck_db == 0:
        return clip
    factor = _db_to_gain(duck_db)
    for start, end in windows:
        if end > start:
            clip = clip.with_volume_scaled(factor, start_time=start, end_time=end)
    return clip


_SFX_FILES = {"paper_rustle": assets.AUDIO_PAPER_RUSTLE}


def _pick_sfx_path(tag: str, used_index: dict[str, int]) -> Path | None:
    """태그에 맞는 실제 효과음 파일을 하나 고른다.

    `assets.SFX_POOLS` 는 태그당 실제 효과음(Mixkit, 무료 라이선스) 2개씩을 묶어
    둔다. 무작위로 고르면 같은 파일이 연달아 뽑히거나, 한 편에서 10개 중 절반은
    한 번도 안 나올 수 있다("10개 다 쓰라고 적절하게" 요청) — `used_index`에
    태그별로 몇 번째까지 썼는지 기록해두고 그 다음 것을 순서대로 돌려써서, 같은
    태그가 두 번 이상 나오면 반드시 두 파일 다 한 번씩은 쓰이게 한다.
    `paper_rustle` 처럼 풀이 없는 태그는 `_SFX_FILES` 단일 경로를 그대로 쓴다.
    """
    pool = assets.SFX_POOLS.get(tag)
    if pool:
        existing = [path for path in pool if path.exists()]
        if existing:
            index = used_index.get(tag, 0) % len(existing)
            used_index[tag] = index + 1
            return existing[index]
    return _SFX_FILES.get(tag)


def _sfx_layers(scenes: list[Scene], settings, total: float):
    """`sfx` 태그가 붙은 컷이 시작될 때 그 태그에 맞는 소리를 얹는다.

    반환값은 (오디오 레이어 목록, 배경음 더킹 구간 목록) 쌍이다 — 더킹 구간은
    실제로 튼 효과음의 길이 그대로라, `_build_audio` 가 그 시간 동안 배경음을
    낮춰 효과음이 또렷하게 들리게 한다(사용자 지시, 2026-09-30).
    """
    # "효과음이 난잡하다"는 지적 이후: 대본이 명시한 sfx 만 쓴다(자동 전환음 없음).
    # plan_typed_scenes 는 문장 하나 = 씬 하나라 대본의 sfx 배정이 이미 컷 전환과
    # 맞아떨어진다 — 여기에 더 얹으면 겹쳐서 시끄러워진다.
    gain = _db_to_gain(settings.get("audio.sfx_volume_db", -6))
    layers = []
    duck_windows: list[tuple[float, float]] = []
    used_index: dict[str, int] = {}
    for scene in scenes:
        if scene.sfx == "none" or scene.start >= total:
            continue
        path = _pick_sfx_path(scene.sfx, used_index)
        if path is None or not path.exists():
            # 매핑된 파일이 없거나 실제로 없으면(에셋 다운로드 누락 등) 완전히
            # 조용해지는 대신 기본 임팩트음 풀에서 대체한다 — 안전 장치.
            path = _pick_sfx_path("sub_boom", used_index)
            if path is None or not path.exists():
                continue
        try:
            hit = AudioFileClip(str(path))
        except Exception as exc:
            logger.warning(f"효과음을 쓰지 못했습니다: {exc}")
            continue

        room = max(total - scene.start, 0.05)
        length = min(hit.duration, room)
        layers.append(
            hit.subclipped(0, length).with_start(scene.start).with_volume_scaled(gain)
        )
        duck_windows.append((scene.start, scene.start + length))
    return layers, duck_windows


def _load_bgm(settings, duration: float):
    """배경 베드. 화음 패드 > 다크 드론 > 예전 BGM 폴더 순으로 고른다.

    드론(45Hz 룸블링, 멜로디 없음)만으로는 "웅장함이 없다"는 지적이 있었다 —
    단조 화음을 느리게 스웰하는 패드(epic_pad)를 새로 만들어 우선순위를 앞에
    둔다. 여전히 작게 깔리므로(bgm_volume_db) 내레이션과 부딪히지 않는다.
    """
    if assets.AUDIO_EPIC_PAD.exists():
        track = assets.AUDIO_EPIC_PAD
    elif assets.AUDIO_DRONE.exists():
        track = assets.AUDIO_DRONE
    else:
        bgm_dir = RESOURCE_DIR / "bgm"
        tracks = sorted(
            path for path in bgm_dir.glob("*") if path.suffix.lower() in {".mp3", ".m4a", ".wav"}
        )
        if not tracks:
            logger.info("배경음이 없어 내레이션만 사용합니다 (./download_assets.sh)")
            return None
        track = random.choice(tracks)

    clip = AudioFileClip(str(track))

    # 음원이 영상보다 짧으면 뒤쪽이 통째로 무음이 된다. 길이를 채울 때까지 이어 붙인다.
    if clip.duration < duration:
        clip = concatenate_audioclips([clip] * (int(duration // clip.duration) + 1))
    clip = clip.subclipped(0, duration)

    base_gain = _db_to_gain(settings.get("audio.bgm_volume_db", -18))
    hook_gain = _db_to_gain(settings.get("audio.bgm_hook_volume_db", -18))
    hook_seconds = min(float(settings.get("video.hook_duration", 3.0)), duration)

    if hook_seconds <= 0 or hook_gain == base_gain:
        return clip.with_volume_scaled(base_gain)

    # 시청 이탈은 첫 몇 초에서 갈린다. 그 구간만 음악을 크게 깔아 붙잡는다.
    return concatenate_audioclips(
        [
            clip.subclipped(0, hook_seconds).with_volume_scaled(hook_gain),
            clip.subclipped(hook_seconds).with_volume_scaled(base_gain),
        ]
    )


def _db_to_gain(volume_db: float) -> float:
    return 10 ** (float(volume_db) / 20)


def photo_credit_line(credits: list[PhotoCredit], prefix: str = "사진: ") -> str:
    """화면과 설명란에 같이 쓰는 사진 출처 한 줄.

    기본 접두사(한국어)는 텔레그램 운영자 알림용이다. 유튜브 설명란(시청자 공개)에
    쓸 때는 채널 언어에 맞는 접두사를 넘긴다(disaster 데이터셋은 스페인어 "Fotos: ").
    """
    names = list(dict.fromkeys(credit.source_name for credit in credits if credit.source_name))
    if not names:
        return ""
    return prefix + ", ".join(names)
