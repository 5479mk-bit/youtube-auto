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
    assert names == ["gemini"], f"gemini(2번 목소리) 하나만 써야 합니다 (실제: {names})"


def test_build_tts_engines_includes_edge_when_quality_not_required():
    from src import pipeline
    from src.settings import Settings, load_settings

    base = load_settings()
    raw = dict(base.raw)
    # edge 폴백은 gemini 고정(2번 목소리)이 아닌 엔진 설정에서만 의미가 있다.
    raw["audio"] = {**raw["audio"], "engine": "cloud_tts", "require_quality_voice": False}
    settings = Settings(raw, base.secrets)
    with patch.object(pipeline, "build_vertex", return_value=(None, None)):
        engines = pipeline.build_tts_engines(settings)
    assert "edge" in [engine.name for engine in engines]


def test_gemini_engine_never_falls_back_even_if_quality_not_required():
    # "2번 tts 외에는 아무것도 tts 하지마" — gemini 로 고정했으면 edge 도 끼면 안 된다.
    from src import pipeline
    from src.settings import Settings, load_settings

    base = load_settings()
    raw = dict(base.raw)
    raw["audio"] = {**raw["audio"], "engine": "gemini", "require_quality_voice": False}
    settings = Settings(raw, base.secrets)
    with patch.object(pipeline, "build_vertex", return_value=(None, None)):
        engines = pipeline.build_tts_engines(settings)
    assert [engine.name for engine in engines] == ["gemini"]


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

    layers, duck_windows = video_builder._sfx_layers([scene], settings, total=5.0)
    # 실제 sub_boom 풀 파일이 폴백으로 쓰였는지 확인한다 — 에셋이 실제로
    # 존재하는 한 완전히 비어 있으면 안 된다.
    if any(path.exists() for path in assets.SFX_POOLS.get("sub_boom", [])):
        assert len(layers) == 1
        assert len(duck_windows) == 1


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


# --- 레퍼런스(건축사전) 스타일 반영 ---------------------------------------------


def test_overlay_kept_only_when_in_sentence():
    from src.script.script_writer import _overlay

    assert _overlay("98m", "해발 98m짜리 봉우리가 서 있습니다") == "98m"
    # 문장에 없는 숫자는 화면에 띄우지 않는다.
    assert _overlay("120m", "해발 98m짜리 봉우리가 서 있습니다") == ""
    assert _overlay("", "아무 문장") == ""
    assert _overlay("아주아주긴오버레이문구", "아주아주긴오버레이문구입니다") == ""


def test_disaster_prompt_is_formal_tone():
    # 스페인어 전환(2026-09-30) 이후에도 옛 한국어 프롬프트는 폐기가 아니라
    # 설정으로 빠진 것뿐이다 — 되돌릴 수 있게 그 내용은 그대로 유지돼야 한다.
    from src.script.script_writer import GLOBAL_DISASTER_PROMPT_KO_UNUSED

    assert "합니다체" in GLOBAL_DISASTER_PROMPT_KO_UNUSED
    assert "반말 금지" in GLOBAL_DISASTER_PROMPT_KO_UNUSED
    assert '"overlay"' in GLOBAL_DISASTER_PROMPT_KO_UNUSED


def test_disaster_prompt_is_spanish_and_formal():
    from src.script.script_writer import GLOBAL_DISASTER_PROMPT

    assert "en español" in GLOBAL_DISASTER_PROMPT
    assert "tono informal" in GLOBAL_DISASTER_PROMPT
    assert '"overlay"' in GLOBAL_DISASTER_PROMPT
    # 나레이션 문장은 스페인어지만, 이미지/영상 생성 프롬프트는 그대로 영어여야
    # 한다(모델이 영어만 이해한다) — 스타일 문구가 안 깨졌는지 확인.
    assert "photoreal engineering explainer" in GLOBAL_DISASTER_PROMPT


def test_disaster_prompt_has_mystery_hook_and_high_contrast_destruction_style():
    # 사용자 지시(2026-09-30): 더 자극적으로 — 실제 자료에 있을 때만 미스터리/
    # 은폐 앵글을 쓰라는 가드레일과, 붉은빛·고대비 파괴 연출 스타일이 프롬프트에
    # 실제로 들어갔는지 확인한다.
    from src.script.script_writer import GLOBAL_DISASTER_PROMPT

    assert "no lo inventes" in GLOBAL_DISASTER_PROMPT  # 없는 음모론은 지어내지 않는다
    assert "glowing deep red light" in GLOBAL_DISASTER_PROMPT
    assert "high-contrast red and black" in GLOBAL_DISASTER_PROMPT
    assert "disintegrating into dust" in GLOBAL_DISASTER_PROMPT


