"""재난 제목 자동 발굴(2026-10-15, run #66 "선정할 주제 후보가 없습니다" 재발 방지)."""

from __future__ import annotations

import json
from unittest.mock import patch

from src.disasters import _looks_domestic, collect_candidates
from src.disasters import discovery


def test_is_event_title_filters_concepts_and_lists():
    assert discovery.is_event_title("1906년 샌프란시스코 지진")
    assert discovery.is_event_title("보팔 가스 누출 사고")
    assert not discovery.is_event_title("지진학자")
    assert not discovery.is_event_title("목록 지진")
    assert not discovery.is_event_title("분류:지진")
    assert not discovery.is_event_title("한국의 지진")
    assert not discovery.is_event_title("역사지진")
    assert discovery.is_event_title("간토 대지진")


def test_crawl_skips_korean_and_meta_categories():
    tree = {
        ("분류:루트", "page"): ["2010년 아이티 지진", "지진"],
        ("분류:루트", "subcat"): ["분류:대한민국의 재난", "분류:유럽의 재난", "분류:재난 영화"],
        ("분류:유럽의 재난", "page"): ["리스본 대지진"],
        ("분류:유럽의 재난", "subcat"): [],
    }
    visited = []

    def fake(category, member_type):
        visited.append(category)
        return tree.get((category, member_type), [])

    with patch.object(discovery, "_members", fake), patch.object(discovery.time, "sleep"):
        titles = discovery.crawl(["분류:루트"], max_depth=2)

    assert titles == ["2010년 아이티 지진", "리스본 대지진"]
    assert "분류:대한민국의 재난" not in visited
    assert "분류:재난 영화" not in visited


def test_crawl_survives_blocked_requests():
    with patch.object(discovery, "_members", return_value=None), patch.object(
        discovery.time, "sleep"
    ):
        assert discovery.crawl(["분류:루트"]) == []


def test_discover_titles_uses_fresh_cache_and_falls_back_to_stale(tmp_path):
    cache = tmp_path / "titles.json"
    cache.write_text(
        json.dumps({"fetched_at": 1000, "roots": ["a"], "titles": ["X 사고"]}),
        encoding="utf-8",
    )
    with patch.object(discovery, "crawl") as crawl:
        assert discovery.discover_titles(["a"], cache, now=1000 + 60) == ["X 사고"]
        crawl.assert_not_called()

    # 캐시가 낡았는데 크롤링이 막히면(빈 결과) 묵은 캐시라도 쓴다.
    with patch.object(discovery, "crawl", return_value=[]):
        stale = 1000 + discovery.CACHE_TTL_SECONDS + 1
        assert discovery.discover_titles(["a"], cache, now=stale) == ["X 사고"]


def test_shuffled_is_stable_per_seed():
    titles = [f"{i}년 사고" for i in range(20)]
    assert discovery.shuffled(titles, "2026-10-15") == discovery.shuffled(titles, "2026-10-15")
    assert discovery.shuffled(titles, "2026-10-15") != discovery.shuffled(titles, "2026-10-16")


def test_collect_candidates_uses_discovered_after_curated_and_caps_calls():
    texts = {"큐레이션 사고": "본문 " * 50, "발굴1 사고": "본문 " * 50, "발굴2 사고": "본문 " * 50}
    fetched = []

    def fake_fetch(title):
        fetched.append(title)
        return texts[title]

    with patch("src.disasters.wikipedia_enrich.fetch_full_text", fake_fetch), patch(
        "src.disasters.time.sleep"
    ):
        got = collect_candidates(
            ["큐레이션 사고"],
            used={"disaster:큐레이션 사고"},
            discovered=["발굴1 사고", "발굴2 사고"],
            max_candidates=1,
        )

    assert [c.headline for c in got] == ["발굴1 사고"]
    assert fetched == ["발굴1 사고"]  # 이미 쓴 제목·상한 이후는 호출하지 않는다


def test_discovered_domestic_incident_is_rejected_but_curated_is_not():
    domestic = "대한민국 서울특별시에서 발생한 사고이다. " * 5
    assert _looks_domestic(domestic)

    with patch("src.disasters.wikipedia_enrich.fetch_full_text", return_value=domestic), patch(
        "src.disasters.time.sleep"
    ):
        assert collect_candidates([], discovered=["국내 사고"]) == []
        assert len(collect_candidates(["국내 사고"])) == 1
