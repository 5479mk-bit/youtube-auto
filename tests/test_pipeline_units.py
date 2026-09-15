"""핵심 규칙들에 대한 단위 테스트.

렌더 자체는 오래 걸려 여기서 다루지 않는다. 대신 영상 품질을 좌우하는 규칙들
(주제 선정, 컷 배치, 자막, 라이선스 필터, 조사 처리)을 고정한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.datasource.base import load_fixture as load_stats_fixture
from src.heritage.fixtures import load_fixture as load_heritage_fixture
from src.heritage.story_scorer import mystery_score, prestige_score, story_density
from src.models import DataPoint, ScriptLine, ScriptPart, StoryCandidate
from src.script.script_writer import _template_script
from src.narration.pacing import HOOK_PAUSE, SENTENCE_PAUSE, apply_pacing
from src.text_ko import particle, read_aloud, sino_korean
from src.subtitles.subtitle_builder import build_cues, wrap_text
from src.topics import topic_scorer
from src.motion.veo_budget import BudgetExceeded, ClipCost, VeoBudget
from src.visuals.photo_sources.wikimedia import normalize_license
from src.visuals.scene_planner import MIN_CUT_DURATION, engine_counts, plan_scenes

WEIGHTS = {
    "story_density": 0.30,
    "mystery_score": 0.35,
    "prestige": 0.20,
    "freshness": 0.15,
}


def make_candidate(topic_key: str = "t:1", series_length: int = 3, **features) -> StoryCandidate:
    return StoryCandidate(
        dataset="test",
        topic_key=topic_key,
        headline="테스트",
        headline_number="국보",
        subject="테스트 유적",
        context="설명",
        series=[DataPoint(label=f"항목{index}", value=float(index + 1)) for index in range(series_length)],
        series_unit="년",
        source_name="테스트 출처",
        image_queries_ko=["테스트 유적"],
        image_queries_en=["test heritage site"],
        facts=["테스트 유적은 통일신라시대에 세워진 것으로 전해진다."],
        wiki_excerpt="전해지는 말로는 테스트 유적에 얽힌 전설이 있다고 한다.",
        features={"story_density": 0.5, "mystery_score": 0.5, "prestige": 0.5, "freshness": 0.5}
        | features,
    )


def make_part(texts: list[tuple[str, float, bool]]) -> ScriptPart:
    return ScriptPart(
        part=1,
        hook_number="3.0년",
        lines=[
            ScriptLine(
                text=text,
                visual_query_ko="서울",
                visual_query_en="seoul",
                show_chart=chart,
                duration=duration,
            )
            for text, duration, chart in texts
        ],
    )


class TestTopicScorer:
    def test_already_used_topics_are_excluded(self):
        candidates = [make_candidate("used"), make_candidate("fresh")]
        picked = topic_scorer.pick(candidates, WEIGHTS, {"used"})
        assert picked is not None and picked.topic_key == "fresh"

    def test_candidate_without_facts_or_wiki_excerpt_is_skipped(self):
        empty = make_candidate("empty", mystery_score=1.0)
        empty.facts = []
        empty.wiki_excerpt = ""
        grounded = make_candidate("grounded", mystery_score=0.1)
        picked = topic_scorer.pick([empty, grounded], WEIGHTS, set())
        assert picked is not None and picked.topic_key == "grounded"

    def test_higher_mystery_score_wins(self):
        low = make_candidate("low", mystery_score=0.1)
        high = make_candidate("high", mystery_score=1.0)
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


class TestMotionEngines:
    def test_opening_becomes_one_veo_scene(self):
        part = make_part([(f"문장 {index}", 3.0, False) for index in range(8)])
        scenes = plan_scenes(
            part, 3.0, freeze_last=True, series_labels=[], veo_seconds=8.0
        )
        # 8초를 결제하니 8초를 다 쓴다. 3초만 쓰고 5초를 버리면 돈을 버리는 것과 같다.
        assert scenes[0].engine == "veo"
        assert scenes[0].duration == pytest.approx(8.0)
        assert engine_counts(scenes)["veo"] == 1

    def test_without_veo_every_cut_is_parallax(self):
        part = make_part([(f"문장 {index}", 3.0, False) for index in range(4)])
        scenes = plan_scenes(part, 3.0, freeze_last=False, series_labels=[])
        assert engine_counts(scenes) == {"veo": 0, "parallax": len(scenes)}


class TestVeoBudget:
    def _budget(self, tmp_path, per_episode=2, per_day=6) -> VeoBudget:
        return VeoBudget(tmp_path, "lite", per_episode, per_day)

    def test_same_input_gives_the_same_cache_key(self, tmp_path):
        image = tmp_path / "a.jpg"
        image.write_bytes(b"fake-image-bytes")
        budget = self._budget(tmp_path)
        params = {"aspectRatio": "9:16"}
        assert budget.cache_key(image, "prompt", params) == budget.cache_key(
            image, "prompt", params
        )
        assert budget.cache_key(image, "prompt", params) != budget.cache_key(
            image, "다른 프롬프트", params
        )

    def test_episode_limit_blocks_further_generation(self, tmp_path):
        budget = self._budget(tmp_path, per_episode=1)
        budget.ensure_can_generate()
        budget.record(1)
        with pytest.raises(BudgetExceeded):
            budget.ensure_can_generate()

    def test_daily_limit_blocks_generation(self, tmp_path):
        budget = self._budget(tmp_path, per_episode=99, per_day=1)
        budget.record(1)
        with pytest.raises(BudgetExceeded):
            budget.ensure_can_generate()

    def test_cost_scales_with_tier_and_clip_count(self):
        assert ClipCost(2, 8, "lite").usd == pytest.approx(0.8)
        assert ClipCost(2, 8, "fast").usd == pytest.approx(2.4)
        assert "2클립" in ClipCost(2, 8, "lite").summary()


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
        haystack = normalize_license(declared)
        assert any(normalize_license(item) in haystack for item in allowed)

    def test_unfree_license_does_not_match(self):
        haystack = normalize_license("All rights reserved")
        assert not any(
            normalize_license(item) in haystack for item in ["cc0", "cc-by", "cc-by-sa"]
        )


class TestReadAloud:
    @pytest.mark.parametrize(
        ("number", "expected"),
        [(0, "영"), (10, "십"), (1000, "천"), (12480, "만 이천사백팔십"), (100000, "십만")],
    )
    def test_sino_korean_reading(self, number, expected):
        assert sino_korean(number) == expected

    def test_decimals_are_read_digit_by_digit(self):
        assert read_aloud("2.9년") == "이 점 구 년"

    def test_thousands_separator_is_handled(self):
        assert read_aloud("12,480곳") == "만 이천사백팔십 곳"

    def test_percent_becomes_a_word(self):
        assert read_aloud("22.6% 올랐습니다") == "이십이 점 육 퍼센트 올랐습니다"

    def test_short_numbers_stay_attached_to_their_unit(self):
        # '일 부에서' 가 아니라 '일부에서'. 짧은 숫자는 붙여 읽어야 자연스럽다.
        assert read_aloud("1부에서") == "일부에서"

    def test_text_without_numbers_is_untouched(self):
        assert read_aloud("짧죠.") == "짧죠."


class TestPacing:
    def test_hook_is_slower_and_gets_a_longer_pause(self):
        part = make_part([("훅", 2.0, False), ("본문", 3.0, False), ("마무리", 3.0, False)])
        apply_pacing(part)
        assert part.lines[0].pause_after == pytest.approx(HOOK_PAUSE)
        assert part.lines[0].speech_rate.startswith("-")

    def test_every_line_gets_a_pause_so_sentences_never_collide(self):
        part = make_part([(f"문장{index}", 3.0, False) for index in range(5)])
        apply_pacing(part)
        assert all(line.pause_after >= SENTENCE_PAUSE for line in part.lines[:-1])

    def test_line_reads_spoken_text_when_present(self):
        part = make_part([("2.9년", 2.0, False)])
        part.lines[0].spoken_text = "이 점 구 년"
        assert part.lines[0].narration == "이 점 구 년"
        part.lines[0].spoken_text = ""
        assert part.lines[0].narration == "2.9년"


class TestScriptTemplate:
    def test_particles_follow_the_final_consonant(self):
        assert particle("카페", "이", "가") == "카페가"
        assert particle("한식", "이", "가") == "한식이"
        assert particle("년", "으로", "로") == "년으로"

    def test_template_covers_both_parts_and_cites_source(self):
        script = _template_script(make_candidate())
        assert script.part1.lines and script.part2.lines
        assert any("전해지는 말로는" in line.text for line in script.part1.lines + script.part2.lines)
        assert any("테스트 출처" in line.text for line in script.part2.lines)

    def test_template_without_wiki_excerpt_falls_back_to_facts_only(self):
        candidate = make_candidate()
        candidate.wiki_excerpt = ""
        script = _template_script(candidate)
        joined = " ".join(line.text for line in script.part1.lines + script.part2.lines)
        assert "전해지는 말로는" not in joined


class TestHeritageScorer:
    def test_mystery_keywords_raise_the_score(self):
        plain = mystery_score("평범한 사실 설명 문장입니다." * 10)
        mysterious = mystery_score("전설과 설화가 전해지며, 사라진 유물의 행방은 여전히 의문이다." * 3)
        assert mysterious > plain

    def test_prestige_ranks_designation_grades(self):
        assert prestige_score("국보") > prestige_score("보물")
        assert prestige_score("보물") > prestige_score("시도유형문화유산")
        assert prestige_score("") == prestige_score("알수없음")

    def test_story_density_scales_with_text_length(self):
        assert story_density("짧다") < story_density("길다 " * 200)


class TestFixtures:
    def test_stats_fixture_candidates_are_marked_as_sample(self):
        candidates = load_stats_fixture("business")
        assert candidates
        assert all(candidate.is_sample for candidate in candidates)

    def test_heritage_fixture_candidates_are_marked_as_sample(self):
        candidates = load_heritage_fixture()
        assert candidates
        assert all(candidate.is_sample for candidate in candidates)
        assert all(candidate.facts for candidate in candidates)