def test_center_overlay_renders(tmp_path):
    from PIL import Image

    from src.render import overlays

    path = overlays.render_center_overlay("1978", tmp_path / "o.png", (1080, 1920))
    image = Image.open(path)
    assert image.size == (1080, 1920)
    assert image.getbbox() is not None


def test_sfx_none_scenes_stay_silent(monkeypatch, tmp_path):
    """"효과음이 난잡하다"는 지적 이후: sfx="none" 컷에는 자동으로 아무 소리도
    안 붙는다 — 대본이 명시한 것만 쓴다(cut_whoosh 자동 채움 제거)."""
    from src.models import Scene
    from src.render import video_builder

    picked = []

    def fake_pick(tag, used_index):
        picked.append(tag)
        return None

    monkeypatch.setattr(video_builder, "_pick_sfx_path", fake_pick)

    class S:
        def get(self, key, default=None):
            return default

    scenes = [
        Scene(start=0.0, duration=3, image_query_ko="a", image_query_en="a", camera="push_in", sfx="none"),
        Scene(start=3.0, duration=3, image_query_ko="a", image_query_en="a", camera="push_in", sfx="none"),
        Scene(start=6.0, duration=3, image_query_ko="a", image_query_en="a", camera="push_in", sfx="crack"),
    ]
    video_builder._sfx_layers(scenes, S(), 9.0)
    # "none" 컷 두 개는 아예 건너뛰고, 대본이 준 crack 하나만 시도된다(파일이 없어
    # 안전장치로 sub_boom 폴백까지 한 번 더 시도되지만, "none" 컷에서는 아예 호출
    # 자체가 없다).
    assert picked == ["crack", "sub_boom"]


# --- 합본 렌더(--combine-parts): 1·2부를 나누지 않고 영상 하나로 ---------------------


def _make_script_part(part_num: int, hook: str, texts: list[str]):
    from src.models import ScriptLine, ScriptPart

    return ScriptPart(
        part=part_num,
        hook_number=hook,
        lines=[ScriptLine(text=t, visual_query_ko="a", duration=2.0) for t in texts],
    )


def test_merge_parts_concatenates_lines_and_keeps_part1_hook():
    from src.models import Script
    from src.pipeline import _merge_parts

    script = Script(
        topic_key="k",
        title_seed="제목",
        part1=_make_script_part(1, "훅1", ["문장1", "문장2"]),
        part2=_make_script_part(2, "훅2", ["문장3", "문장4"]),
    )
    merged = _merge_parts(script)
    assert merged.part == 1
    assert merged.hook_number == "훅1"
    assert [line.text for line in merged.lines] == ["문장1", "문장2", "문장3", "문장4"]
    assert merged.duration == pytest.approx(8.0)


def test_warn_if_off_target_uses_combined_bounds(monkeypatch):
    from src import pipeline

    part = _make_script_part(1, "훅", ["a"] * 20)  # 20 * 2.0초 = 40초

    class S:
        def get(self, key, default=None):
            return default

    warnings = []
    monkeypatch.setattr(pipeline.logger, "warning", lambda msg: warnings.append(msg))

    pipeline._warn_if_off_target(S(), part, duration_bounds=(63, 999))
    assert any("40.0초" in msg and "63" in msg for msg in warnings)

    warnings.clear()
    pipeline._warn_if_off_target(S(), part, duration_bounds=(30, 999))
    assert not warnings  # 40초는 (30, 999) 구간 안이라 경고 없음


def test_seo_metadata_combined_skips_part_suffix():
    from src.models import Script, StoryCandidate
    from src.publish import seo_metadata

    script = Script(
        topic_key="k",
        title_seed="제목",
        part1=_make_script_part(1, "훅", ["문장"]),
        part2=_make_script_part(2, "훅2", ["문장2"]),
    )
    candidate = StoryCandidate(
        dataset="disaster",
        topic_key="disaster:x",
        headline="사건",
        headline_number="",
        subject="사건",
        context="",
        source_name="위키백과",
    )
    metadata = seo_metadata.generate(script, script.part1, candidate, None, [], is_combined=True)
    assert "부)" not in metadata.title
    metadata_split = seo_metadata.generate(script, script.part1, candidate, None, [])
    assert "(1부)" in metadata_split.title


