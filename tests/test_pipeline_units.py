"""핵심 규칙들에 대한 단위 테스트.

렌더 자체는 오래 걸려 여기서 다루지 않는다. 대신 영상 품질을 좌우하는 규칙들
(주제 선정, 컷 배치, 자막, 라이선스 필터, 조사 처리)을 고정한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import pipeline
from src.cloud.spend_guard import CreditsExhausted, SpendGuard
from src.datasource.base import load_fixture as load_stats_fixture
from src.heritage import khs_client
from src.heritage.fixtures import load_fixture as load_heritage_fixture
from src.heritage.heritage_media import HeritageVideo, fetch_images, select_video
from src.heritage.heritage_source import HeritageSource
from src.heritage.khs_client import _extract_items
from src.heritage.story_scorer import mystery_score, prestige_score, story_density
from src.models import DataPoint, ScriptLine, ScriptPart, StoryCandidate
from src.script.script_writer import _template_script
from src.sillok import article as sillok_article
from src.sillok.event_keywords import scan
from src.narration.pacing import HOOK_PAUSE, SENTENCE_PAUSE, apply_pacing
from src.text_ko import particle, read_aloud, sino_korean
from src.subtitles.subtitle_builder import build_cues, wrap_text
from src.topics import topic_scorer
from src.visuals.image_collector import ImageCollector
from src.visuals.photo_sources.base import PhotoResult
from src.motion.veo_budget import BudgetExceeded, ClipCost, VeoBudget
from src.review.telegram_bot import ReviewDecision, TelegramReviewer
from src.topics import history
from src.visuals.photo_sources.wikimedia import WikimediaSource, normalize_license
from src.visuals.scene_planner import (
    MIN_CUT_DURATION,
    engine_counts,
    interleave_veo,
    plan_scenes,
)

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
        counts = engine_counts(scenes)
        assert counts["parallax"] == len(scenes)
        assert not any(count for name, count in counts.items() if name != "parallax")

    def test_official_video_intro_skips_veo(self):
        part = make_part([(f"문장 {index}", 3.0, False) for index in range(8)])
        scenes = plan_scenes(
            part,
            3.0,
            freeze_last=True,
            series_labels=[],
            veo_seconds=6.0,
            intro_engine="official_video",
            intro_video_path="/tmp/official_intro.mp4",
        )
        assert scenes[0].engine == "official_video"
        assert scenes[0].official_video_path == "/tmp/official_intro.mp4"
        assert engine_counts(scenes)["veo"] == 0
        assert engine_counts(scenes)["official_video"] == 1


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


class TestWikimediaRelevance:
    def test_search_ranking_is_respected_not_pageid_order(self):
        # MediaWiki 는 결과를 pageid 키 딕셔너리로 준다. 그대로 순회하면 검색 순위가 아니라
        # pageid 순서로 읽혀, 실제로 "석굴암" 검색에서 엉뚱한 사진이 먼저 잡혔었다.
        source = WikimediaSource(["cc0"], 1080)
        payload = {
            "query": {
                "pages": {
                    "999": _wiki_page(index=2, title="File:Second.jpg", url="http://x/2.jpg"),
                    "111": _wiki_page(index=1, title="File:First.jpg", url="http://x/1.jpg"),
                }
            }
        }
        with patch.object(source, "_get_with_retry", return_value=_FakeResponse(payload)):
            results = source.search("석굴암", "Seokguram", limit=2)

        assert [r.url for r in results] == ["http://x/1.jpg", "http://x/2.jpg"]

    def test_photos_of_somewhere_near_the_subject_are_rejected(self):
        # 다른 유적을 띄워놓고 이 유적을 설명하는 게 이 채널에선 가장 치명적인 사고다.
        source = WikimediaSource(["cc0"], 1080)
        payload = {
            "query": {
                "pages": {
                    "1": _wiki_page(
                        index=1,
                        title="File:Interior view of the Buddhist temple near Seokguram Grotto.jpg",
                        url="http://x/near.jpg",
                    ),
                    "2": _wiki_page(index=2, title="File:Seokguram Grotto 03.jpg", url="http://x/real.jpg"),
                }
            }
        }
        with patch.object(source, "_get_with_retry", return_value=_FakeResponse(payload)):
            results = source.search("석굴암", "Seokguram", limit=2)

        assert [r.url for r in results] == ["http://x/real.jpg"]


def _wiki_page(index: int, title: str, url: str) -> dict:
    return {
        "index": index,
        "title": title,
        "imageinfo": [
            {
                "width": 1600,
                "height": 1200,
                "url": url,
                "descriptionurl": f"https://commons.wikimedia.org/wiki/{title}",
                "extmetadata": {
                    "LicenseShortName": {"value": "CC0"},
                    "License": {"value": "cc0"},
                },
            }
        ],
    }


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


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
        # 절대 배속(부호)이 아니라 본문보다 상대적으로 느린지를 본다 — 사용자 청취
        # 검증으로 전체 기준 속도가 "-"(0.85~0.9배속)에서 "+"(1.05~1.1배속)로
        # 바뀌었지만, 훅이 본문보다 느리게 시작한다는 관계는 그대로다.
        from src.narration.pacing import BODY_RATE, _rate_to_percent

        part = make_part([("훅", 2.0, False), ("본문", 3.0, False), ("마무리", 3.0, False)])
        apply_pacing(part)
        assert part.lines[0].pause_after == pytest.approx(HOOK_PAUSE)
        assert _rate_to_percent(part.lines[0].speech_rate) < _rate_to_percent(BODY_RATE)

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


class TestHeritageMedia:
    def test_select_video_returns_first_when_available(self):
        # 실제 응답엔 url 뿐이라(해상도·재생시간 필드가 없음) 순서대로 첫 항목을 쓴다 —
        # 부적합하면 다운로드 후 pipeline.fetch_official_video 가 걸러낸다.
        first = HeritageVideo(url="a")
        second = HeritageVideo(url="b")
        assert select_video([first, second]) is first

    def test_select_video_returns_none_for_empty_list(self):
        assert select_video([]) is None

    def test_fetch_images_reads_real_field_names(self):
        # 사용자가 실제로 호출해 확인한 이미지 API 응답 형태(item: sn/imageNuri/imageUrl/
        # ccimDesc, ccbaMnm1 같은 이름 필드는 item 밖에 있어 안 옴)를 그대로 본떴다.
        rows = [
            {"sn": "1", "imageNuri": "A", "imageUrl": "http://example/1.jpg", "ccimDesc": "전경"},
        ]
        with patch.object(khs_client, "get", return_value=rows):
            photos = fetch_images("11", "0000030000000", "11")
        assert photos[0].url == "http://example/1.jpg"
        assert photos[0].author == "전경"


class TestHeritageSourceDetail:
    def test_fetch_detail_reads_real_field_names(self):
        # 사용자가 실제로 호출해 확인한 상세 API 응답 형태를 그대로 본떴다. ccbaAsdt 는
        # 지정일자(날짜 문자열)라 해설문으로 쓰면 안 된다 — content 만 써야 한다.
        row = {
            "ccmaName": "국보",
            "ccbaMnm1": "서울 북한산 신라 진흥왕 순수비",
            "ccbaAsdt": "19621220",
            "ccbaLcad": "서울 용산구 서빙고로 137, 국립중앙박물관 (용산동6가)",
            "content": "신라 진흥왕이 세운 순수척경비 중 하나다.",
        }
        with patch.object(khs_client, "get", return_value=[row]):
            candidate = HeritageSource().fetch_detail(
                "서울 북한산 신라 진흥왕 순수비", "문화유산", "", "11", "0000030000000", "11"
            )
        assert candidate is not None
        assert candidate.headline_number == "국보"
        assert candidate.context == "신라 진흥왕이 세운 순수척경비 중 하나다."
        assert "19621220" not in candidate.context
        assert candidate.heritage_ccba_kdcd == "11"
        assert candidate.heritage_ccba_asno == "0000030000000"


class TestInterleaveVeo:
    def _part(self, count: int = 10) -> ScriptPart:
        return ScriptPart(
            part=1,
            hook_number="훅",
            lines=[
                ScriptLine(
                    text=f"문장{i}",
                    visual_query_ko=f"사진{i // 3}",
                    visual_query_en=f"photo{i // 3}",
                    duration=3.0,
                )
                for i in range(count)
            ],
        )

    def test_veo_cut_reuses_the_photo_from_the_still_before_it(self):
        # "사진 → 그 사진이 움직이는 컷" 이 되려면 Veo 컷이 앞 사진과 같은 검색어여야 한다
        # (ImageCollector 가 검색어로 캐시하므로 같은 파일이 쓰인다).
        scenes = plan_scenes(self._part(), 3.0, freeze_last=False, series_labels=[])
        out = interleave_veo(scenes, clip_seconds=8.0, still_seconds=6.0, max_clips=2)

        for index, scene in enumerate(out):
            if scene.engine == "veo":
                assert index > 0
                assert out[index - 1].image_query_ko == scene.image_query_ko

    def test_veo_cuts_are_exactly_one_billing_unit_long(self):
        # 생성 클립은 정확히 8초다. 씬을 9초로 잡으면 1초가 비고, 3초로 잡으면 8초 값을
        # 내고 5초를 버린다.
        scenes = plan_scenes(self._part(), 3.0, freeze_last=False, series_labels=[])
        out = interleave_veo(scenes, clip_seconds=8.0, still_seconds=6.0, max_clips=2)

        veo_cuts = [scene for scene in out if scene.engine == "veo"]
        assert veo_cuts
        assert all(scene.duration == pytest.approx(8.0) for scene in veo_cuts)

    def test_total_duration_is_preserved(self):
        scenes = plan_scenes(self._part(), 3.0, freeze_last=False, series_labels=[])
        before = sum(scene.duration for scene in scenes)
        out = interleave_veo(scenes, clip_seconds=8.0, still_seconds=6.0, max_clips=2)
        assert sum(scene.duration for scene in out) == pytest.approx(before)

    def test_max_clips_is_respected(self):
        scenes = plan_scenes(self._part(20), 3.0, freeze_last=False, series_labels=[])
        out = interleave_veo(scenes, clip_seconds=8.0, still_seconds=6.0, max_clips=1)
        assert engine_counts(out)["veo"] == 1

    def test_zero_clips_leaves_scenes_untouched(self):
        scenes = plan_scenes(self._part(), 3.0, freeze_last=False, series_labels=[])
        assert interleave_veo(scenes, 8.0, 6.0, max_clips=0) is scenes

    def test_too_short_to_fill_a_billing_unit_makes_no_clip(self):
        # 남은 길이가 8초를 못 채우면 아예 만들지 않는다 — 3초 쓰자고 8초 값을 낼 순 없다.
        short = ScriptPart(
            part=1,
            hook_number="훅",
            lines=[
                ScriptLine(text="하나", visual_query_ko="사진", visual_query_en="p", duration=3.0),
                ScriptLine(text="둘", visual_query_ko="사진", visual_query_en="p", duration=3.0),
                ScriptLine(text="셋", visual_query_ko="사진", visual_query_en="p", duration=3.0),
            ],
        )
        scenes = plan_scenes(short, 3.0, freeze_last=False, series_labels=[])
        out = interleave_veo(scenes, clip_seconds=8.0, still_seconds=6.0, max_clips=2)
        assert engine_counts(out)["veo"] == 0


class TestSillokEventKeywords:
    def test_finds_events_in_classical_chinese(self):
        # 공개된 실록은 원문(한문)뿐이라, 사건 발굴은 한문에서 걸려야 한다.
        assert scan("夜 彗星見于東方 光芒指西").matched
        assert scan("有怪獸出沒 形如犬而大").matched

    def test_finds_the_same_event_in_korean_translation(self):
        match = scan("함경도에서 괴이한 짐승이 나타났는데 개와 비슷하나 크고")
        assert match.matched
        assert "괴물·요괴" in match.topics

    def test_routine_personnel_records_are_filtered_out(self):
        # 실록 기사 대부분은 인사 발령이라 영상으로 만들 게 없다.
        match = scan("以李某爲吏曹判書 金某除授承旨 遞差前職 謝恩肅拜")
        assert not match.matched
        assert match.is_routine

    def test_an_article_hitting_several_topics_scores_higher(self):
        one = scan("夜 彗星見于東方")
        many = scan("世子暴薨 中外疑其鴆毒 鞫問內人 告變")
        assert many.score > one.score

    def test_repeating_one_word_does_not_inflate_the_score(self):
        # 같은 말이 열 번 나온다고 열 배 센 사건은 아니다.
        once = scan("彗星")
        many = scan("彗星 " * 20)
        assert many.score <= once.score * 3 + 0.01

    def test_empty_text_matches_nothing(self):
        assert not scan("").matched
        assert scan("").score == 0.0


class TestKhsClient:
    def test_extract_items_reads_nested_item_tags_from_real_response_shape(self):
        # khs.go.kr 를 실제로 호출해 확인한 응답 구조(직접 호출 결과 캡처)를 그대로 본떴다:
        # 인증키 없이도 <result><body><items><item>...</item></items></body></result> 형태의
        # XML로 온다. DataGoKrClient(JSON 전제)로는 이 응답을 전부 오류로 처리해버려 별도
        # 클라이언트가 필요했다.
        sample = """<?xml version="1.0" encoding="UTF-8"?>
        <result>
          <header><resultCode>0000</resultCode><resultMsg>NORMAL SERVICE</resultMsg></header>
          <body>
            <items>
              <item>
                <ccbaMnm1>서울 숭례문</ccbaMnm1>
                <ccbaKdcd>11</ccbaKdcd>
                <ccbaAsno>0000010000000</ccbaAsno>
                <ccbaCtcd>11</ccbaCtcd>
                <ccbaCtcdNm>서울</ccbaCtcdNm>
              </item>
              <item>
                <ccbaMnm1>원각사지 십층석탑</ccbaMnm1>
                <ccbaKdcd>11</ccbaKdcd>
                <ccbaAsno>0000020000000</ccbaAsno>
                <ccbaCtcd>11</ccbaCtcd>
              </item>
            </items>
            <totalCnt>163</totalCnt>
          </body>
        </result>""".encode("utf-8")

        items = _extract_items(sample)

        assert len(items) == 2
        assert items[0]["ccbaMnm1"] == "서울 숭례문"
        assert items[0]["ccbaCtcdNm"] == "서울"
        assert items[1]["ccbaMnm1"] == "원각사지 십층석탑"

    def test_extract_items_returns_empty_list_when_no_items(self):
        sample = b"<result><body><items/><totalCnt>0</totalCnt></body></result>"
        assert _extract_items(sample) == []


class TestImageCollectorPriorityPool:
    def test_official_photos_are_tried_before_the_source_chain(self, tmp_path, monkeypatch):
        official = PhotoResult(url="http://example.com/official.jpg", source_name="국가유산청")
        collector = ImageCollector([], tmp_path, priority_images=[official])
        monkeypatch.setattr(collector, "_download", lambda photo, label: tmp_path / "fake.jpg")

        result = collector.collect("아무 검색어", "")

        assert result is not None
        _, credit = result
        assert credit.source_name == "국가유산청"

    def test_falls_back_to_source_chain_once_pool_is_empty(self, tmp_path, monkeypatch):
        collector = ImageCollector([], tmp_path, priority_images=[])
        monkeypatch.setattr(collector, "_download", lambda photo, label: None)

        assert collector.collect("빈 풀", "") is None
        assert "빈 풀" in collector.failed_queries


class TestTelegramReviewerMultiWait:
    def test_a_callback_for_one_task_does_not_get_consumed_while_waiting_for_another(self):
        # 회차 두 개가 동시에 대기 중일 때, task2 콜백이 먼저 도착해도 task1 을 기다리다
        # 놓치면 안 된다 — wait_for_decision 을 따로따로 부르면 이게 실제로 깨졌었다.
        reviewer = TelegramReviewer("token", "chat")
        updates = {
            "result": [
                {
                    "update_id": 1,
                    "callback_query": {"id": "q1", "data": "task2:a0"},
                },
            ]
        }
        with patch.object(reviewer, "_call", return_value=updates) as mock_call:
            decisions = reviewer.wait_for_decisions(["task1", "task2"], timeout_seconds=1)

        assert "task2" in decisions
        assert decisions["task2"].approved is True
        assert "task1" not in decisions
        # answerCallbackQuery 가 실제로 불렸는지도 확인한다.
        assert any(call.args[0] == "answerCallbackQuery" for call in mock_call.call_args_list)

    def test_approval_still_records_when_answer_callback_query_fails(self):
        # 실제로 있었던 사고: 버튼을 눌렀는데 그 콜백을 폴링이 한참 뒤에야 집어서
        # 텔레그램이 "query is too old"로 answerCallbackQuery 를 거부했다. 그
        # 예외가 그대로 올라가면 뒤에 오는 resolved[matched] = ... 가 실행조차
        # 안 돼 승인 자체가 통째로 사라진다("승인을 눌렀는데 업로드가 안 됐다").
        # answerCallbackQuery 는 로딩 스피너를 꺼주는 UI 피드백일 뿐이라, 이게
        # 실패해도 승인 판정은 그대로 기록돼야 한다.
        reviewer = TelegramReviewer("token", "chat")
        updates = {
            "result": [
                {"update_id": 1, "callback_query": {"id": "q1", "data": "task1:a2"}},
            ]
        }

        def fake_call(method, payload, timeout=30):
            if method == "answerCallbackQuery":
                raise requests.exceptions.HTTPError("400 query is too old")
            return updates

        with patch.object(reviewer, "_call", side_effect=fake_call):
            decisions = reviewer.wait_for_decisions(["task1"], timeout_seconds=1)

        assert "task1" in decisions
        assert decisions["task1"].approved is True
        assert decisions["task1"].title_index == 2


class TestResumePendingReviews:
    def test_timeout_without_response_stays_pending_not_rejected(self, tmp_path):
        # 실제로 있었던 사고: 초기 대기(review_wait_seconds) 안에 응답이 없으면
        # "거절"과 똑같이 status 를 'rejected'(종결)로 박아버렸다. 그러면 사람이
        # 나중에라도 승인 버튼을 눌러도, review-pending 은 'pending'/'decided' 만
        # 다시 보므로 그 클릭이 조용히 무시됐다("승인했는데 유튜브 안 올라갔다").
        # 응답이 없을 뿐이면(decision=None) status 는 'pending' 그대로 남아
        # 다음 review-pending 확인이 다시 물어봐야 한다.
        from src.review.telegram_bot import TelegramReviewer

        fake_db = tmp_path / "state.sqlite3"
        history.save_pending(fake_db, "task-timeout", {"topic_key": "disaster:x", "titles": ["제목"]})

        reviewer = MagicMock(spec=TelegramReviewer)
        with patch.object(pipeline, "STATE_DB", fake_db):
            result = pipeline._finish_review(
                MagicMock(), reviewer, "task-timeout", False, [], ["제목"], None
            )

        assert result == []
        actionable = history.list_actionable_reviews(fake_db)
        assert [row["task_id"] for row in actionable] == ["task-timeout"]
        assert actionable[0]["status"] == "pending"

    def test_explicit_reject_click_is_terminal(self, tmp_path):
        from src.review.telegram_bot import ReviewDecision, TelegramReviewer

        fake_db = tmp_path / "state.sqlite3"
        history.save_pending(fake_db, "task-rejected", {"topic_key": "disaster:x", "titles": ["제목"]})

        reviewer = MagicMock(spec=TelegramReviewer)
        with patch.object(pipeline, "STATE_DB", fake_db):
            pipeline._finish_review(
                MagicMock(), reviewer, "task-rejected", False, [],
                ["제목"], ReviewDecision(approved=False),
            )

        # 실제 거절이면 다시 물어볼 필요가 없다 — actionable 목록에서 빠진다.
        assert history.list_actionable_reviews(fake_db) == []

    def test_resolved_approval_reaches_upload_and_clears_pending_status(self, tmp_path):
        task_id = "test-task-1"
        part_dir = tmp_path / "output" / task_id
        part_dir.mkdir(parents=True)
        for part_num in (1, 2):
            (part_dir / f"part{part_num}.mp4").write_bytes(b"fake")
            (part_dir / f"part{part_num}_thumb.jpg").write_bytes(b"fake")

        result_json = {
            "task_id": task_id,
            "topic_key": "heritage:테스트",
            "is_sample": False,
            "parts": [
                {
                    "part": 1,
                    "video": str(part_dir / "part1.mp4"),
                    "thumbnail": str(part_dir / "part1_thumb.jpg"),
                    "duration": 30.0,
                    "title": "테스트 제목",
                    "title_alternatives": ["대안1"],
                    "description": "설명",
                    "tags": ["tag1"],
                },
                {
                    "part": 2,
                    "video": str(part_dir / "part2.mp4"),
                    "thumbnail": str(part_dir / "part2_thumb.jpg"),
                    "duration": 30.0,
                    "title": "테스트 제목 (2부)",
                    "title_alternatives": [],
                    "description": "설명2",
                    "tags": ["tag1"],
                },
            ],
        }
        (part_dir / "result.json").write_text(
            json.dumps(result_json, ensure_ascii=False), encoding="utf-8"
        )

        fake_db = tmp_path / "state.sqlite3"
        history.save_pending(
            fake_db, task_id, {"topic_key": "heritage:테스트", "titles": ["테스트 제목", "대안1"]}
        )

        settings = MagicMock()
        settings.secrets.telegram_bot_token = "x"
        settings.secrets.telegram_chat_id = "y"
        settings.get.side_effect = lambda key, default=None: default

        with patch.object(pipeline, "STATE_DB", fake_db), patch.object(
            pipeline, "OUTPUT_DIR", tmp_path / "output"
        ), patch("src.review.telegram_bot.TelegramReviewer") as MockReviewer, patch(
            "src.publish.youtube_uploader.get_service", return_value=object()
        ), patch(
            "src.publish.youtube_uploader.upload_series", return_value=["vid1", "vid2"]
        ):
            reviewer = MockReviewer.return_value
            reviewer.enabled = True
            reviewer.wait_for_decisions.return_value = {
                task_id: ReviewDecision(approved=True, title_index=0)
            }

            results = pipeline.resume_pending_reviews(settings, 5)

        assert results == {task_id: ["vid1", "vid2"]}
        assert history.list_pending_reviews(fake_db) == []

    def test_upload_failure_persists_decision_and_retries_without_asking_telegram_again(
        self, tmp_path
    ):
        # 실제로 있었던 사고: 승인은 받았는데(텔레그램 콜백 매칭까지 성공) 업로드가
        # 유튜브 API 오류로 실패하면서, 그 판정 자체가 다음 실행에서 사라져 있었다
        # (텔레그램 콜백은 한 번 받으면 다시 오지 않는다). 업로드가 실패해도 판정은
        # 'decided' 로 남아 있어야 하고, 다음 resume_pending_reviews 호출은 텔레그램에
        # 다시 묻지 않고 그 판정 그대로 업로드만 재시도해야 한다.
        task_id = "test-task-2"
        part_dir = tmp_path / "output" / task_id
        part_dir.mkdir(parents=True)
        (part_dir / "part1.mp4").write_bytes(b"fake")
        (part_dir / "part1_thumb.jpg").write_bytes(b"fake")
        result_json = {
            "task_id": task_id,
            "topic_key": "disaster:테스트",
            "is_sample": False,
            "parts": [
                {
                    "part": 1,
                    "video": str(part_dir / "part1.mp4"),
                    "thumbnail": str(part_dir / "part1_thumb.jpg"),
                    "duration": 60.0,
                    "title": "테스트 제목",
                    "title_alternatives": [],
                    "description": "설명",
                    "tags": [],
                },
            ],
        }
        (part_dir / "result.json").write_text(
            json.dumps(result_json, ensure_ascii=False), encoding="utf-8"
        )

        fake_db = tmp_path / "state.sqlite3"
        history.save_pending(fake_db, task_id, {"topic_key": "disaster:테스트", "titles": ["테스트 제목"]})

        settings = MagicMock()
        settings.secrets.telegram_bot_token = "x"
        settings.secrets.telegram_chat_id = "y"
        settings.get.side_effect = lambda key, default=None: default

        # 1회차: 승인은 받았지만 업로드가 API 오류로 실패한다.
        with patch.object(pipeline, "STATE_DB", fake_db), patch.object(
            pipeline, "OUTPUT_DIR", tmp_path / "output"
        ), patch("src.review.telegram_bot.TelegramReviewer") as MockReviewer, patch(
            "src.publish.youtube_uploader.get_service", return_value=object()
        ), patch(
            "src.publish.youtube_uploader.upload_series",
            side_effect=RuntimeError("YouTube Data API v3 has not been used"),
        ):
            reviewer = MockReviewer.return_value
            reviewer.enabled = True
            reviewer.wait_for_decisions.return_value = {
                task_id: ReviewDecision(approved=True, title_index=0)
            }
            # 한 회차의 업로드가 깨져도 resume_pending_reviews 자체는 예외를 삼키고
            # 계속 진행한다(배치 안 다른 회차가 같이 멈추면 안 되므로) — 이 회차의
            # 결과만 빈 리스트로 남고, 판정(decided)은 그대로 보존돼 재시도된다.
            results = pipeline.resume_pending_reviews(settings, 5)
        assert results == {task_id: []}

        # 판정은 'decided' 로 남아야 하고, 다음 실행이 볼 수 있어야 한다.
        actionable = history.list_actionable_reviews(fake_db)
        assert len(actionable) == 1
        assert actionable[0]["status"] == "decided"
        assert actionable[0]["payload"]["decision"] == {"approved": True, "title_index": 0}

        # 2회차: 업로드가 이번엔 성공한다 — 텔레그램에 다시 묻지 않고도 끝나야 한다.
        with patch.object(pipeline, "STATE_DB", fake_db), patch.object(
            pipeline, "OUTPUT_DIR", tmp_path / "output"
        ), patch("src.review.telegram_bot.TelegramReviewer") as MockReviewer, patch(
            "src.publish.youtube_uploader.get_service", return_value=object()
        ), patch(
            "src.publish.youtube_uploader.upload_series", return_value=["vid1"]
        ):
            reviewer = MockReviewer.return_value
            reviewer.enabled = True
            results = pipeline.resume_pending_reviews(settings, 5)

        assert results == {task_id: ["vid1"]}
        # 텔레그램에 다시 묻지 않았다는 게 핵심이다.
        reviewer.wait_for_decisions.assert_not_called()
        assert history.list_actionable_reviews(fake_db) == []


class TestSillokArticle:
    """실록 기사 페이지 파싱. 실제 응답을 잘라 픽스처로 넣어뒀다."""

    @staticmethod
    def _article():
        html = (
            Path(__file__).parent / "fixtures" / "sillok_kob_10109025_003.html"
        ).read_text(encoding="utf-8")
        return sillok_article.parse("kob_10109025_003", html)

    def test_국역과_원문을_각각_뽑는다(self):
        article = self._article()
        assert article.translation.startswith("강원 감사 이형욱(李馨郁)이 치계하였다.")
        assert "杆城郡" in article.original
        assert article.title.endswith("이형욱이 치계하다")
        assert article.categories == ["과학-천기(天氣)"]

    def test_인명을_감싼_태그를_지워도_조사가_붙어_있다(self):
        # <a>이형욱(李馨郁)</a>이 → "이형욱(李馨郁) 이" 로 벌어지면 읽을 때 어색해진다.
        assert "이형욱(李馨郁)이" in self._article().translation

    def test_서지_저작권_표시는_본문에서_뺀다(self):
        article = self._article()
        assert "정족산사고본" not in article.translation
        assert "한국고전번역원" not in article.translation

    def test_화면에_띄울_날짜는_짧게_줄인다(self):
        assert self._article().article_id == "kob_10109025_003"
        assert sillok_article.to_candidate(self._article()).headline_number == (
            "광해 1년 9월 25일 (1609년)"
        )

    def test_후보로_옮기면_사건_점수가_붙는다(self):
        candidate = sillok_article.to_candidate(self._article())
        assert candidate.dataset == "sillok"
        assert candidate.topic_key == "sillok:kob_10109025_003"
        # 하늘의 이변 그물에 걸려야 한다 — 처음엔 하나도 안 걸려서 키워드를 보강했다.
        assert candidate.features["event_score"] > 0.5
        assert candidate.facts
        assert candidate.image_queries_ko

    def test_국역이_없으면_조용히_넘어가지_않는다(self):
        with pytest.raises(sillok_article.SillokError):
            sillok_article.parse("x", "<div class='detail-view'></div>")


class TestSpendGuard:
    """크레딧이 바닥나면 호출 전에 멈춰야 한다 — 바닥난 뒤의 호출은 카드로 나간다."""

    def test_남은_금액_안에서는_통과한다(self, tmp_path):
        guard = SpendGuard(tmp_path / "ledger.json", credit_krw=10_000, reserve_krw=1_000)
        guard.ensure_can_spend(5_000)
        guard.record(5_000, "veo", "테스트")
        assert guard.spent_krw == 5_000
        assert guard.remaining_krw == 4_000

    def test_남은_금액을_넘으면_예외를_던진다(self, tmp_path):
        guard = SpendGuard(tmp_path / "ledger.json", credit_krw=10_000, reserve_krw=1_000)
        guard.record(8_000, "veo", "테스트")
        with pytest.raises(CreditsExhausted):
            guard.ensure_can_spend(2_000)

    def test_예비금은_아예_쓰지_않는다(self, tmp_path):
        guard = SpendGuard(tmp_path / "ledger.json", credit_krw=10_000, reserve_krw=3_000)
        assert guard.usable_krw == 7_000
        with pytest.raises(CreditsExhausted):
            guard.ensure_can_spend(7_001)

    def test_원장은_재실행해도_이어진다(self, tmp_path):
        ledger = tmp_path / "ledger.json"
        SpendGuard(ledger, credit_krw=10_000).record(1_500, "image", "1장")
        assert SpendGuard(ledger, credit_krw=10_000).spent_krw == 1_500

    def test_상한을_끄면_막지_않는다(self, tmp_path):
        guard = SpendGuard(tmp_path / "ledger.json", credit_krw=0, enabled=False)
        guard.ensure_can_spend(999_999)

class TestTelegramPollingResilience:
    def test_get_updates_transient_failure_is_retried_not_raised(self):
        # 실제로 있었던 사고: 텔레그램 롱폴 timeout(50초)이 caller 의 wait_seconds
        # (예: review-pending 의 --wait 30)보다 길어서 요청 하나가 deadline 을
        # 넘겨 끝나고, 그 경계에서 ReadTimeout 이 나며 프로세스 전체가 죽었다.
        # getUpdates 하나가 실패해도 루프는 계속 돌아야 한다.
        reviewer = TelegramReviewer("token", "chat")
        get_updates_calls = {"n": 0}

        def fake_call(method, payload, timeout=30):
            if method != "getUpdates":
                return {}  # answerCallbackQuery 등은 그냥 성공 처리
            get_updates_calls["n"] += 1
            if get_updates_calls["n"] == 1:
                raise requests.exceptions.ReadTimeout("read timed out")
            return {"result": [{"update_id": 1, "callback_query": {"id": "q1", "data": "task1:a0"}}]}

        with patch.object(reviewer, "_call", side_effect=fake_call):
            decisions = reviewer.wait_for_decisions(["task1"], timeout_seconds=5)

        assert get_updates_calls["n"] == 2  # 첫 실패 후 다시 시도해 성공했다
        assert decisions["task1"].approved is True

    def test_get_updates_poll_timeout_capped_to_remaining_deadline(self):
        # 남은 시간이 POLL_TIMEOUT(50초)보다 짧으면, 텔레그램에 요청하는
        # "timeout" 값도 그 남은 시간 안으로 잘려야 한다 — 안 그러면 요청 하나가
        # 전체 wait_seconds 를 그대로 넘겨버린다.
        reviewer = TelegramReviewer("token", "chat")
        seen_timeouts = []

        def fake_call(method, payload, timeout=30):
            if method == "getUpdates":
                seen_timeouts.append(payload["timeout"])
            return {"result": []}

        with patch.object(reviewer, "_call", side_effect=fake_call):
            reviewer.wait_for_decisions(["task1"], timeout_seconds=5)

        assert seen_timeouts  # 최소 한 번은 불렸다
        assert all(t <= 5 for t in seen_timeouts)


class TestTelegramVideoSizeFallback:
    def test_small_file_sent_as_is_without_compression(self, tmp_path):
        video = tmp_path / "part1.mp4"
        video.write_bytes(b"x" * 1024)  # 48MB 훨씬 아래
        reviewer = TelegramReviewer("token", "chat")

        with patch.object(reviewer.session, "post") as mock_post, patch(
            "src.review.telegram_bot._compress_for_telegram"
        ) as mock_compress:
            mock_post.return_value = MagicMock(ok=True)
            reviewer._send_video(video, caption="1부")

        mock_compress.assert_not_called()
        assert mock_post.call_args.kwargs["data"]["caption"] == "1부"

    def test_oversized_file_is_compressed_before_sending_and_cleaned_up(self, tmp_path):
        # 실제로 있었던 사고: 합본 영상이 길어져 50MB 를 넘으면 텔레그램이
        # 413(Request Entity Too Large)으로 거부해 검수 자체가 불가능해졌다
        # ("텔레그램 왔는데 영상은 안 옴" — 버튼 메시지만 가고 영상은 안 갔다).
        # 크기 제한을 넘으면 압축본을 대신 보내고, 원본은 그대로 손대지 않는다.
        video = tmp_path / "part1.mp4"
        video.write_bytes(b"x" * (49 * 1024 * 1024))  # 48MB 상한 초과
        compressed = tmp_path / "part1_tg.mp4"
        compressed.write_bytes(b"y" * 1024)
        reviewer = TelegramReviewer("token", "chat")

        with patch.object(reviewer.session, "post") as mock_post, patch(
            "src.review.telegram_bot._compress_for_telegram", return_value=compressed
        ) as mock_compress:
            mock_post.return_value = MagicMock(ok=True)
            reviewer._send_video(video, caption="1부")

        mock_compress.assert_called_once_with(video)
        assert "저화질" in mock_post.call_args.kwargs["data"]["caption"]
        assert video.exists()  # 원본은 그대로
        assert not compressed.exists()  # 전송 후 압축본은 정리된다

    def test_falls_back_to_original_when_compression_fails(self, tmp_path):
        video = tmp_path / "part1.mp4"
        video.write_bytes(b"x" * (49 * 1024 * 1024))
        reviewer = TelegramReviewer("token", "chat")

        with patch.object(reviewer.session, "post") as mock_post, patch(
            "src.review.telegram_bot._compress_for_telegram", return_value=None
        ):
            mock_post.return_value = MagicMock(ok=True)
            reviewer._send_video(video, caption="1부")

        # 압축이 실패해도 원본으로라도 전송을 시도한다.
        assert mock_post.called
        assert video.exists()


def _make_script_part(part_num: int, hook: str, texts: list[str]):
    return ScriptPart(
        part=part_num,
        hook_number=hook,
        lines=[ScriptLine(text=t, visual_query_ko="a", duration=2.0) for t in texts],
    )


def test_build_summary_appends_korean_titles_to_same_bubble():
    from src.review.telegram_bot import build_summary

    text = build_summary(
        "제목", ["제목1", "제목2"], is_sample=False, titles_ko=["한글제목1", "한글제목2"],
    )
    assert "제목 후보(한국어 번역)" in text
    assert "한글제목1" in text
    assert "한글제목2" in text


def test_build_summary_omits_korean_section_when_absent():
    from src.review.telegram_bot import build_summary

    text = build_summary("제목", ["제목1"], is_sample=False, titles_ko=None)
    assert "제목 후보(한국어 번역)" not in text


class TestReviewSummarySplitsInfoLines:
    def _make_result(self, tmp_path, credits):
        from src.models import (
            PhotoCredit,
            RenderedPart,
            Script,
            ScriptPart,
            StoryCandidate,
            VideoMetadata,
        )

        video = tmp_path / "part1.mp4"
        video.write_bytes(b"fake")
        thumb = tmp_path / "part1_thumb.jpg"
        thumb.write_bytes(b"fake")

        metadata = VideoMetadata(
            title="제목",
            title_alternatives=["대안"],
            description="후킹 설명\n\n출처: 위키백과",
            tags=["#3d"],
            hook_description="후킹 설명",
            source_line="출처: 위키백과",
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
            headline_number="", subject="사건", context="", source_name="위키백과",
        )
        return pipeline.PipelineResult(
            task_id="task-x", candidate=candidate, script=script, parts=[part],
            work_dir=tmp_path, credits=credits, veo_clips=7,
            cost_note="Veo lite 7클립 · 약 2,058원",
        )

    def test_cost_sent_as_separate_message_not_in_summary(self, tmp_path):
        # 사용자 지시(2026-10-11): 설명/캡션은 승인 "이후"에만 보낸다 — 승인
        # 전(카드 발송 시점)에 notify() 로 나가는 건 비용 한 줄뿐이다.
        from src.models import PhotoCredit

        credits = [PhotoCredit(query="q", source_name="AI 생성 영상"),
                   PhotoCredit(query="q2", source_name="AI 생성 이미지")]
        result = self._make_result(tmp_path, credits)

        settings = MagicMock()
        settings.secrets.telegram_bot_token = "x"
        settings.secrets.telegram_chat_id = "y"

        with patch.object(pipeline, "STATE_DB", tmp_path / "state.sqlite3"), patch(
            "src.review.telegram_bot.TelegramReviewer"
        ) as MockReviewer, patch(
            "src.review.telegram_bot.build_summary"
        ) as mock_build_summary:
            reviewer = MockReviewer.return_value
            reviewer.enabled = True
            reviewer.wait_for_decision.return_value = None
            mock_build_summary.return_value = "카드 본문"

            pipeline.review_and_publish(settings, result, wait_seconds=1)

        # 카드 본문(build_summary)에는 제목 후보(+한국어 번역)만 들어간다.
        summary_call = mock_build_summary.call_args
        assert summary_call.args[1] == ["제목", "대안"]

        notified = [call.args[0] for call in reviewer.notify.call_args_list]
        assert not any("후킹 설명" in n for n in notified)
        assert not any("출처" in n or "Fuente" in n for n in notified)
        assert not any("AI 생성 영상" in n or "AI 생성 이미지" in n for n in notified)
        assert any("Veo lite 7클립" in n for n in notified)

    def test_title_candidates_korean_translation_passed_to_summary_when_present(self, tmp_path):
        # 사용자 지시(2026-10-11): 제목 후보 한국어 번역은 별도 말풍선이 아니라
        # 승인 요청(build_summary)과 같은 말풍선으로 합쳐 보낸다.
        result = self._make_result(tmp_path, credits=[])
        metadata = result.parts[0].metadata
        metadata.title_ko = "타이타닉, 치명적 설계 결함"
        metadata.title_alternatives_ko = ["1,514명을 앗아간 결함"]

        settings = MagicMock()
        settings.secrets.telegram_bot_token = "x"
        settings.secrets.telegram_chat_id = "y"

        with patch.object(pipeline, "STATE_DB", tmp_path / "state.sqlite3"), patch(
            "src.review.telegram_bot.TelegramReviewer"
        ) as MockReviewer, patch(
            "src.review.telegram_bot.build_summary"
        ) as mock_build_summary:
            reviewer = MockReviewer.return_value
            reviewer.enabled = True
            reviewer.wait_for_decision.return_value = None

            pipeline.review_and_publish(settings, result, wait_seconds=1)

        summary_call = mock_build_summary.call_args
        assert summary_call.args[3] == [
            "타이타닉, 치명적 설계 결함", "1,514명을 앗아간 결함",
        ]

    def test_title_candidates_korean_translation_empty_when_absent(self, tmp_path):
        result = self._make_result(tmp_path, credits=[])

        settings = MagicMock()
        settings.secrets.telegram_bot_token = "x"
        settings.secrets.telegram_chat_id = "y"

        with patch.object(pipeline, "STATE_DB", tmp_path / "state.sqlite3"), patch(
            "src.review.telegram_bot.TelegramReviewer"
        ) as MockReviewer, patch(
            "src.review.telegram_bot.build_summary"
        ) as mock_build_summary:
            reviewer = MockReviewer.return_value
            reviewer.enabled = True
            reviewer.wait_for_decision.return_value = None

            pipeline.review_and_publish(settings, result, wait_seconds=1)

        summary_call = mock_build_summary.call_args
        assert summary_call.args[3] == []

    def test_approved_title_and_caption_sent_right_after_decision(self, tmp_path):
        # 사용자 지시(2026-10-11): 승인 접수 직후 선택된 제목(스페인어만) 한
        # 줄과, 설명+해시태그 5개를 라벨 없이 바로 복붙 가능한 형태로 보낸다.
        result = self._make_result(tmp_path, credits=[])

        settings = MagicMock()
        settings.secrets.telegram_bot_token = "x"
        settings.secrets.telegram_chat_id = "y"
        settings.get.side_effect = lambda key, default=None: default

        with patch.object(pipeline, "STATE_DB", tmp_path / "state.sqlite3"), patch(
            "src.review.telegram_bot.TelegramReviewer"
        ) as MockReviewer, patch("src.review.telegram_bot.build_summary"), patch(
            "src.publish.youtube_uploader.get_service", return_value=object()
        ), patch(
            "src.publish.youtube_uploader.upload_series", return_value=["vid1"]
        ):
            reviewer = MockReviewer.return_value
            reviewer.enabled = True
            reviewer.wait_for_decision.return_value = ReviewDecision(
                approved=True, title_index=0
            )

            pipeline.review_and_publish(settings, result, wait_seconds=1)

        notified = [call.args[0] for call in reviewer.notify.call_args_list]
        assert "제목" in notified
        caption = next(n for n in notified if "후킹 설명" in n)
        assert "#historia" in caption
        assert "#desastres" in caption
        assert "#catastrofes" in caption
        assert "#misterio" in caption
        assert "#datoscuriosos" in caption
        # 유튜브용 12개 해시태그 목록은 안 섞여 들어간다(텔레그램 전용 5개만).
        assert "#shorts" not in caption
        assert "#terror" not in caption

    def test_single_video_upload_completion_has_no_part_label(self, tmp_path):
        # 사용자 지시: 통합본은 하나의 영상으로 종결이다. 업로드 완료 메시지가
        # "1부: <url>" 이라고 쓰면 없는 2부가 있는 것처럼 보인다.
        task_id = "task-single"
        part_dir = tmp_path / "output" / task_id
        part_dir.mkdir(parents=True)
        (part_dir / "part1.mp4").write_bytes(b"fake")
        (part_dir / "part1_thumb.jpg").write_bytes(b"fake")
        result_json = {
            "task_id": task_id,
            "topic_key": "disaster:테스트",
            "is_sample": False,
            "parts": [
                {
                    "part": 1,
                    "video": str(part_dir / "part1.mp4"),
                    "thumbnail": str(part_dir / "part1_thumb.jpg"),
                    "duration": 80.0,
                    "title": "테스트 제목",
                    "title_alternatives": [],
                    "description": "설명",
                    "tags": [],
                },
            ],
        }
        (part_dir / "result.json").write_text(
            json.dumps(result_json, ensure_ascii=False), encoding="utf-8"
        )

        fake_db = tmp_path / "state.sqlite3"
        history.save_pending(fake_db, task_id, {"topic_key": "disaster:테스트", "titles": ["테스트 제목"]})

        settings = MagicMock()
        settings.secrets.telegram_bot_token = "x"
        settings.secrets.telegram_chat_id = "y"
        settings.get.side_effect = lambda key, default=None: default

        with patch.object(pipeline, "STATE_DB", fake_db), patch.object(
            pipeline, "OUTPUT_DIR", tmp_path / "output"
        ), patch("src.review.telegram_bot.TelegramReviewer") as MockReviewer, patch(
            "src.publish.youtube_uploader.get_service", return_value=object()
        ), patch(
            "src.publish.youtube_uploader.upload_series", return_value=["vid1"]
        ):
            reviewer = MockReviewer.return_value
            reviewer.enabled = True
            reviewer.wait_for_decisions.return_value = {
                task_id: ReviewDecision(approved=True, title_index=0)
            }
            pipeline.resume_pending_reviews(settings, 5)

        completion = [
            call.args[0] for call in reviewer.notify.call_args_list if "업로드 완료" in call.args[0]
        ][0]
        assert "1부:" not in completion
        assert "https://www.youtube.com/watch?v=vid1" in completion


class TestAutoPublish:
    """2026-10-16: review.auto_publish 가 켜지면 승인 없이 바로 올리고, 텔레그램에는
    '업로드 완료' → 영상 → 설명(+해시태그)만 보낸다."""

    def _result(self, tmp_path):
        return TestReviewSummarySplitsInfoLines()._make_result(tmp_path, [])

    def _settings(self):
        settings = MagicMock()
        settings.secrets.telegram_bot_token = "x"
        settings.secrets.telegram_chat_id = "y"
        settings.get.side_effect = lambda key, default=None: (
            True if key == "review.auto_publish" else default
        )
        return settings

    def test_uploads_without_waiting_and_sends_only_notice_video_description(self, tmp_path):
        result = self._result(tmp_path)

        with patch.object(pipeline, "STATE_DB", tmp_path / "state.sqlite3"), patch(
            "src.review.telegram_bot.TelegramReviewer"
        ) as MockReviewer, patch(
            "src.publish.youtube_uploader.get_service", return_value=object()
        ), patch(
            "src.publish.youtube_uploader.upload_series", return_value=["vid1"]
        ), patch.object(pipeline, "_upload_to_extra_platforms", return_value=[]):
            reviewer = MockReviewer.return_value
            reviewer.enabled = True
            ids = pipeline.review_and_publish(self._settings(), result, wait_seconds=1)

        assert ids == ["vid1"]
        reviewer.send_review.assert_not_called()
        reviewer.wait_for_decision.assert_not_called()

        notified = [call.args[0] for call in reviewer.notify.call_args_list]
        assert len(notified) == 2
        assert notified[0].startswith("업로드 완료") and "vid1" in notified[0]
        assert "후킹 설명" in notified[1] and "#" in notified[1]  # 설명 + 해시태그
        reviewer.send_video.assert_called_once()
        assert history.list_actionable_reviews(tmp_path / "state.sqlite3") == []

    def test_upload_failure_notifies_and_stays_retryable(self, tmp_path):
        result = self._result(tmp_path)
        db = tmp_path / "state.sqlite3"

        with patch.object(pipeline, "STATE_DB", db), patch(
            "src.review.telegram_bot.TelegramReviewer"
        ) as MockReviewer, patch(
            "src.publish.youtube_uploader.get_service", return_value=object()
        ), patch(
            "src.publish.youtube_uploader.upload_series", side_effect=RuntimeError("quota")
        ):
            reviewer = MockReviewer.return_value
            reviewer.enabled = True
            with pytest.raises(RuntimeError):
                pipeline.review_and_publish(self._settings(), result, wait_seconds=1)

        assert any("실패" in call.args[0] for call in reviewer.notify.call_args_list)
        # 판정이 남아 있어 review-pending 이 같은 판정으로 업로드만 재시도한다.
        assert [r["status"] for r in history.list_actionable_reviews(db)] == ["decided"]


class TestSpendGuardBaseline:
    """2026-10-19: 콘솔의 실제 잔액을 기준으로 장부를 다시 맞춘다."""

    def test_baseline_counts_only_entries_after_it(self, tmp_path):
        import json

        from src.cloud.spend_guard import SpendGuard

        ledger = tmp_path / "ledger.json"
        ledger.write_text(json.dumps({
            "total_krw": 150_000,
            "entries": [
                {"when": "2026-10-18T10:00:00", "kind": "veo", "krw": 100_000, "note": ""},
                {"when": "2026-10-19T03:00:00", "kind": "veo", "krw": 10_000, "note": ""},
                {"when": "2026-10-19T04:00:00", "kind": "veo", "krw": 40_000, "note": ""},
            ],
        }), encoding="utf-8")

        guard = SpendGuard(ledger, credit_krw=91_000, reserve_krw=20_000,
                           baseline_at="2026-10-19T00:00:00")
        assert guard.spent_krw == 50_000          # 10/18 분은 콘솔 잔액에 이미 반영됨
        assert guard.remaining_krw == 91_000 - 20_000 - 50_000

        legacy = SpendGuard(ledger, credit_krw=354_417, reserve_krw=20_000)
        assert legacy.spent_krw == 150_000        # baseline 없으면 예전처럼 누적 전체

    def test_build_spend_guard_uses_console_balance_minus_adjustment(self):
        settings = MagicMock()
        values = {
            "cloud.balance_krw": 124096, "cloud.balance_adjust_krw": 33000,
            "cloud.balance_as_of": "2026-10-19T00:00:00", "cloud.reserve_krw": 20000,
            "cloud.stop_when_exhausted": True, "cloud.credit_krw": 354417,
        }
        settings.get.side_effect = lambda key, default=None: values.get(key, default)

        guard = pipeline.build_spend_guard(settings)

        assert guard.credit_krw == 124096 - 33000
        assert guard.baseline_at == "2026-10-19T00:00:00"
