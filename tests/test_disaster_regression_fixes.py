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
    # 사용자가 청취 비교로 Gemini 2.5 Pro TTS 를 골랐다 — gemini 가 맨 앞이고,
    # CI 에서 먼저 성공해버릴 수 있는 supertonic 은 체인에 없어야 한다.
    assert names[0] == "gemini", f"gemini 가 1순위여야 합니다 (실제 순서: {names})"
    assert "supertonic" not in names


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


def test_pad_to_duration_freezes_last_frame_instead_of_stretching_metadata():
    # Veo 가 반올림 오차로 요청보다 몇십 ms 짧게 돌려줘도, with_duration()으로 길이
    # 메타데이터만 늘리면 존재하지 않는 프레임을 읽으려다 깨질 수 있다 — 마지막
    # 프레임을 진짜로 이어붙여 항상 실제 프레임이 있게 해야 한다("8초 만들었으면
    # 8초 다 써야지" 피드백에 맞춰 씬 길이를 생성 길이에 바짝 맞췄기 때문에 이제
    # 이 경로가 실제로 자주 탄다).
    from moviepy import ImageClip

    from src.render.motion_sources import _pad_to_duration

    short_clip = ImageClip(np.zeros((10, 10, 3), dtype=np.uint8)).with_duration(1.0)
    padded = _pad_to_duration(short_clip, target_duration=1.5)
    assert padded.duration == pytest.approx(1.5, abs=0.02)


def test_pad_to_duration_is_noop_when_already_long_enough():
    from moviepy import ImageClip

    from src.render.motion_sources import _pad_to_duration

    clip = ImageClip(np.zeros((10, 10, 3), dtype=np.uint8)).with_duration(2.0)
    result = _pad_to_duration(clip, target_duration=1.0)
    assert result is clip


def test_veo_scene_default_floor_matches_full_generated_duration():
    # config 기본값(veo.duration_seconds=8)과 min_scene_seconds 가 같은 값이어야
    # "실제로 만든 8초를 다 쓴다"는 요구가 지켜진다.
    from src.settings import load_settings

    settings = load_settings()
    assert settings.get("veo.min_scene_seconds", None) == settings.get(
        "veo.duration_seconds", None
    )


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


def test_unrecognized_sfx_tag_falls_back_to_sub_boom_not_silence():
    # LLM 이 프롬프트에 없는 이름(impact/explosion 등)을 쓰면 예전엔 "none"으로
    # 떨어져 효과음이 조용히 사라졌다 — 이제는 가장 범용적인 타격음으로 대체한다.
    from src.script.script_writer import _sfx

    assert _sfx("impact") == "sub_boom"
    assert _sfx("explosion") == "sub_boom"
    assert _sfx("water_rushing") == "whoosh"
    assert _sfx("완전히-모르는-태그") == "sub_boom"
    assert _sfx("none") == "none"
    assert _sfx(None) == "none"
    assert _sfx("sub_boom") == "sub_boom"


def test_veo_line_defaults_to_sub_boom_when_llm_omits_sfx():
    # VEO 컷은 편당 4개뿐인 가장 센 장면이다 — LLM 이 sfx 를 깜빡 비워도 가장 중요한
    # 컷은 항상 뭔가 터지게 한다.
    from src.script.script_writer import _parse_line

    candidate = _disaster_candidate()
    raw = {"text": "문장", "scene_type": "VEO", "prompt": "p", "sfx": "none"}
    line = _parse_line(raw, candidate)
    assert line.sfx == "sub_boom"


def test_ai_image_line_keeps_none_sfx_when_llm_omits_it():
    from src.script.script_writer import _parse_line

    candidate = _disaster_candidate()
    raw = {"text": "문장", "scene_type": "AI_IMAGE", "prompt": "p", "sfx": "none"}
    line = _parse_line(raw, candidate)
    assert line.sfx == "none"


def test_sfx_layer_falls_back_to_pool_when_tag_unmapped(tmp_path):
    # 매핑된 효과음 태그가 실제 풀에 없으면(에셋 다운로드 누락 등) 조용히 스킵하는
    # 대신 기본 임팩트음 풀(sub_boom)에서 대체한다.
    from src import assets
    from src.render import video_builder

    scene = Scene(
        start=0.0,
        duration=2.0,
        image_query_ko="",
        image_query_en="",
        camera="push_in",
        engine="veo",
        sfx="does_not_exist_tag",
    )
    settings = MagicMock()
    settings.get.return_value = -4

    layers = video_builder._sfx_layers([scene], settings, total=5.0)
    # 실제 sub_boom 풀 파일이 폴백으로 쓰였는지 확인한다 — 에셋이 실제로
    # 존재하는 한 완전히 비어 있으면 안 된다.
    if any(path.exists() for path in assets.SFX_POOLS.get("sub_boom", [])):
        assert len(layers) == 1


def test_sfx_pools_registered_for_all_tags():
    # "효과음이 sub_boom/braam 뿐이라 밋밋하다"는 지적에 맞춰 늘린 갈래(실제
    # 라이선스 SFX, Mixkit)가 전부 풀로 연결돼 있는지 확인한다.
    from src import assets

    for tag in ("sub_boom", "crack", "whoosh", "alarm", "braam"):
        assert tag in assets.SFX_POOLS
        assert len(assets.SFX_POOLS[tag]) == 2