def test_seo_metadata_hook_description_excludes_source_and_hashtags():
    # 사용자 지시: 텔레그램 검수 카드에는 출처·해시태그가 안 섞인 순수 후킹 설명만
    # 보여준다 — description(유튜브 업로드용)에는 그대로 붙어 있어야 한다.
    from src.models import Script, StoryCandidate
    from src.publish import seo_metadata

    script = Script(
        topic_key="k", title_seed="제목",
        part1=_make_script_part(1, "훅", ["문장"]),
        part2=_make_script_part(2, "훅2", ["문장2"]),
    )
    candidate = StoryCandidate(
        dataset="disaster", topic_key="disaster:x", headline="사건",
        headline_number="", subject="사건", context="", source_name="위키백과",
    )
    metadata = seo_metadata.generate(script, script.part1, candidate, None, [], is_combined=True)
    assert metadata.hook_description
    assert "Fuente" not in metadata.hook_description
    assert "#" not in metadata.hook_description
    # disaster 데이터셋은 채널 언어 전환(2026-09-30)으로 출처 표기도 스페인어다.
    assert metadata.source_line == "Fuente: 위키백과"
    assert metadata.hook_description in metadata.description
    assert "Fuente: 위키백과" in metadata.description


def test_seo_metadata_disaster_uses_fixed_hashtags_not_llm_tags():
    """사용자 지시(2026-09-27): 해시태그는 LLM 이 고르지 않고 이 5개로 고정한다."""
    from src.models import Script, StoryCandidate
    from src.publish import seo_metadata

    script = Script(
        topic_key="k",
        title_seed="제목",
        part1=_make_script_part(1, "훅", ["문장"]),
        part2=_make_script_part(2, "훅2", ["문장2"]),
    )
    candidate = StoryCandidate(
        dataset="disaster",
        topic_key="disaster:x",
        headline="사건",
        headline_number="",
        subject="사건",
        context="",
        source_name="위키백과",
    )

    class FakeProvider:
        def complete_json(self, prompt, temperature=0.8):
            return {
                "titles": ["LLM 제목1", "LLM 제목2"],
                "description": "후킹 설명",
                "tags": ["엉뚱한태그1", "엉뚱한태그2"],  # 무시되고 고정 5개로 대체돼야 한다
            }

    metadata = seo_metadata.generate(
        script, script.part1, candidate, FakeProvider(), ["기본태그"], is_combined=True
    )
    assert metadata.tags == seo_metadata.DISASTER_HASHTAGS
    assert "엉뚱한태그1" not in metadata.tags
    assert "기본태그" not in metadata.tags
    # 설명 하단에 해시태그 줄로도 노출돼야 한다(유튜브 화면에 보이는 자리).
    assert " ".join(seo_metadata.DISASTER_HASHTAGS) in metadata.description


def test_seo_metadata_extracts_pinned_comment_from_llm():
    # 사용자 지시(2026-09-30): 틱톡/릴스에도 붙여넣을 고정 댓글 후보를 LLM 이 같이
    # 만든다.
    from src.models import Script, StoryCandidate
    from src.publish import seo_metadata

    script = Script(
        topic_key="k", title_seed="제목",
        part1=_make_script_part(1, "훅", ["문장"]),
        part2=_make_script_part(2, "훅2", ["문장2"]),
    )
    candidate = StoryCandidate(
        dataset="disaster", topic_key="disaster:x", headline="사건",
        headline_number="", subject="사건", context="", source_name="위키백과",
    )

    class FakeProvider:
        def complete_json(self, prompt, temperature=0.8):
            return {
                "titles": ["LLM 제목1", "LLM 제목2"],
                "description": "후킹 설명",
                "description_ko": "한국어 번역",
                "pinned_comment": "¿Tú qué habrías hecho? 👇",
                "tags": [],
            }

    metadata = seo_metadata.generate(
        script, script.part1, candidate, FakeProvider(), [], is_combined=True
    )
    assert metadata.pinned_comment == "¿Tú qué habrías hecho? 👇"
    assert metadata.description_ko == "한국어 번역"


def test_seo_metadata_disaster_template_also_uses_fixed_hashtags():
    """LLM 이 없을 때(템플릿 폴백)도 해시태그는 그대로 고정 5개다."""
    from src.models import Script, StoryCandidate
    from src.publish import seo_metadata

    script = Script(
        topic_key="k",
        title_seed="제목",
        part1=_make_script_part(1, "훅", ["문장"]),
        part2=_make_script_part(2, "훅2", ["문장2"]),
    )
    candidate = StoryCandidate(
        dataset="disaster",
        topic_key="disaster:y",
        headline="사건2",
        headline_number="",
        subject="사건2",
        context="",
        source_name="위키백과",
    )
    metadata = seo_metadata.generate(script, script.part1, candidate, None, ["기본태그"])
    assert metadata.tags == seo_metadata.DISASTER_HASHTAGS


