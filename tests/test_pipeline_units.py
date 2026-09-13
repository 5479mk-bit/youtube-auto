"""핵심 규칙들에 대한 단위 테스트.

렌더 자체는 오래 걸려 여기서 다루지 않는다. 대신 영상 품질을 좌우하는 규칙들
(주제 선정, 컷 배치, 자막, 라이선스 필터, 조사 처리)을 고정한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.datasource.base import load_fixture
from src.models import DataPoint, ScriptLine, ScriptPart, StoryCandidate
from src.script.script_writer import _template_script
from src.text_ko import particle
from src.subtitles.subtitle_builder import build_cues, wrap_text
from src.topics import topic_scorer
from src.visuals.image_collector import _normalize_license
from src.visuals.scene_planner import MIN_CUT_DURATION, plan_scenes

WEIGHTS = {
    "personal_relevance": 0.35,
    "surprise": 0.35,
    "magnitude": 0.20,
    "freshness": 0.10,
}


def make_candidate(topic_key: str = "t:1", series_length: int = 3, **features) -> StoryCandidate:
    return StoryCandidate(
        dataset="test",
        topic_key=topic_key,
        headline="테스트",
        headline_number="3.0년",
        subject="테스트 업종",
        context="설명",
        series=[DataPoint(label=f"항목{index}", value=float(index + 1)) for index in range(series_length)],
        series_unit="년",
        source_name="테스트 출처",
        image_queries=["test city"],
        features={"personal_relevance": 0.5, "surprise": 0.5, "magnitude": 0.5, "freshness": 0.5}
        | features,
    )


def make_part(texts: list[tuple[str, float, bool]]) -> ScriptPart:
    return ScriptPart(
        part=1,
        hook_number="3.0년",
        lines=[
            ScriptLine(text=text, visual_query="q", show_chart=chart, duration=duration)
            for text, duration, chart in texts
        ],
    )


class TestTopicScorer:
    def test_already_used_topics_are_excluded(self):
        candidates = [make_candidate("used"), make_candidate("fresh")]
        picked = topic_scorer.pick(candidates, WEIGHTS, {"used"})
        assert picked is not None and picked.topic_key == "fresh"

    def test_candidate_without_chartable_series_is_skipped(self):
        thin = make_candidate("thin", series_length=1, surprise=1.0)
        fat = make_candidate("fat", series_length=4, surprise=0.1)
        picked = topic_scorer.pick([thin, fat], WEIGHTS, set())
        assert picked is not None and picked.topic_key == "fat"

    def test_higher_personal_relevance_wins(self):
        low = make_candidate("low", personal_relevance=0.1)
        high = make_candidate("high", personal_relevance=1.0)
        picked = topic_scorer.pick([low, high], WEIGHTS, set())
        assert picked is not None and picked.topic_key == "high"


class TestScenePlanner:
    def test_consecutive_cuts_never_repeat_the_same_move(self):
        part = make_part([(f"문장 {index}", 6.0, False) for index in range(5)])
        scenes = plan_scenes(part, 3.0, freeze_last=False, series_labels=[])
        moves = [scene.camera for scene in scenes]
        assert all(first != second for first, second in zip(moves, moves[1:]))

    def test_long_line_is_split_into_multiple_cuts(self):
        part = make_part([("아주 긴 문장", 9.0, False)])
        scenes = plan_scenes(part, 3.0, freeze_last=False, series_labels=[])
        assert len(scenes) == 3
        assert all(scene.duration == pytest.approx(3.0) for scene in scenes)

    def test_zero_duration_line_still_gets_a_visible_cut(self):
        part = make_part([("무음", 0.0, False)])
        scenes = plan_scenes(part, 3.0, freeze_last=False, series_labels=[])
        assert scenes[0].duration == pytest.approx(MIN_CUT_DURATION)

    def test_chart_reveals_up_to_the_item_being_narrated(self):
        part = make_part(
            [("카페 이야기", 3.0, True), ("편의점은 다릅니다", 3.0, True)]
        )
        scenes = plan_scenes(
            part, 3.0, freeze_last=False, series_labels=["카페", "치킨", "편의점"]
        )
        assert scenes[0].chart_step == 1
        # '편의점'은 세 번째 항목이므로 두 칸을 건너뛰어 함께 드러나야 한다.
        assert scenes[1].chart_step == 3

    def test_first_part_ends_on_a_freeze(self):
        part = make_part([("하나", 3.0, False), ("둘", 3.0, False)])
        scenes = plan_scenes(part, 3.0, freeze_last=True, series_labels=[])
        assert scenes[-1].camera == "freeze"


class TestSubtitles:
    def test_wrap_keeps_lines_within_limit(self):
        lines = wrap_text("서울에서 가장 빨리 사라지는 가게 업종은 무엇일까요", 16)
        assert all(len(line) <= 16 for line in lines)

    def test_hook_line_is_suppressed_but_timing_is_kept(self):
        part = make_part([("3.0년.", 2.0, False), ("이어지는 문장", 3.0, False)])
        cues = build_cues(part, 16, suppress_texts=["3.0년"])
        assert len(cues) == 1
        # 훅 자막을 지워도 다음 자막은 원래 시각에 그대로 시작해야 한다.
        assert cues[0].start == pytest.approx(2.0)


class TestLicenseFilter:
    @pytest.mark.parametrize(
        "declared",
        ["CC BY-SA 4.0", "cc_by_sa", "CC-BY-SA-3.0", "Public domain"],
    )
    def test_hyphen_and_space_variants_match(self, declared):
        allowed = ["cc-by-sa", "public domain"]
        haystack = _normalize_license(declared)
        assert any(_normalize_license(item) in haystack for item in allowed)

    def test_unfree_license_does_not_match(self):
        haystack = _normalize_license("All rights reserved")
        assert not any(
            _normalize_license(item) in haystack for item in ["cc0", "cc-by", "cc-by-sa"]
        )


class TestScriptTemplate:
    def test_particles_follow_the_final_consonant(self):
        assert particle("카페", "이", "가") == "카페가"
        assert particle("한식", "이", "가") == "한식이"
        assert particle("년", "으로", "로") == "년으로"

    def test_template_covers_both_parts_and_shows_charts(self):
        script = _template_script(make_candidate(series_length=5))
        assert script.part1.lines and script.part2.lines
        assert any(line.show_chart for line in script.part1.lines)
        assert any(line.show_chart for line in script.part2.lines)


class TestFixtures:
    def test_fixture_candidates_are_marked_as_sample(self):
        candidates = load_fixture("business")
        assert candidates
        assert all(candidate.is_sample for candidate in candidates)
