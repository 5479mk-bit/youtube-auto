"""문장을 컷으로 쪼개고, 각 컷을 무엇으로 움직일지 정한다.

컷 배치의 두 원칙:
- 연속한 컷은 절대 같은 방향 무빙을 쓰지 않는다. 정지 사진의 지루함은 무빙의 반복에서 온다.
- 도입부는 통째로 Veo 클립 하나로 덮는다. Veo 는 8초 단위로 과금되므로 3초만 쓰고 5초를
  버리면 돈을 버리는 것과 같다. 시청 이탈이 결정되는 구간이기도 해서 여기에 몰아준다.
"""

from __future__ import annotations

import math

from ..models import CameraMove, MotionEngine, Scene, ScriptPart

MOVE_CYCLE: list[CameraMove] = ["push_in", "pan_lr", "pull_out", "pan_rl", "push_in", "pan_tb"]
MIN_CUT_DURATION = 0.5


def plan_scenes(
    part: ScriptPart,
    cut_duration_max: float,
    freeze_last: bool,
    series_labels: list[str],
    veo_seconds: float = 0.0,
) -> list[Scene]:
    """veo_seconds 만큼의 도입부를 Veo 씬 하나로 묶고, 나머지를 컷으로 쪼갠다."""
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

        # 도입부 구간에 걸치는 문장은 쪼개지 않고 Veo 씬에 흡수시킨다.
        if cursor < veo_seconds:
            take = min(line_duration, max(veo_seconds - cursor, 0.0))
            if scenes and scenes[0].engine == "veo":
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
                        engine="veo",
                        show_chart=line.show_chart,
                        chart_step=chart_step if line.show_chart else 0,
                        is_hook=True,
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

    if freeze_last and scenes and scenes[-1].engine != "veo":
        scenes[-1].camera = "freeze"

    return scenes


def engine_counts(scenes: list[Scene]) -> dict[MotionEngine, int]:
    counts: dict[MotionEngine, int] = {"veo": 0, "parallax": 0}
    for scene in scenes:
        counts[scene.engine] += 1
    return counts


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