def test_seo_metadata_prompt_forbids_channel_branding():
    from src.publish import seo_metadata

    assert "신비한 건축사전" in seo_metadata.PROMPT  # 금지 예시로만 언급돼야 한다
    assert "쓰지 않는다" in seo_metadata.PROMPT
    assert "신비한 건축사전" in seo_metadata.COMBINED_PROMPT


def test_disaster_script_prompt_has_no_channel_branding_leak():
    """title_seed 필드가 채널 브랜드명을 유도하지 않는지 확인한다(스페인어판도
    한국어판과 같은 원칙을 지켜야 한다 — 표현만 스페인어로 바뀐다)."""
    from src.script.script_writer import GLOBAL_DISASTER_PROMPT

    assert '"신비한 건축사전"류' not in GLOBAL_DISASTER_PROMPT
    assert "신비한 건축사전" not in GLOBAL_DISASTER_PROMPT
    assert "marca" in GLOBAL_DISASTER_PROMPT


def test_cap_fact_for_speech_truncates_long_paragraph():
    from src.script.script_writer import _cap_fact_for_speech

    short = "짧은 문장입니다."
    assert _cap_fact_for_speech(short) == short

    long_fact = "첫 문장입니다. " + "그 뒤로 아주 긴 설명이 계속 이어집니다. " * 20
    capped = _cap_fact_for_speech(long_fact)
    assert len(capped) <= 120
    assert capped  # 빈 문자열이 되면 안 된다


def test_disaster_template_script_caps_fact_length():
    from src.models import StoryCandidate
    from src.script.script_writer import _disaster_template_script

    huge_fact = "설명이 이어집니다. " * 40  # 400자 넘는 하나의 "사실"
    candidate = StoryCandidate(
        dataset="disaster",
        topic_key="disaster:y",
        headline="침몰 사고",
        headline_number="",
        subject="침몰 사고",
        context="",
        source_name="위키백과",
        facts=[huge_fact] * 3,
    )
    script = _disaster_template_script(candidate)
    for part in (script.part1, script.part2):
        for line in part.lines:
            assert len(line.text) <= 130  # 120자 상한 + 약간의 여유


# --- 유튜브 업로드 부가 기능: AI 표시·재생목록·관련 영상 (사용자 지시 2026-09-27) ---


def _fake_youtube_service(existing_playlists=None, existing_videos=None):
    """videos/channels/playlists/playlistItems 를 흉내 내는 MagicMock 서비스."""
    from unittest.mock import MagicMock

    service = MagicMock()

    # videos().insert(...).next_chunk() -> (status, response)
    request = MagicMock()
    request.next_chunk.return_value = (None, {"id": "vid_new"})
    service.videos.return_value.insert.return_value = request

    # channels().list(...).execute() -> uploads 재생목록 id
    service.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxxxx"}}}]
    }

    # playlistItems().list(...).execute() -> 기존 영상 목록(관련 영상 후보)
    videos = existing_videos if existing_videos is not None else ["old_vid_1", "old_vid_2"]
    service.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"videoId": v}} for v in videos]
    }

    # playlists().list(...).execute() -> 기존 재생목록 목록
    playlists = existing_playlists if existing_playlists is not None else []
    service.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"id": pid, "snippet": {"title": title}} for pid, title in playlists]
    }
    service.playlists.return_value.insert.return_value.execute.return_value = {"id": "pl_new"}

    return service


def test_upload_sets_synthetic_media_flag(tmp_path):
    from src.models import VideoMetadata
    from src.publish import youtube_uploader

    video_path = tmp_path / "part1.mp4"
    video_path.write_bytes(b"fake video bytes")
    metadata = VideoMetadata(title="제목", title_alternatives=[], description="설명", tags=[])
    service = _fake_youtube_service()

    youtube_uploader.upload(service, video_path, metadata, "private", "25")

    body = service.videos.return_value.insert.call_args.kwargs["body"]
    assert body["status"]["containsSyntheticMedia"] is True


def test_upload_sets_default_language_when_given(tmp_path):
    # 채널 언어를 스페인어로 전환(2026-09-30)하면서 유튜브 알고리즘이 스페인어권
    # 시청자에게 더 정확히 노출/추천하도록 defaultLanguage/defaultAudioLanguage 를
    # 설정한다. 값이 없으면(옛 콘텐츠) 아예 보내지 않는다.
    from src.models import VideoMetadata
    from src.publish import youtube_uploader

    video_path = tmp_path / "part1.mp4"
    video_path.write_bytes(b"fake video bytes")
    metadata = VideoMetadata(title="제목", title_alternatives=[], description="설명", tags=[])
    service = _fake_youtube_service()

    youtube_uploader.upload(service, video_path, metadata, "private", "25", default_language="es")
    body = service.videos.return_value.insert.call_args.kwargs["body"]
    assert body["snippet"]["defaultLanguage"] == "es"
    assert body["snippet"]["defaultAudioLanguage"] == "es"

    service2 = _fake_youtube_service()
    youtube_uploader.upload(service2, video_path, metadata, "private", "25")
    body2 = service2.videos.return_value.insert.call_args.kwargs["body"]
    assert "defaultLanguage" not in body2["snippet"]


