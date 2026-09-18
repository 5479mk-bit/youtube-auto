"""문장을 컷으로 쪼개고, 각 컷을 무엇으로 움직일지 정한다.

컷 배치의 두 원칙:
- 연속한 컷은 절대 같은 방향 무빙을 쓰지 않는다. 정지 사진의 지루함은 무빙의 반복에서 온다.
- 도입부는 통째로 Veo 클립 하나로 덮는다. Veo 는 8초 단위로 과금되므로 3초만 쓰고 5초를
  버리면 돈을 버리는 것과 같다. 시청 이탈이 결정되는 구간이기도 해서 여기에 몰아준다.
"""

from __future__ import annotations

import math

from ..models import ENGINE_NAMES, SCENE_TYPE_ENGINE, CameraMove, Scene, ScriptPart

MOVE_CYCLE: list[CameraMove] = ["push_in", "pan_lr", "pull_out", "pan_rl", "push_in", "pan_tb"]
MIN_CUT_DURATION = 0.5


def plan_scenes(
    part: ScriptPart,
    cut_duration_max: float,
    freeze_last: bool,
    series_labels: list[str],
    veo_seconds: float = 0.0,
    intro_engine: str = "veo",
    intro_video_path: str = "",
) -> list[Scene]:
    """veo_seconds 만큼의 도입부를 생성/공식 영상 씬 하나로 묶고, 나머지를 컷으로 쪼갠다.

    intro_engine 이 "official_video" 면 국가유산청 공식 영상을 쓴다 — Veo 생성 없이 실사
    영상을 그대로 쓸 수 있어 그만큼 과금을 건너뛴다. 영상이 없거나 부적합하면 호출하는
    쪽(pipeline)이 intro_engine 을 "veo" 로 되돌려 지금까지의 동작과 같게 만든다.
    """
    scenes: list[Scene] = []
    cursor = 0.0
    move_index = 0
    chart_step = 0

    for line_index, line in enumerate(part.lines):
        if line.show_chart:
            chart_step = _next_chart_step(chart_step, line, series_labels)

        # TTS 가 빈 음원을 돌려주면 길이 0 컷이 생기고, 이어붙인 클립은 그 지점에서
        # 프레임을 찾지 못해 렌더가 멈춘다. 화면에 보일 최소 길이는 보장한다.
        line_duration = max(line.duration, MIN_CUT_DURATION)

        # 도입부 구간에 걸치는 문장은 쪼개지 않고 도입부 씬에 흡수시킨다.
        if cursor < veo_seconds:
            take = min(line_duration, max(veo_seconds - cursor, 0.0))
            if scenes and scenes[0].engine == intro_engine:
                scenes[0].duration += take
                if line.show_chart:
                    scenes[0].show_chart = True
                    scenes[0].chart_step = chart_step
            else:
                scenes.append(
                    Scene(
                        start=0.0,
                        duration=take,
                        image_query_ko=line.visual_query_ko,
                        image_query_en=line.visual_query_en,
                        camera="push_in",
                        engine=intro_engine,
                        show_chart=line.show_chart,
                        chart_step=chart_step if line.show_chart else 0,
                        is_hook=True,
                        official_video_path=intro_video_path,
                    )
                )
            cursor += take
            remaining = line_duration - take
            if remaining <= 0.05:
                continue
            line_duration = remaining

        cut_count = max(1, math.ceil(line_duration / cut_duration_max))
        cut_duration = line_duration / cut_count

        for _ in range(cut_count):
            camera, move_index = _next_move(move_index, scenes[-1].camera if scenes else None)
            scenes.append(
                Scene(
                    start=cursor,
                    duration=cut_duration,
                    image_query_ko=line.visual_query_ko,
                    image_query_en=line.visual_query_en,
                    camera=camera,
                    engine="parallax",
                    show_chart=line.show_chart,
                    chart_step=chart_step if line.show_chart else 0,
                    is_hook=not scenes,
                )
            )
            cursor += cut_duration

    if freeze_last and scenes and scenes[-1].engine not in ("veo", "official_video"):
        scenes[-1].camera = "freeze"

    return scenes


