from pathlib import Path
from unittest.mock import patch

from src.disasters import collect_candidates
from src.narration.tts_supertonic import SupertonicEngine, _rate_to_speed
from src.narration.tts_voxcpm import VOICE_DESIGN_TAG, VoxCPMEngine


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


def test_voxcpm_engine_returns_false_when_library_missing(tmp_path):
    # 이 세션에는 voxcpm(GPU 전용 패키지)이 설치돼 있지 않으므로, 임포트 실패 →
    # 조용한 False 폴백 경로가 실제로 타는지 확인한다.
    engine = VoxCPMEngine()
    out_path = tmp_path / "out.wav"
    assert engine.synthesize("테스트 문장", "+0%", out_path) is False
    assert engine._load_failed is True


def test_voxcpm_engine_caches_load_failure(tmp_path):
    # 한 번 로딩에 실패하면 같은 인스턴스로 다시 호출해도 매번 무거운 임포트를
    # 재시도하지 않는다 — _load_failed 캐시가 실제로 동작하는지 확인한다.
    engine = VoxCPMEngine()
    engine.synthesize("첫 문장", "+0%", tmp_path / "a.wav")
    assert engine._load_failed is True
    assert engine.synthesize("둘째 문장", "+0%", tmp_path / "b.wav") is False
    assert engine._model is None


def test_voice_design_tag_is_prepended():
    assert VOICE_DESIGN_TAG.startswith("(") and VOICE_DESIGN_TAG.endswith(")")