def test_upload_creates_playlist_when_missing_and_adds_video(tmp_path):
    from src.models import VideoMetadata
    from src.publish import youtube_uploader

    video_path = tmp_path / "part1.mp4"
    video_path.write_bytes(b"fake video bytes")
    metadata = VideoMetadata(title="제목", title_alternatives=[], description="설명", tags=[])
    service = _fake_youtube_service(existing_playlists=[])  # 아직 없음

    youtube_uploader.upload(
        service, video_path, metadata, "private", "25", playlist_title="재난아카이브"
    )

    service.playlists.return_value.insert.assert_called_once()
    created_body = service.playlists.return_value.insert.call_args.kwargs["body"]
    assert created_body["snippet"]["title"] == "재난아카이브"

    add_body = service.playlistItems.return_value.insert.call_args.kwargs["body"]
    assert add_body["snippet"]["playlistId"] == "pl_new"
    assert add_body["snippet"]["resourceId"]["videoId"] == "vid_new"


def test_upload_reuses_existing_playlist_without_creating_new_one(tmp_path):
    from src.models import VideoMetadata
    from src.publish import youtube_uploader

    video_path = tmp_path / "part1.mp4"
    video_path.write_bytes(b"fake video bytes")
    metadata = VideoMetadata(title="제목", title_alternatives=[], description="설명", tags=[])
    service = _fake_youtube_service(existing_playlists=[("pl_existing", "재난아카이브")])

    youtube_uploader.upload(
        service, video_path, metadata, "private", "25", playlist_title="재난아카이브"
    )

    service.playlists.return_value.insert.assert_not_called()
    add_body = service.playlistItems.return_value.insert.call_args.kwargs["body"]
    assert add_body["snippet"]["playlistId"] == "pl_existing"


def test_upload_links_an_existing_video_as_related(tmp_path):
    from src.models import VideoMetadata
    from src.publish import youtube_uploader

    video_path = tmp_path / "part1.mp4"
    video_path.write_bytes(b"fake video bytes")
    metadata = VideoMetadata(title="제목", title_alternatives=[], description="원래 설명", tags=[])
    service = _fake_youtube_service(existing_videos=["old_vid_1"])

    youtube_uploader.upload(service, video_path, metadata, "private", "25")

    body = service.videos.return_value.insert.call_args.kwargs["body"]
    assert "old_vid_1" in body["snippet"]["description"]
    assert "원래 설명" in body["snippet"]["description"]


def test_upload_survives_playlist_and_related_video_failures(tmp_path):
    """재생목록·관련 영상 권한이 없는(구 스코프) 토큰이어도 업로드 자체는 끝나야 한다."""
    from src.models import VideoMetadata
    from src.publish import youtube_uploader

    video_path = tmp_path / "part1.mp4"
    video_path.write_bytes(b"fake video bytes")
    metadata = VideoMetadata(title="제목", title_alternatives=[], description="설명", tags=[])
    service = _fake_youtube_service()
    service.channels.return_value.list.return_value.execute.side_effect = Exception("403")
    service.playlists.return_value.list.return_value.execute.side_effect = Exception("403")

    video_id = youtube_uploader.upload(service, video_path, metadata, "private", "25")
    assert video_id == "vid_new"


def test_get_service_falls_back_to_upload_only_scope_on_invalid_scope(tmp_path):
    """실제로 있었던 사고: 넓은 스코프(youtube) 요청이 기존 좁은 스코프
    (youtube.upload) 토큰으로 invalid_scope 거부됐다. 그 예외로 전체 업로드가
    죽으면 안 되고, 좁은 스코프로 자동으로 내려가 업로드는 계속돼야 한다."""
    from unittest.mock import MagicMock, patch

    from google.auth.exceptions import RefreshError
    from src.publish import youtube_uploader

    calls = []

    def fake_get_service(client_secret_file, token_file, scopes):
        calls.append(list(scopes))
        if scopes == youtube_uploader.SCOPES:
            raise RefreshError("invalid_scope: Bad Request")
        return MagicMock()

    with patch.object(youtube_uploader, "_get_service", side_effect=fake_get_service):
        service = youtube_uploader.get_service("secret.json", "token.json")

    assert service is not None
    assert calls == [youtube_uploader.SCOPES, youtube_uploader.SCOPES_UPLOAD_ONLY]


