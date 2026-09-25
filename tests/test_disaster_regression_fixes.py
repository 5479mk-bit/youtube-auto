"""사용자가 실제 렌더 결과물(run #23)을 보고 지적한 세 가지 문제에 대한 회귀 테스트.

1. TTS 가 edge(기계음)로 떨어짐 — build_tts_engines() 기본 체인이 gemini 를 건너뛰던 버그.
2. 화면이 정적/칙칙함 — render_ai_image_motion() 이 조선시대/CCTV 시절 다크 그레이딩을
   3D 엔지니어링 단면도 콘셉트에도 그대로 강제하던 버그.
3. 3중 후킹 무시 — _parse() 가 문장 수를 검증하지 않아 얇은 대본도 그대로 통과시키던 버그.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

from src.models import StoryCandidate
from src.render.scene_renderers import render_ai_image_motion
from src.script.script_writer import _parse


def _disaster_candidate() -> StoryCandidate:
    return StoryCandidate(
        dataset="disaster",
        topic_key="disaster:test",
        headline="테스트 재난",
        headline_number="",
        subject="테스트 재난",
        context="본문",
        source_name="위키백과",
    )


def _raw_script(part1_lines: int, part2_lines: int) -> dict:
    def line(i: int) -> dict:
        return {"text": f"문장 {i}", "scene_type": "AI_IMAGE", "prompt": "p"}

    return {
        "title_seed": "제목",
        "part1": {"hook_number": "훅1", "lines": [line(i) for i in range(part1_lines)]},
        "part2": {"hook_number": "훅2", "lines": [line(i) for i in range(part2_lines)]},
    }


def test_thin_disaster_script_is_rejected_to_force_template_fallback():
    # 3중 후킹 구조([오프닝]-[중반 반전]-[클라이맥스])가 들어갈 자리가 없는 대본은
    # 파싱에는 성공하더라도 통과시키지 않는다(--preview-seconds 로 잘린 게 아니라
    # LLM 이 직접 짧게 내놓은 경우를 시뮬레이션).
    raw = _raw_script(part1_lines=3, part2_lines=3)
    assert _parse(raw, _disaster_candidate()) is None


def test_full_length_disaster_script_is_accepted():
    raw = _raw_script(part1_lines=10, part2_lines=10)
    script = _parse(raw, _disaster_candidate())
    assert script is not None
    assert len(script.part1.lines) == 10
    assert len(script.part2.lines) == 10


def test_heritage_script_is_not_subject_to_disaster_line_minimum():
    # 이 최소 문장 수 검증은 disaster 데이터셋 전용이다 — 다른 데이터셋까지 막으면
    # 과도한 검증이다.
    candidate = StoryCandidate(
        dataset="heritage",
        topic_key="heritage:test",
        headline="테스트 문화재",
        headline_number="국보 1호",
        subject="테스트 문화재",
        context="본문",
        source_name="국가유산청",
    )
    raw = _raw_script(part1_lines=2, part2_lines=2)
    assert _parse(raw, candidate) is not None


def test_render_ai_image_motion_defaults_apply_no_dark_grading(tmp_path):
    # 조선시대/CCTV 콘셉트의 하드코딩된 "채도 30%p 감소 + 비네팅 + 밤안개"가 기본값으로
    # 3D 단면도 콘셉트에도 강제되던 게 "화면이 정적/칙칙하다" 지적의 실제 원인이었다.
    # 기본 호출은 이제 그 어떤 그레이딩도 걸지 않아야 한다.
    from PIL import Image

    image_path = tmp_path / "sample.png"
    Image.new("RGB", (64, 64), (200, 50, 50)).save(image_path)

    clip = render_ai_image_motion(image_path, duration=1.0, size=(64, 64))
    first_frame = clip.get_frame(0.0)
    # 원본이 순색이므로, 그레이딩이 안 걸렸다면 프레임 중앙 픽셀이 원본과 거의 같아야
    # 한다(리사이즈로 인한 미세한 보간 오차만 허용).
    center = first_frame[32, 32]
    assert abs(int(center[0]) - 200) < 5
    assert abs(int(center[1]) - 50) < 5
    assert abs(int(center[2]) - 50) < 5


def test_build_tts_engines_prefers_gemini_over_edge_by_default():
    from src import pipeline
    from src.settings import load_settings

    settings = load_settings()
    with patch.object(pipeline, "build_vertex", return_value=(None, None)):
        engines = pipeline.build_tts_engines(settings)
    names = [engine.name for engine in engines]
    assert names.index("gemini") < names.index("edge"), (
        f"gemini 가 edge 보다 먼저 시도돼야 합니다 (실제 순서: {names})"
    )
