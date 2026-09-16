"""문화유산 야사 콘텐츠의 후보 수집 오케스트레이션.

pipeline.py 는 이 모듈의 `collect_candidates()` 하나만 부르면 된다. 내부에서
국가유산청 목록 → 국가유산청 상세(해설문) → 관광공사 overview 보강 → 위키백과 미스터리
보강 순서로 후보를 채우고, 국가유산청 응답이 비면 픽스처로 폴백한다.

국가유산청(khs.go.kr) 목록/상세/이미지/동영상 API는 실제로 호출해본 결과 인증키가 필요
없다 — `DATA_GO_KR_SERVICE_KEY` 는 관광공사 overview 보강(2차 소스)에만 쓰인다. 없어도
국가유산청 해설문만으로는 동작한다.
"""

from __future__ import annotations

from loguru import logger

from ..datasource.datagokr import DataGoKrClient
from ..models import StoryCandidate
from . import fixtures
from .heritage_source import HeritageSource
from .story_scorer import extract_facts, story_density
from .tour_overview import TourOverviewSource
from .wikipedia_enrich import enrich as enrich_with_wikipedia

MIN_CONTEXT_CHARS = 200


def collect_candidates(client: DataGoKrClient, limit_per_query: int = 8) -> list[StoryCandidate]:
    heritage_source = HeritageSource()
    candidates = heritage_source.fetch(limit_per_query)

    if not candidates:
        logger.warning("[heritage] 국가유산청 응답이 비어 픽스처로 폴백합니다")
        return fixtures.load_fixture()

    candidates = [_with_detail(heritage_source, candidate) for candidate in candidates]

    # 관광공사 overview 보강은 여전히 DATA_GO_KR_SERVICE_KEY 가 있어야 한다.
    tour_overview = TourOverviewSource(client)
    for candidate in candidates:
        _enrich_thin_context(candidate, tour_overview)
        enrich_with_wikipedia(candidate)

    return candidates


def _with_detail(heritage_source: HeritageSource, candidate: StoryCandidate) -> StoryCandidate:
    """목록 API 에는 해설문이 없다 — 상세 API 를 한 번 더 불러 채운다."""
    detailed = heritage_source.fetch_detail(
        candidate.subject,
        candidate.headline_number,
        "",
        candidate.heritage_ccba_kdcd,
        candidate.heritage_ccba_asno,
        candidate.heritage_ccba_ctcd,
    )
    return detailed or candidate


def _enrich_thin_context(candidate: StoryCandidate, tour_overview: TourOverviewSource) -> None:
    """해설문이 너무 짧으면 관광공사 overview 로 보강한다."""
    if len(candidate.context) >= MIN_CONTEXT_CHARS:
        return

    overview = tour_overview.fetch_overview(candidate.subject)
    if not overview:
        return

    candidate.context = f"{candidate.context}\n\n{overview}".strip()
    candidate.facts = extract_facts(candidate.context)
    candidate.features["story_density"] = story_density(candidate.context)