def test_get_service_reraises_non_scope_refresh_errors(tmp_path):
    from unittest.mock import patch

    from google.auth.exceptions import RefreshError
    from src.publish import youtube_uploader

    def fake_get_service(client_secret_file, token_file, scopes):
        raise RefreshError("invalid_grant: token expired")

    with patch.object(youtube_uploader, "_get_service", side_effect=fake_get_service):
        with pytest.raises(RefreshError):
            youtube_uploader.get_service("secret.json", "token.json")


def test_badge_layers_never_shows_next_part_banner_when_combined(tmp_path):
    # 실제로 있었던 사고: 합본(--combine-parts) 렌더는 _merge_parts() 가 1·2부를
    # 이어 붙인 뒤에도 ScriptPart.part 를 1로 둔다(화면 상단 타이틀이 1부
    # hook_number 를 쓰기 때문) — 그래서 "part.part == 1 이면 '2부에서 계속' 배지를
    # 붙인다"는 옛 로직이 합본(사실상 유일한 영상)의 끝에도 그 배지를 찍었다.
    # is_combined=True 면 이 분기를 절대 타면 안 된다.
    from src.models import Script
    from src.render.video_builder import _badge_layers

    script = Script(
        topic_key="k", title_seed="제목",
        part1=_make_script_part(1, "훅", ["문장"]),
        part2=_make_script_part(2, "훅2", ["문장2"]),
        source_note="출처: 위키백과",
    )
    merged_part = _make_script_part(1, "훅", ["문장1", "문장2"])

    layers = _badge_layers(
        script, merged_part, tmp_path, (1080, 1920), duration=80.0, credits=[],
        is_combined=True,
    )

    assert not (tmp_path / "banner_cliffhanger.png").exists()
    assert (tmp_path / "banner_source.png").exists()
    assert len(layers) >= 1


def test_overlay_pop_scale_starts_small_overshoots_and_settles():
    # 사용자 지시(2026-09-30): 숫자/단어 오버레이가 뜰 때 커졌다 자리 잡는
    # kinetic typography 팝인 애니메이션.
    from src.render.video_builder import _overlay_pop_scale

    assert _overlay_pop_scale(0.0) == pytest.approx(0.6)
    assert _overlay_pop_scale(0.15) == pytest.approx(1.15)  # 오버슈트 정점
    assert _overlay_pop_scale(0.25) == pytest.approx(1.0)   # 팝인이 끝나면 1.0배
    assert _overlay_pop_scale(1.0) == pytest.approx(1.0)    # 그 뒤로는 계속 1.0배


def test_camera_shake_only_windows_around_impact_sfx(tmp_path):
    # 사용자 지시(2026-09-30): 임팩트 sfx(sub_boom/crack/alarm/braam) 컷이 시작될
    # 때만 화면이 짧게 흔들리고, "none"/"whoosh" 컷에서는 흔들리지 않는다.
    from moviepy import ColorClip
    from src.render.video_builder import _apply_camera_shake

    size = (200, 200)
    bg = ColorClip(size, color=(10, 10, 10), duration=3.0)
    scenes = [
        Scene(start=0.0, duration=1.0, image_query_ko="a", image_query_en="a", camera="push_in", sfx="none"),
        Scene(start=1.0, duration=1.0, image_query_ko="a", image_query_en="a", camera="push_in", sfx="crack"),
        Scene(start=2.0, duration=1.0, image_query_ko="a", image_query_en="a", camera="push_in", sfx="whoosh"),
    ]

    class S:
        def get(self, key, default=None):
            return default

    shaken = _apply_camera_shake(bg, scenes, size, S())
    pos_quiet = shaken.pos(0.5)   # "none" 컷 — 흔들리지 않는 기준 위치
    pos_impact = shaken.pos(1.05)  # "crack" 컷 시작 직후 — 흔들려야 한다
    pos_whoosh = shaken.pos(2.05)  # "whoosh" — 흔들리지 않는다(기준 위치와 같아야 함)

    assert pos_impact != pos_quiet
    assert pos_whoosh == pos_quiet


def test_camera_shake_disabled_by_setting(tmp_path):
    from moviepy import ColorClip
    from src.render.video_builder import _apply_camera_shake

    size = (200, 200)
    bg = ColorClip(size, color=(10, 10, 10), duration=2.0)
    scenes = [
        Scene(start=0.0, duration=1.0, image_query_ko="a", image_query_en="a", camera="push_in", sfx="crack"),
    ]

    class S:
        def get(self, key, default=None):
            return False if key == "video.camera_shake" else default

    result = _apply_camera_shake(bg, scenes, size, S())
    assert result is bg  # 꺼두면 손대지 않고 그대로 돌려준다


