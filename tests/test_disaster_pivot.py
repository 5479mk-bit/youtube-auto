from pathlib import Path
from unittest.mock import patch

from src.disasters import collect_candidates
from src.narration.tts_supertonic import SupertonicEngine, _rate_to_speed


def test_collect_candidates_blocks_korean_disasters():
    candidates = collect_candidates(["세월호 침몰 사고", "천안함"])
    assert candidates == []


def test_collect_candidates_skips_empty_wikipedia_results():
    with patch("src.disasters.wikipedia_enrich.fetch_full_text", return_value=""):
        candidates = collect_candidates(["존재하지 않는 문서"])
    assert candidates == []


def test_collect_candidates_builds_disaster_candidate():
    with patch(
        "src.disasters.wikipedia_enrich.fetch_full_text",
        return_value="문단 하나.\n\n문단 둘.",
    ):
        candidates = collect_candidates(["타이타닉호"])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.dataset == "disaster"
    assert candidate.headline == "타이타닉호"
    assert candidate.facts == ["문단 하나.", "문단 둘."]


def test_rate_to_speed_converts_percent_strings():
    assert _rate_to_speed("-10%") == 0.9
    assert _rate_to_speed("+2%") == 1.02
    assert _rate_to_speed("") == 1.0


def test_rate_to_speed_clamps_range():
    assert _rate_to_speed("-90%") == 0.5
    assert _rate_to_speed("+500%") == 2.0


def test_supertonic_engine_returns_false_when_server_unreachable(tmp_path):
    engine = SupertonicEngine(base_url="http://127.0.0.1:1/api/synthesize", timeout=1)
    out_path = tmp_path / "out.mp3"
    assert engine.synthesize("테스트", "+0%", out_path) is False
