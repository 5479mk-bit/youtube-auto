"""틱톡 확장판(60초 초과) + 유튜브판(35~45초) 동시 생성(2026-10-27 사용자 지시)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import imageio_ffmpeg

from src import pipeline
from src.models import RenderedPart, ScriptLine, ScriptPart, StoryCandidate, VideoMetadata
from src.narration import tts_engine
from src.script import script_writer


def _candidate():
    return StoryCandidate(
        dataset="disaster", topic_key="disaster:X", headline="X", headline_number="",
        subject="X", context="texto", facts=["texto"], source_name="Wikipedia",
    )


def _raw_line(text, solo=False, scene="AI_IMAGE"):
    return {"text": text, "scene_type": scene, "prompt": "p", "sfx": "none", "solo_tiktok": solo}


def test_prompt_asks_for_tiktok_only_lines():
    prompt = script_writer._prompt(_candidate(), 260, 290)
    assert "solo_tiktok" in prompt and "más de 60 segundos" in prompt
    assert "35 a 45 segundos" in prompt


def test_parse_marks_tiktok_only_and_protects_key_lines():
    veo = "Una frase de video bastante larga"
    part1 = [_raw_line(veo, solo=True, scene="VEO"), _raw_line("Dos", solo=True)] + [
        _raw_line(f"Normal {i}") for i in range(5)
    ] + [_raw_line("Extra uno", solo=True, scene="VEO"), _raw_line("Cierre", solo=True)]
    part2 = [_raw_line(f"Normal {i}") for i in range(5)] + [_raw_line("Extra dos", solo="true")]
    script = script_writer._parse(
        {"title_seed": "t", "part1": {"hook_number": "¿Qué?", "lines": part1},
         "part2": {"hook_number": "", "lines": part2}},
        _candidate(),
    )
    lines1 = script.part1.lines
    # 첫 두 문장과 각 부의 마지막 문장은 유튜브판에 남아야 한다.
    assert not lines1[0].tiktok_only and not lines1[1].tiktok_only and not lines1[-1].tiktok_only
    extra = lines1[-2]
    assert extra.tiktok_only and extra.scene_type == "AI_IMAGE"  # Veo 클립을 더 쓰지 않는다
    assert lines1[0].scene_type == "VEO"
    assert not script.part2.lines[-1].tiktok_only


def test_youtube_cut_drops_tiktok_only_lines_but_shares_objects():
    lines = [ScriptLine(text=t, visual_query_ko="", tiktok_only=t.startswith("x")) for t in ("a", "x1", "b", "x2", "c")]
    full = ScriptPart(part=1, hook_number="¿?", lines=lines)
    cut = pipeline._youtube_cut(full)
    assert [line.text for line in cut.lines] == ["a", "b", "c"]
    assert cut.lines[0] is lines[0]


def _tone(path: Path, seconds: float) -> Path:
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i",
         f"sine=frequency=440:duration={seconds}", str(path)],
        check=True, capture_output=True,
    )
    return path


def _part_with_audio(tmp_path, seconds_each, count):
    lines = []
    for index in range(count):
        raw = _tone(tmp_path / f"line{index}.wav", seconds_each)
        lines.append(ScriptLine(text=f"l{index}", visual_query_ko="", raw_audio_path=str(raw),
                                audio_path=str(raw), duration=seconds_each))
    return ScriptPart(part=1, hook_number="¿?", lines=lines)


def test_retimed_copy_slows_down_to_pass_sixty_seconds(tmp_path):
    part = _part_with_audio(tmp_path, 5.0, 12)  # 1.0배속이면 60초 — 61.5초에 모자란다
    result = tts_engine.retimed_copy(part, 1.0, min_seconds=61.5)
    total = sum(line.duration for line in result.lines)
    assert total >= 61.5
    # 원본(유튜브판) 줄은 건드리지 않는다.
    assert part.lines[0].duration == 5.0 and part.lines[0].audio_path.endswith("line0.wav")
    assert result.lines[0].audio_path.endswith("_tiktok.wav")


def test_retimed_copy_does_not_pad_huge_gaps(tmp_path):
    part = _part_with_audio(tmp_path, 2.0, 10)  # 20초 — 배속 0.9 로도 한참 모자란다
    result = tts_engine.retimed_copy(part, 1.0, min_seconds=61.5)
    total = sum(line.duration for line in result.lines)
    assert 20 < total < 30  # 40초 무음으로 억지로 늘리지 않는다


def test_render_tiktok_version_failure_keeps_youtube(tmp_path):
    youtube = RenderedPart(part=1, video_path=str(tmp_path / "yt.mp4"), thumbnail_path="",
                           duration=40, metadata=VideoMetadata("t", [], "d", []))
    settings = MagicMock()
    settings.get.side_effect = lambda key, default=None: default
    with patch.object(pipeline.tts_engine, "retimed_copy", side_effect=RuntimeError("boom")):
        pipeline._render_tiktok_version(settings, None, ScriptPart(1, "", []), None, None, None,
                                        tmp_path, None, None, None, youtube)
    assert youtube.tiktok_video_path == ""
    assert pipeline._tiktok_file(youtube) == Path(youtube.video_path)


def test_tiktok_upload_uses_extended_file(tmp_path):
    tiktok = tmp_path / "tiktok.mp4"
    tiktok.write_bytes(b"x")
    part = RenderedPart(part=1, video_path=str(tmp_path / "yt.mp4"), thumbnail_path="", duration=40,
                        metadata=VideoMetadata("Título", [], "d", []),
                        tiktok_video_path=str(tiktok), tiktok_duration=64.2)
    settings = MagicMock()
    settings.get.side_effect = lambda key, default=None: {"publish.tiktok.enabled": True}.get(key, default)
    with patch("src.publish.tiktok_uploader.get_client", return_value=object()), \
         patch("src.publish.tiktok_uploader.upload", return_value="post123") as upload:
        links = pipeline._upload_to_extra_platforms(settings, part)
    assert upload.call_args.args[1] == tiktok
    assert "확장판 64초" in links[0]


def test_config_enables_extended_tiktok_and_updated_balance():
    import yaml

    config = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))
    tiktok = config["publish"]["tiktok"]
    assert tiktok["extended"] is True and tiktok["min_seconds"] > 60
    assert config["cloud"]["balance_krw"] == 78119
    assert config["cloud"]["balance_adjust_krw"] == 0


def test_finish_keeps_raw_audio_so_tiktok_is_slower_than_youtube(tmp_path):
    raw = _tone(tmp_path / "line00.wav", 3.0)
    part = ScriptPart(part=1, hook_number="", lines=[ScriptLine(text="a", visual_query_ko="")])
    tts_engine._finish(part, [raw], tempo=1.1)
    youtube_seconds = part.lines[0].duration
    assert Path(part.lines[0].raw_audio_path).exists()
    tiktok = tts_engine.retimed_copy(part, 1.0)
    assert tiktok.lines[0].duration > youtube_seconds + 0.2
    assert part.lines[0].duration == youtube_seconds