def test_multiplatform_caption_sent_with_hashtags_and_pinned_comment(tmp_path):
    # 사용자 지시(2026-09-30): 틱톡/릴스에 바로 복붙할 캡션(해시태그 묶음)과
    # 고정 댓글 후보를 텔레그램으로 같이 보낸다.
    from src import pipeline as pipeline_module

    result = _make_result_for_multiplatform_test(tmp_path)

    settings = MagicMock()
    settings.secrets.telegram_bot_token = "x"
    settings.secrets.telegram_chat_id = "y"

    with patch.object(pipeline_module, "STATE_DB", tmp_path / "state.sqlite3"), patch(
        "src.review.telegram_bot.TelegramReviewer"
    ) as MockReviewer, patch("src.review.telegram_bot.build_summary"):
        reviewer = MockReviewer.return_value
        reviewer.enabled = True
        reviewer.wait_for_decision.return_value = None

        pipeline_module.review_and_publish(settings, result, wait_seconds=1)

    notified = [call.args[0] for call in reviewer.notify.call_args_list]
    caption = next(n for n in notified if "멀티플랫폼" in n)
    assert "#shorts" in caption
    assert "¿Tú qué habrías hecho?" in caption


def _make_result_for_multiplatform_test(tmp_path):
    from src.models import RenderedPart, Script, StoryCandidate, VideoMetadata
    from src import pipeline as pipeline_module

    video = tmp_path / "part1.mp4"
    video.write_bytes(b"fake")
    thumb = tmp_path / "part1_thumb.jpg"
    thumb.write_bytes(b"fake")

    metadata = VideoMetadata(
        title="El Titanic y su falla fatal",
        title_alternatives=[],
        description="Una falla de diseño...",
        tags=[],
        hook_description="Una falla de diseño...",
        source_line="Fuente: Wikipedia",
        pinned_comment="¿Tú qué habrías hecho? 👇",
    )
    part = RenderedPart(
        part=1, video_path=str(video), thumbnail_path=str(thumb), duration=80.0,
        metadata=metadata,
    )
    script = Script(
        topic_key="k", title_seed="제목",
        part1=_make_script_part(1, "훅", ["문장"]),
        part2=_make_script_part(2, "훅2", []),
    )
    candidate = StoryCandidate(
        dataset="disaster", topic_key="disaster:x", headline="사건",
        headline_number="", subject="사건", context="", source_name="Wikipedia",
    )
    return pipeline_module.PipelineResult(
        task_id="task-multiplatform", candidate=candidate, script=script, parts=[part],
        work_dir=tmp_path, credits=[], veo_clips=0, cost_note="",
    )


# --- 2026-10-01: 화면 Korean 누출 수정 + Veo 전체 길이 사용 + 자막 팝인 회귀 테스트 ---


def test_disaster_source_note_is_spanish_not_korean():
    # _parse() 는 실제 LLM 경로에서 쓰이는 유일한 경로다 — 여기서 "출처: X"를
    # 하드코딩하면 스페인어 영상 말미 배지에 한글이 그대로 섞여 나온다(사용자가
    # "자막이 한글어 섞여나와"로 지적한 원인).
    raw = _raw_script(part1_lines=10, part2_lines=10)
    script = _parse(raw, _disaster_candidate())
    assert script is not None
    assert script.source_note == "Fuente: 위키백과"
    assert "출처" not in script.source_note


def test_non_disaster_source_note_stays_korean():
    candidate = StoryCandidate(
        dataset="heritage", topic_key="heritage:test", headline="테스트 문화재",
        headline_number="국보 1호", subject="테스트 문화재", context="본문",
        source_name="국가유산청",
    )
    raw = _raw_script(part1_lines=2, part2_lines=2)
    script = _parse(raw, candidate)
    assert script is not None
    assert script.source_note == "출처: 국가유산청"


def test_photo_credit_line_uses_given_prefix():
    from src.models import PhotoCredit
    from src.render.video_builder import photo_credit_line

    credits = [PhotoCredit(query="q", source_name="Imagen generada por IA")]
    assert photo_credit_line(credits, prefix="Fotos: ") == "Fotos: Imagen generada por IA"
    # 기본값(텔레그램 운영자 알림용)은 계속 한국어다 — 호출부를 안 바꿔도 되게.
    assert photo_credit_line(credits) == "사진: Imagen generada por IA"


