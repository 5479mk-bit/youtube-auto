"""도로교통공단 교통사고 통계 — 지역/시간대별 사고 이야기를 뽑는다.

데이터셋: https://www.data.go.kr/data/15070282/openapi.do
"""

from __future__ import annotations

from ...models import StoryCandidate
from ..base import series_from_pairs, to_float
from ..datagokr import DataGoKrClient

ENDPOINT = "/B552061/frequentzoneLg/getRestFrequentzoneLg"


class TrafficAdapter:
    name = "traffic"

    def fetch(self, client: DataGoKrClient, limit: int) -> list[StoryCandidate]:
        rows = list(client.paginate(ENDPOINT, {}, page_size=100, max_pages=5))

        candidates: list[StoryCandidate] = []
        for row in rows:
            spot = str(row.get("spot_nm") or row.get("spotNm") or "").strip()
            accidents = to_float(row.get("occrrnc_cnt") or row.get("occrrncCnt"))
            casualties = to_float(row.get("caslt_cnt") or row.get("casltCnt"))
            deaths = to_float(row.get("dth_dnv_cnt") or row.get("dthDnvCnt"))
            if not spot or accidents <= 0:
                continue

            candidates.append(
                StoryCandidate(
                    dataset=self.name,
                    topic_key=f"traffic:{spot}",
                    headline=f"{spot} 사고 다발 구간",
                    headline_number=f"{accidents:,.0f}건",
                    subject=f"{spot}의 교통사고 집중 구간",
                    context=(
                        f"{spot} 구간에서 교통사고 {accidents:,.0f}건이 발생해 "
                        f"{casualties:,.0f}명이 다치고 {deaths:,.0f}명이 숨졌다."
                    ),
                    series=series_from_pairs(
                        [("사고", accidents), ("부상", casualties), ("사망", deaths)]
                    ),
                    series_unit="건",
                    source_name="도로교통공단 교통사고 다발지역",
                    source_url="https://www.data.go.kr/data/15070282/openapi.do",
                    image_queries_ko=[spot, f"{spot} 도로", "교차로"],
                    image_queries_en=[spot, "korean road intersection", "seoul traffic"],
                    features={
                        "personal_relevance": 0.75,
                        "surprise": min(accidents / 30, 1.0),
                        "magnitude": min(casualties / 50, 1.0),
                        "freshness": 0.6,
                    },
                )
            )

        candidates.sort(key=lambda c: c.features["magnitude"], reverse=True)
        return candidates[:limit]
