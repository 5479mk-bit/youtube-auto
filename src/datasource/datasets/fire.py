"""소방청 화재 통계 — "언제 가장 위험한가" 류의 시간대 이야기를 뽑는다.

데이터셋: https://www.data.go.kr/data/15055800/openapi.do
"""

from __future__ import annotations

from collections import Counter

from ...models import StoryCandidate
from ..base import series_from_pairs
from ..datagokr import DataGoKrClient

ENDPOINT = "/1661000/FireStatsService/getFireStats"


class FireAdapter:
    name = "fire"

    def fetch(self, client: DataGoKrClient, limit: int) -> list[StoryCandidate]:
        rows = list(client.paginate(ENDPOINT, {}, page_size=200, max_pages=5))

        by_cause: dict[str, Counter] = {}
        for row in rows:
            region = str(row.get("sidoNm") or row.get("sido") or "전국").strip()
            cause = str(row.get("fireCauseNm") or row.get("causeNm") or "").strip()
            if cause:
                by_cause.setdefault(region, Counter())[cause] += 1

        candidates: list[StoryCandidate] = []
        for region, counter in by_cause.items():
            top = counter.most_common(5)
            if len(top) < 3:
                continue
            leader, leader_count = top[0]
            total = sum(counter.values())
            share = leader_count / total * 100

            candidates.append(
                StoryCandidate(
                    dataset=self.name,
                    topic_key=f"fire:{region}",
                    headline=f"{region} 화재 원인 1위",
                    headline_number=f"{share:.0f}%",
                    subject=f"{region}에서 발생한 화재의 원인 분포",
                    context=(
                        f"{region}에서 집계된 화재 {total:,}건 중 원인 1위는 {leader}로 "
                        f"{leader_count:,}건({share:.1f}%)이다. 상위 원인: "
                        + ", ".join(f"{name} {count:,}건" for name, count in top)
                    ),
                    series=series_from_pairs([(name, float(count)) for name, count in top]),
                    series_unit="건",
                    source_name="소방청 화재통계",
                    source_url="https://www.data.go.kr/data/15055800/openapi.do",
                    image_queries_ko=[f"{region} 도심", "소방서", "아파트 단지"],
                    image_queries_en=[f"{region} city", "fire truck korea", "apartment building korea"],
                    features={
                        "personal_relevance": 0.8,
                        "surprise": min(share / 45, 1.0),
                        "magnitude": min(total / 10_000, 1.0),
                        "freshness": 0.7,
                    },
                )
            )

        candidates.sort(key=lambda c: c.features["surprise"], reverse=True)
        return candidates[:limit]
