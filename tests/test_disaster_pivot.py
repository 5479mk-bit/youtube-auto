from pathlib import Path
from unittest.mock import patch

from src.disasters import collect_candidates
from src.narration.tts_supertonic import SupertonicEngine, _rate_to_speed
from src.narration.tts_voxcpm import VOICE_DESIGN_TAG, VoxCPMEngine
from src.topics import topic_scorer


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


def test_collect_candidates_fills_features_so_weights_are_not_a_no_op():
    # 실제로 있었던 사고: candidate.features 가 항상 비어 있어서 topic_scorer 가
    # 모든 후보를 0.000점으로 묶었고, 그 결과 안정 정렬 때문에 config.yaml 목록의
    # 첫 번째 제목(타이타닉호)만 매번 뽑혔다(reuse_topic 기본값 버그와 겹쳐 3일
    # 내내 같은 주제만 나온 원인이었다). weights 축이 실제로 뭔가를 보게 한다.
    with patch(
        "src.disasters.wikipedia_enrich.fetch_full_text",
        return_value="문단 하나.\n\n문단 둘.",
    ):
        candidates = collect_candidates(["타이타닉호"])
    assert candidates[0].features["story_density"] > 0
    assert candidates[0].features["freshness"] > 0


def test_disaster_candidates_no_longer_tie_at_zero_score():
    weights = {"story_density": 0.5, "freshness": 0.5}
    with patch(
        "src.disasters.wikipedia_enrich.fetch_full_text",
        side_effect=["짧다.", "더 긴 위키백과 문단이 여기 여러 번 반복됩니다. " * 20],
    ):
        candidates = collect_candidates(["짧은문서", "긴문서"])
    ranked = topic_scorer.rank(candidates, weights, exclude_keys=set())
    assert ranked[0].score > 0
    assert ranked[0].headline == "긴문서"  # 더 두꺼운 본문이 앞선다


def test_rate_to_speed_converts_percent_strings():
    assert _rate_to_speed("-10%") == 0.9
    assert _rate_to_speed("+2%") == 1.02
    assert _rate_to_speed("") == 1.0


def test_rate_to_speed_clamps_range():
    # Supertonic 문서가 밝힌 안전 배율 범위는 0.7~2.0 이다.
    assert _rate_to_speed("-90%") == 0.7
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