def test_thumbnail_label_drops_part_suffix_when_combined():
    from pathlib import Path
    from src.render.thumbnail import build_thumbnail

    part = _make_script_part(1, "El naufragio que nadie esperaba", ["문장"])
    with patch("src.render.thumbnail.VideoFileClip") as mock_clip_cls, patch(
        "src.render.thumbnail.overlays.render_banner"
    ) as mock_banner:
        mock_clip = MagicMock()
        mock_clip.get_frame.return_value = np.zeros((10, 10, 3), dtype=np.uint8)
        mock_clip.duration = 10.0
        mock_clip_cls.return_value.__enter__.return_value = mock_clip
        mock_banner.return_value = Path("/tmp/does-not-matter.png")
        with patch("src.render.thumbnail.Image.open"):
            try:
                build_thumbnail(Path("x.mp4"), part, Path("out.jpg"), (1080, 1920), is_combined=True)
            except Exception:
                pass  # PIL 합성까지는 목이 아니라 label 인자만 확인하면 된다

    label_arg = mock_banner.call_args[0][0]
    assert label_arg == "El naufragio que nadie esperaba"
    assert "부" not in label_arg


def test_veo_clip_speed_scales_instead_of_truncating_when_longer():
    from pathlib import Path
    from src.models import Scene
    from src.render import motion_sources

    scene = Scene(
        start=0.0, duration=4.0, image_query_ko="", image_query_en="",
        camera="push_in", engine="veo", prompt="a test veo prompt",
    )
    fake_source_clip = MagicMock()
    fake_source_clip.duration = 8.0  # Veo 가 4초를 요청해도 8초를 돌려준 상황
    fake_resized = MagicMock()
    fake_source_clip.without_audio.return_value = fake_source_clip
    fake_source_clip.with_speed_scaled.return_value = fake_source_clip
    fake_source_clip.subclipped.return_value = fake_source_clip
    fake_source_clip.resized.return_value = fake_resized

    veo_client = MagicMock()
    veo_client.animate.return_value = Path("/tmp/fake_veo.mp4")

    with patch("src.render.motion_sources.VideoFileClip", return_value=fake_source_clip), patch(
        "src.render.motion_sources._pad_to_duration", side_effect=lambda c, d: c
    ):
        motion_sources._veo_clip(veo_client, Path("img.png"), scene, (1080, 1920))

    # 잘라내지 않고(subclipped 미호출) 속도만 올려 전체 프레임을 다 쓴다.
    fake_source_clip.with_speed_scaled.assert_called_once_with(final_duration=4.0)
    fake_source_clip.subclipped.assert_not_called()


def test_veo_clip_pads_instead_of_speed_scaling_when_close_to_scene_duration():
    from pathlib import Path
    from src.models import Scene
    from src.render import motion_sources

    scene = Scene(
        start=0.0, duration=4.0, image_query_ko="", image_query_en="",
        camera="push_in", engine="veo", prompt="a test veo prompt",
    )
    fake_source_clip = MagicMock()
    fake_source_clip.duration = 4.05  # 반올림 오차 수준
    fake_source_clip.without_audio.return_value = fake_source_clip
    fake_source_clip.subclipped.return_value = fake_source_clip
    fake_source_clip.resized.return_value = fake_source_clip

    veo_client = MagicMock()
    veo_client.animate.return_value = Path("/tmp/fake_veo.mp4")

    with patch("src.render.motion_sources.VideoFileClip", return_value=fake_source_clip), patch(
        "src.render.motion_sources._pad_to_duration", side_effect=lambda c, d: c
    ):
        motion_sources._veo_clip(veo_client, Path("img.png"), scene, (1080, 1920))

    fake_source_clip.with_speed_scaled.assert_not_called()
    fake_source_clip.subclipped.assert_called_once_with(0, 4.0)


def test_disaster_prompt_requires_minimum_veo_count():
    from src.script.script_writer import GLOBAL_DISASTER_PROMPT

    assert "Mínimo 4 frases por parte" in GLOBAL_DISASTER_PROMPT
    assert "mínimo 8 en total" in GLOBAL_DISASTER_PROMPT


def test_render_subtitle_returns_cropped_bbox_not_full_frame():
    from pathlib import Path
    from src.subtitles.subtitle_builder import SubtitleCue
    from src.render import overlays

    cue = SubtitleCue(start=0.0, end=1.0, lines=["hola mundo"], emphasis=[])
    style = {
        "font_size": 46, "font_color": "#FFFFFF", "emphasis_color": "#FFFFFF",
        "shadow_blur": 6, "bottom_ratio": 0.24,
    }
    path, (x0, y0, w, h) = overlays.render_subtitle(
        cue, Path("/tmp/test_sub.png"), (1080, 1920), style, 0.24
    )
    assert path.exists()
    assert 0 < w < 1080
    assert 0 < h < 1920
    path.unlink()
