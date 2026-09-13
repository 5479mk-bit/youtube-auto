"""후보를 '감정 훅' 기준으로 점수화해 이번 회차 주제 하나를 고른다.

점수 축은 넷이다.
- personal_relevance: 시청자가 자기 얘기로 느끼는 정도 (내 동네·내 돈·내 안전)
- surprise: 숫자가 상식에서 벗어난 정도
- magnitude: 다루는 규모
- freshness: 데이터의 최신성
"""

from __future__ import annotations

from ..models import StoryCandidate

FEATURE_KEYS = ("personal_relevance", "surprise", "magnitude", "freshness")


def score_candidate(candidate: StoryCandidate, weights: dict[str, float]) -> float:
    return sum(
        weights.get(key, 0.0) * min(max(candidate.features.get(key, 0.0), 0.0), 1.0)
        for key in FEATURE_KEYS
    )


def rank(
    candidates: list[StoryCandidate],
    weights: dict[str, float],
    exclude_keys: set[str],
) -> list[StoryCandidate]:
    ranked: list[StoryCandidate] = []
    for candidate in candidates:
        if candidate.topic_key in exclude_keys:
            continue
        # 차트로 보여줄 게 없으면 데이터 다큐가 성립하지 않는다.
        if len(candidate.series) < 2:
            continue
        candidate.score = score_candidate(candidate, weights)
        ranked.append(candidate)

    ranked.sort(key=lambda c: c.score, reverse=True)
    return ranked


def pick(
    candidates: list[StoryCandidate],
    weights: dict[str, float],
    exclude_keys: set[str],
) -> StoryCandidate | None:
    ranked = rank(candidates, weights, exclude_keys)
    return ranked[0] if ranked else None
