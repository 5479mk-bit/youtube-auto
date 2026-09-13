"""문장을 컷으로 쪼개고 카메라 무빙을 배정한다.

정지 사진으로 만든 영상이 지루해지는 이유는 무빙이 없어서가 아니라 같은 무빙이
반복되기 때문이다. 그래서 연속한 컷은 절대 같은 방향을 쓰지 않는다. 훅은 항상
느린 push-in 이고, 1부의 마지막 컷은 freeze 로 멈춰 클리프행어를 만든다.
"""

from __future__ import annotations

import math

from ..models import CameraMove, Scene, ScriptPart

MOVE_CYCLE: list[CameraMove] = ["push_in", "pan_lr", "pull_out", "pan_rl", "push_in", "pan_tb"]
MIN_CUT_DURATION = 0.5


def plan_scenes(
    part: ScriptPart,
    cut_duration_max: float,
    freeze_last: bool,
    series_labels: list[str],
) -> list[Scene]:
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
        cut_count = max(1, math.ceil(line_duration / cut_duration_max))
        cut_duration = line_duration / cut_count

        for cut in range(cut_count):
            is_hook = line_index == 0 and cut == 0
            if is_hook:
                camera: CameraMove = "push_in"
            else:
                camera, move_index = _next_move(move_index, scenes[-1].camera if scenes else None)

            scenes.append(
                Scene(
                    start=cursor,
                    duration=cut_duration,
                    image_query=line.visual_query,
                    camera=camera,
                    show_chart=line.show_chart,
                    chart_step=chart_step if line.show_chart else 0,
                    is_hook=is_hook,
                )
            )
            cursor += cut_duration

    if freeze_last and scenes:
        scenes[-1].camera = "freeze"

    return scenes


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
    (템플릿 대본의 '중간에 놓인 편의점' 같은 문장) 그 항목까지 한 번에 연다.
    말하는 항목이 화면에 없는 상태가 가장 나쁘다.
    """
    haystack = line.text + " " + " ".join(line.emphasis)
    mentioned = [
        index for index, label in enumerate(series_labels) if label and label in haystack
    ]
    target = max(mentioned) + 1 if mentioned else current + 1
    return min(max(current + 1, target), len(series_labels))