def test_pick_sfx_path_cycles_through_pool_before_repeating():
    # "10개 다 쓰라고 적절하게" — 같은 태그가 여러 번 나오면 무작위가 아니라
    # 순서대로 돌려써서 풀에 있는 파일이 전부 최소 한 번씩은 쓰이게 한다.
    from src import assets
    from src.render.video_builder import _pick_sfx_path

    if not all(path.exists() for path in assets.SFX_POOLS["sub_boom"]):
        pytest.skip("SFX 에셋이 아직 다운로드되지 않았습니다 (./download_assets.sh)")
    used_index: dict[str, int] = {}
    picks = [_pick_sfx_path("sub_boom", used_index) for _ in range(4)]
    assert set(picks) == set(assets.SFX_POOLS["sub_boom"])
    # 짝수 번째 호출은 첫 번째 파일로 돌아와야 한다(순환).
    assert picks[0] == picks[2]
    assert picks[1] == picks[3]


def test_short_veo_line_is_rejected_to_force_template_fallback():
    # VEO 컷은 실제로 8초짜리 영상이 통째로 생성되는데, 문장이 짧으면 대사가 몇 초
    # 만에 끝나고 나머지는 빈 화면만 흘러간다("8초 동안 대사가 너무 짧다"는 지적의
    # 원인) — 너무 짧은 VEO 문장은 통과시키지 않는다.
    def line(i: int, scene_type: str = "AI_IMAGE", text: str | None = None) -> dict:
        return {
            "text": text or f"문장 {i}가 여기 들어간다 충분히 길게",
            "scene_type": scene_type,
            "prompt": "p",
        }

    raw = {
        "title_seed": "제목",
        "part1": {
            "hook_number": "훅1",
            "lines": [
                line(0, "VEO", text="야, 짧아"),
                *[line(i) for i in range(1, 9)],
            ],
        },
        "part2": {"hook_number": "훅2", "lines": [line(i) for i in range(9)]},
    }
    assert _parse(raw, _disaster_candidate()) is None


def test_long_enough_veo_line_is_accepted():
    def line(i: int, scene_type: str = "AI_IMAGE", text: str | None = None) -> dict:
        return {
            "text": text or f"문장 {i}가 여기 들어간다 충분히 길게",
            "scene_type": scene_type,
            "prompt": "p",
        }

    raw = {
        "title_seed": "제목",
        "part1": {
            "hook_number": "훅1",
            "lines": [
                line(
                    0,
                    "VEO",
                    text="야, 이거 진짜 미쳤던 게, 아무도 그렇게 될 줄 몰랐던 일이었어",
                ),
                *[line(i) for i in range(1, 9)],
            ],
        },
        "part2": {"hook_number": "훅2", "lines": [line(i) for i in range(9)]},
    }
    assert _parse(raw, _disaster_candidate()) is not None


def test_split_for_tts_breaks_long_line_into_clauses():
    # 로컬 TTS(diffusion 기반)가 긴 문장을 한 번에 읽으면 억양이 깨진다는 지적이
    # 있었다 — 쉼표 기준으로 절을 나눠야 한다.
    from src.narration.tts_engine import _split_for_tts

    text = "야, 타이타닉호 알아? 인류 역사상 최악의 해상 사고 중 하나인데, 1,514명이나 죽었어."
    chunks = _split_for_tts(text, max_chars=35)
    assert len(chunks) > 1
    assert all(len(chunk) <= 35 for chunk in chunks)
    # 절을 이어붙이면(쉼표 복원 없이) 원문 내용이 전부 보존돼야 한다.
    assert any("타이타닉호" in chunk for chunk in chunks)


def test_short_line_is_not_chunked():
    from src.narration.tts_engine import _split_for_tts

    text = "짧은 문장이야."
    assert _split_for_tts(text, max_chars=35) == [text]


def test_synthesize_chunked_concatenates_pieces(tmp_path):
    import subprocess

    import imageio_ffmpeg

    from src.narration.tts_engine import _synthesize_chunked

    engine = MagicMock()

    def fake_synthesize(text, rate, out_path):
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.5",
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
        return True

    engine.synthesize.side_effect = fake_synthesize

    text = "야, 타이타닉호 알아? 인류 역사상 최악의 해상 사고 중 하나인데, 1,514명이나 죽었어."
    out_path = tmp_path / "line.wav"
    ok = _synthesize_chunked(engine, text, "+10%", out_path)
    assert ok
    assert out_path.exists()
    assert engine.synthesize.call_count > 1
    # 절 개수만큼 이어붙였으니 최종 길이가 절 하나(0.5초)보다는 길어야 한다.
    from src.narration.tts_engine import _measure_duration

    assert _measure_duration(out_path) > 0.6


def test_short_interjection_clause_is_merged_not_isolated():
    # "야," 처럼 한두 글자짜리 절이 혼자 떨어지면 TTS 가 맥락 없이 그 짧은 말만
    # 합성하다 "애~" 처럼 부자연스럽게 늘어진다는 지적이 있었다 — 짧은 절은
    # 옆 절에 붙어야 한다.
    from src.narration.tts_engine import _split_for_tts

    chunks = _split_for_tts("야, 타이타닉호 알아? 진짜 미쳤던 일이었어.", max_chars=35)
    assert all(len(chunk) >= 8 for chunk in chunks)
    assert chunks[0].startswith("야")


def test_hour_reads_as_native_korean_not_sino_korean():
    # "2시"는 "이시"가 아니라 "두시"라고 읽어야 자연스럽다 — 시각의 시(時)는
    # 순우리말 수를 쓴다.
    from src.text_ko import read_aloud

    assert read_aloud("새벽 2시 20분") == "새벽 두시 이십분"
    assert read_aloud("오후 3시에") == "오후 세시에"
    assert read_aloud("12시 정각") == "열두시 정각"


def test_minute_and_headcount_still_use_sino_korean():
    # 시(時)만 예외고, 분·명 같은 다른 단위는 그대로 한자어 수를 써야 한다.
    from src.text_ko import read_aloud

    assert read_aloud("20분") == "이십분"
    assert read_aloud("1,514명") == "천오백십사명"
