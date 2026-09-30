"""컷·차트·자막·음성을 하나의 "포스터형" 세로 쇼츠로 합친다.

풀스크린 영상 위에 글자를 얹던 예전 방식과 달리, 어두운 캔버스 위에 액자 박스로 영상을
앉히고 위에는 후킹 타이틀이 내내 떠 있고 박스 아래에 자막이 붙는다. 레이어 순서는
캔버스 → 액자 영상 → 테두리 → 차트 → 타이틀 → 자막 → 배지다.
"""

from __future__ import annotations

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
    layers = [background.with_duration(total_duration)]
    layers.extend(_title_layers(part, work_dir, size, settings, background.duration))
    layers.extend(_overlay_layers(part, work_dir, size, total_duration))
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
    return duration, credits


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

    path = overlays.render_title_banner(
        part.hook_number,
        work_dir / f"title_part{part.part}.png",
        size,
        color=settings.get("video.frame_title_color", "#FFFFFF"),
        accent_color=settings.get("subtitle.emphasis_color"),
    )
    return [ImageClip(str(path)).with_start(0).with_duration(duration)]


def _overlay_layers(part: ScriptPart, work_dir: Path, size: tuple[int, int], total: float):
    """문장에 overlay(핵심 숫자/단어)가 있으면 그 문장이 읽히는 동안 화면 중앙에 띄운다."""
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
        layers.append(ImageClip(str(path)).with_start(start).with_duration(length))
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
    bed = _load_bgm(settings, duration)
    if bed is not None:
        layers.append(bed)
    layers.extend(_sfx_layers(scenes or [], settings, total))

    if len(layers) == 1:
        return narration.with_duration(total)
    return CompositeAudioClip(layers).with_duration(total)


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
    """`sfx` 태그가 붙은 컷이 시작될 때 그 태그에 맞는 소리를 얹는다."""
    # "효과음이 난잡하다"는 지적 이후: 대본이 명시한 sfx 만 쓴다(자동 전환음 없음).
    # plan_typed_scenes 는 문장 하나 = 씬 하나라 대본의 sfx 배정이 이미 컷 전환과
    # 맞아떨어진다 — 여기에 더 얹으면 겹쳐서 시끄러워진다.
    gain = _db_to_gain(settings.get("audio.sfx_volume_db", -6))
    layers = []
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
    return layers


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


def photo_credit_line(credits: list[PhotoCredit]) -> str:
    """화면과 설명란에 같이 쓰는 사진 출처 한 줄."""
    names = list(dict.fromkeys(credit.source_name for credit in credits if credit.source_name))
    if not names:
        return ""
    return "사진: " + ", ".join(names)
