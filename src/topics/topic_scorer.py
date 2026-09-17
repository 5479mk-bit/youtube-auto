"""후보를 '이야깃거리' 기준으로 점수화해 이번 회차 주제 하나를 고른다.

점수 축은 넷이다.
- story_density: 해설문·위키 보강이 얼마나 두꺼운가 (풀어낼 이야기가 있는가)
- mystery_score: "전설/의문/사라진/논란" 같은 미스터리 키워드 밀도
- prestige: 지정 등급 규모감 (국보 > 보물 > 사적 > 시도유형문화재)
- freshness: 최근에 다룬 시대·지역과 겹치지 않는 정도
"""

from __future__ import annotations

from ..models import StoryCandidate

FEATURE_KEYS = ("story_density", "mystery_score", "prestige", "freshness")


def score_candidate(candidate: StoryCandidate, weights: dict[str, float]) -> float:
    """가중치에 적힌 축만 본다.

    소스마다 축이 다르다 — 문화재는 위 네 축, 실록은 event_score(사건성)가 중심이다.
    축 이름을 여기 고정해두면 실록 후보가 전부 0점이 되므로 weights 를 따라간다.
    """
    keys = tuple(weights) or FEATURE_KEYS
    return sum(
        weights.get(key, 0.0) * min(max(candidate.features.get(key, 0.0), 0.0), 1.0)
        for key in keys
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
        # 해설문이든 위키 보강이든, 풀어낼 문장이 하나도 없으면 대본이 성립하지 않는다.
        if not candidate.facts and not candidate.wiki_excerpt:
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
