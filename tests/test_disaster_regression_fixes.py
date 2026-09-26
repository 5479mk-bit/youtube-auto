"""run #23/#24 결과물을 보고 사용자가 지적한 문제들에 대한 회귀 테스트.

1. TTS 가 edge(기계음)로 떨어짐 → gemini 로 고쳤는데도 여전히 "다큐 성우" 톤에
   못 미친다는 지적 → cloud_tts 를 1순위로, 품질 엔진 전멸 시 무음/기계음 대신
   TTSFailure 로 하드 스톱.
2. 화면이 정적/칙칙함 → 다크 그레이딩 제거로도 "여전히 정지 사진 같다"는 지적 →
   평평한 줌 대신 사진 컷과 같은 깊이 기반 파랄랙스(motion.parallax)로 교체.
3. 3중 후킹 무시 — _parse() 가 문장 수를 검증하지 않아 얇은 대본도 그대로 통과시키던 버그.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.models import Scene, StoryCandidate
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


def test_build_tts_engines_excludes_edge_by_default():
    # require_quality_voice 기본값(true)에서는 edge(기계음)가 체인에 아예 없어야
    # 한다 — 품질 엔진이 전부 실패하면 무음/기계음이 아니라 TTSFailure 로 멈춘다.
    from src import pipeline
    from src.settings import load_settings

    settings = load_settings()
    with patch.object(pipeline, "build_vertex", return_value=(None, None)):
        engines = pipeline.build_tts_engines(settings)
    names = [engine.name for engine in engines]
    assert "edge" not in names, f"기본 체인에 edge 가 없어야 합니다 (실제: {names})"
    assert names.index("cloud_tts") < names.index("gemini"), (
        f"cloud_tts 가 gemini 보다 먼저 시도돼야 합니다 (실제 순서: {names})"
    )


def test_build_tts_engines_includes_edge_when_quality_not_required():
    from src import pipeline
    from src.settings import Settings, load_settings

    base = load_settings()
    raw = dict(base.raw)
    raw["audio"] = {**raw["audio"], "require_quality_voice": False}
    settings = Settings(raw, base.secrets)
    with patch.object(pipeline, "build_vertex", return_value=(None, None)):
        engines = pipeline.build_tts_engines(settings)
    assert "edge" in [engine.name for engine in engines]


def test_synthesize_part_raises_when_all_quality_engines_fail(tmp_path):
    from src.models import ScriptLine, ScriptPart
    from src.narration.tts_engine import TTSFailure, synthesize_part

    failing_engine = MagicMock()
    failing_engine.name = "cloud_tts"
    failing_engine.enabled = True
    failing_engine.synthesize.return_value = False
    failing_engine.supports_batch = False

    part = ScriptPart(
        part=1,
        hook_number="훅",
        lines=[ScriptLine(text="문장", visual_query_ko="", visual_query_en="")],
    )
    with pytest.raises(TTSFailure):
        synthesize_part(part, [failing_engine], tmp_path, require_quality=True)


def test_synthesize_part_falls_back_to_silence_when_quality_not_required(tmp_path):
    from src.models import ScriptLine, ScriptPart
    from src.narration.tts_engine import synthesize_part

    part = ScriptPart(
        part=1,
        hook_number="훅",
        lines=[ScriptLine(text="문장", visual_query_ko="", visual_query_en="")],
    )
    result = synthesize_part(part, [], tmp_path, require_quality=False)
    assert result.lines[0].audio_path


def test_veo_line_is_padded_to_minimum_duration(tmp_path):
    # VEO 문장이 대본 규칙(15~20자, 약 3~4초)대로 짧게 나오면, scene_planner 가
    # 그 문장 길이를 그대로 씬 길이로 물려받아 Veo 가 실제로 만든 8초짜리 영상도
    # 그만큼만 잘려 쓰인다 — "맨처음+중간 2번은 5초 이상 움직여야" 피드백의 원인.
    # min_veo_line_duration 만큼 무음을 더해 문장 자체의 길이를 늘려야 한다.
    from src.models import ScriptLine, ScriptPart
    from src.narration.tts_engine import synthesize_part

    engine = MagicMock()
    engine.name = "test"
    engine.enabled = True
    engine.supports_batch = False

    def fake_synthesize(text, rate, out_path):
        import subprocess

        import imageio_ffmpeg

        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1.0",
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
        return True

    engine.synthesize.side_effect = fake_synthesize

    part = ScriptPart(
        part=1,
        hook_number="훅",
        lines=[
            ScriptLine(
                text="문장", visual_query_ko="", visual_query_en="", scene_type="VEO"
            )
        ],
    )
    synthesize_part(part, [engine], tmp_path, min_veo_line_duration=5.5)
    assert part.lines[0].duration >= 5.5


def test_non_veo_line_is_not_padded(tmp_path):
    # min_veo_line_duration 은 VEO 문장에만 적용된다 — AI_IMAGE 등 다른 문장까지
    # 늘리면 전체 길이(30~40초 목표)가 불필요하게 늘어난다.
    from src.models import ScriptLine, ScriptPart
    from src.narration.tts_engine import synthesize_part

    engine = MagicMock()
    engine.name = "test"
    engine.enabled = True
    engine.supports_batch = False

    def fake_synthesize(text, rate, out_path):
        import subprocess

        import imageio_ffmpeg

        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1.0",
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
        return True

    engine.synthesize.side_effect = fake_synthesize

    part = ScriptPart(
        part=1,
        hook_number="훅",
        lines=[
            ScriptLine(
                text="문장", visual_query_ko="", visual_query_en="", scene_type="AI_IMAGE"
            )
        ],
    )
    synthesize_part(part, [engine], tmp_path, min_veo_line_duration=5.5)
    assert part.lines[0].duration < 5.5


def test_trim_and_pad_output_is_lossless_wav(tmp_path):
    # 예전엔 이 단계가 mp3 로 다시 인코딩해 문장마다 수십 ms 씩 길이 오차가 쌓였다
    # (부 후반부로 갈수록 자막이 밀리는 원인). 이제는 무손실 WAV 여야 한다.
    import subprocess

    import imageio_ffmpeg

    from src.narration.tts_engine import _trim_and_pad

    src_path = tmp_path / "line.mp3"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.0",
            str(src_path),
        ],
        check=True,
        capture_output=True,
    )
    final_path = _trim_and_pad(src_path, pause=0.25)
    assert final_path.suffix == ".wav"
    assert final_path.exists()
    assert not src_path.exists()


def test_ai_image_scene_uses_depth_parallax_not_flat_zoom():
    # 평평한 줌(render_ai_image_motion)으로는 "화면이 정지 사진 같다"는 지적이
    # 안 풀렸다 — 사진 컷과 동일한 깊이 기반 파랄랙스를 타야 한다.
    from src.render import motion_sources

    scene = Scene(
        start=0.0,
        duration=3.0,
        image_query_ko="",
        image_query_en="",
        camera="push_in",
        engine="ai_image",
        scene_type="AI_IMAGE",
        prompt="a collapsing structure",
    )
    ai_client = MagicMock()
    ai_client.generate.return_value = "/tmp/fake.png"

    with patch.object(motion_sources, "make_parallax_clip") as mock_parallax:
        mock_parallax.return_value = "clip"
        clip, _credit = motion_sources._build_generated_clip(
            scene, veo_client=None, ai_client=ai_client, size=(1080, 1920), settings=None
        )

    assert clip == "clip"
    mock_parallax.assert_called_once()
    args, kwargs = mock_parallax.call_args
    assert args[0] == "/tmp/fake.png"
    assert args[1] == scene.duration