def interleave_veo(
    scenes: list[Scene],
    clip_seconds: float,
    still_seconds: float,
    max_clips: int,
) -> list[Scene]:
    """사진 컷 뒤에 '그 사진이 움직이는' Veo 컷이 오도록 번갈아 배치한다.

    정지 사진만 계속 훑으면 지루하고, 전부 Veo 로 만들면 편당 5천원이 넘는다. 사진을 먼저
    보여주고 같은 사진이 살아나는 컷을 붙이면 리듬이 생기면서 비용은 클립 수만큼만 든다.

    Veo 컷은 바로 앞 사진 컷과 같은 검색어를 물려받는다 — ImageCollector 가 검색어로
    캐시하므로 실제로 '방금 그 사진'이 움직이는 클립이 된다.

    Veo 는 clip_seconds(보통 8초) 단위로 과금되므로, 남은 길이가 그만큼 안 되면 아예
    만들지 않는다. 3초 쓰자고 8초 값을 내는 게 제일 아까운 낭비다.
    """
    if max_clips <= 0 or clip_seconds <= 0:
        return scenes

    result: list[Scene] = []
    index = 0
    clips_used = 0
    still_elapsed = 0.0

    while index < len(scenes):
        scene = scenes[index]
        result.append(scene)
        index += 1

        # 도입부(생성 영상/공식 영상)는 이미 배정된 구간이라 건드리지 않는다.
        if scene.engine != "parallax":
            continue

        still_elapsed += scene.duration
        if clips_used >= max_clips or still_elapsed < still_seconds:
            continue

        taken, total, cursor = [], 0.0, index
        while cursor < len(scenes) and total < clip_seconds and scenes[cursor].engine == "parallax":
            taken.append(scenes[cursor])
            total += scenes[cursor].duration
            cursor += 1

        if total < clip_seconds:
            continue

        # 생성 클립은 정확히 clip_seconds 다. 씬을 그보다 길게 잡으면 그 차이만큼 화면이
        # 빈다 — 넘치는 만큼은 사진 컷으로 되돌려준다.
        result.append(
            Scene(
                start=taken[0].start,
                duration=clip_seconds,
                image_query_ko=scene.image_query_ko,
                image_query_en=scene.image_query_en,
                camera="push_in",
                engine="veo",
                show_chart=taken[0].show_chart,
                chart_step=taken[0].chart_step,
            )
        )
        leftover = total - clip_seconds
        if leftover > 0.05:
            tail = taken[-1]
            result.append(
                Scene(
                    start=taken[0].start + clip_seconds,
                    duration=leftover,
                    image_query_ko=tail.image_query_ko,
                    image_query_en=tail.image_query_en,
                    camera=tail.camera,
                    engine="parallax",
                    show_chart=tail.show_chart,
                    chart_step=tail.chart_step,
                )
            )
        clips_used += 1
        still_elapsed = leftover
        index = cursor

    return result


def plan_typed_scenes(part: ScriptPart, freeze_last: bool) -> list[Scene]:
    """대본이 문장마다 지정한 화면 종류(scene_type)를 그대로 컷으로 옮긴다.

    문화재 경로(`plan_scenes`)는 사진 한 장을 여러 컷으로 쪼개 무빙만 바꿔 지루함을 덜었다.
    실록 경로는 그럴 필요가 없다 — 컷마다 만들 화면이 아예 다르다(생성 영상 / 생성 정지화 /
    사료 / 지도). 문장 하나가 컷 하나다. 문장을 쪼개면 사료 컷 중간에 지도가 끼어드는
    이상한 그림이 된다.
    """
    scenes: list[Scene] = []
    cursor = 0.0
    move_index = 0

    for line in part.lines:
        duration = max(line.duration, MIN_CUT_DURATION)
        camera, move_index = _next_move(move_index, scenes[-1].camera if scenes else None)
        scenes.append(
            Scene(
                start=cursor,
                duration=duration,
                image_query_ko=line.visual_query_ko,
                image_query_en=line.visual_query_en,
                camera=camera,
                engine=SCENE_TYPE_ENGINE.get(line.scene_type, "ai_image"),
                is_hook=not scenes,
                scene_type=line.scene_type,
                prompt=line.prompt,
                doc_title=line.doc_title,
                doc_highlight=line.doc_highlight,
                map_region=line.map_region,
                map_spots=list(line.map_spots),
                sfx=line.sfx,
            )
        )
        cursor += duration

    # 생성 영상 컷은 자체 모션이 있어 카메라를 멈추면 안 된다.
    if freeze_last and scenes and scenes[-1].engine not in ("veo", "official_video"):
        scenes[-1].camera = "freeze"

    return scenes


def engine_counts(scenes: list[Scene]) -> dict[str, int]:
    """엔진별 컷 수. 안 쓰인 엔진도 0 으로 남긴다 — 호출하는 쪽이 키를 그대로 읽는다."""
    counts: dict[str, int] = {engine: 0 for engine in ENGINE_NAMES}
    for scene in scenes:
        counts[scene.engine] = counts.get(scene.engine, 0) + 1
    return counts


def engine_summary(scenes: list[Scene]) -> str:
    """로그 한 줄용. 실제로 쓰인 엔진만 보여준다."""
    used = [(name, count) for name, count in engine_counts(scenes).items() if count]
    return " / ".join(f"{name} {count}" for name, count in used) or "없음"


def _next_move(move_index: int, previous: CameraMove | None) -> tuple[CameraMove, int]:
    """직전 컷과 다른 무빙을 고른다. 순환만 하면 훅(push-in) 다음에 또 push-in 이 온다."""
    for offset in range(len(MOVE_CYCLE)):
        candidate = MOVE_CYCLE[(move_index + offset) % len(MOVE_CYCLE)]
        if candidate != previous:
            return candidate, move_index + offset + 1
    return MOVE_CYCLE[move_index % len(MOVE_CYCLE)], move_index + 1


def _next_chart_step(current: int, line, series_labels: list[str]) -> int:
    """차트는 지금 말하고 있는 항목까지 드러나야 한다.

    한 항목씩 순서대로 여는 게 기본이지만, 내레이션이 뒤쪽 항목을 먼저 부르면
    그 항목까지 한 번에 연다. 말하는 항목이 화면에 없는 상태가 가장 나쁘다.
    """
    haystack = line.text + " " + " ".join(line.emphasis)
    mentioned = [
        index for index, label in enumerate(series_labels) if label and label in haystack
    ]
    target = max(mentioned) + 1 if mentioned else current + 1
    return min(max(current + 1, target), len(series_labels))
