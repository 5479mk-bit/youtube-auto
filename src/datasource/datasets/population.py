"""행정안전부 주민등록 인구통계 — 지역 소멸 속도 이야기를 뽑는다.

데이터셋: https://www.data.go.kr/data/15048556/openapi.do
활용 신청 후 상세 문서의 엔드포인트를 ENDPOINT 에 맞춰 조정한다.
"""

from __future__ import annotations

from ...models import StoryCandidate
from ..base import series_from_pairs, to_float
from ..datagokr import DataGoKrClient

ENDPOINT = "/1741000/admmSexdAgePpltn/getAdmmSexdAgePpltn"


class PopulationAdapter:
    name = "population"

    def fetch(self, client: DataGoKrClient, limit: int) -> list[StoryCandidate]:
        rows = list(client.paginate(ENDPOINT, {}, page_size=200, max_pages=3))

        by_region: dict[str, list[tuple[str, float]]] = {}
        for row in rows:
            region = str(row.get("admmNm") or row.get("regionNm") or "").strip()
            period = str(row.get("statsYm") or row.get("baseYm") or "").strip()
            population = to_float(row.get("totPpltnCo") or row.get("totalPopulation"))
            if region and period and population:
                by_region.setdefault(region, []).append((period, population))

        candidates: list[StoryCandidate] = []
        for region, points in by_region.items():
            points.sort()
            if len(points) < 2:
                continue
            first, last = points[0][1], points[-1][1]
            if first <= 0:
                continue
            change_pct = (last - first) / first * 100

            candidates.append(
                StoryCandidate(
                    dataset=self.name,
                    topic_key=f"population:{region}",
                    headline=f"{region} 인구 변화",
                    headline_number=f"{change_pct:+.1f}%",
                    subject=f"{region}의 인구 증감",
                    context=(
                        f"{region}의 주민등록 인구가 {points[0][0]} {first:,.0f}명에서 "
                        f"{points[-1][0]} {last:,.0f}명으로 {change_pct:+.1f}% 변했다."
                    ),
                    series=series_from_pairs(points[-6:]),
                    series_unit="명",
                    source_name="행정안전부 주민등록 인구통계",
                    source_url="https://jumin.mois.go.kr",
                    image_queries_ko=[region, f"{region} 전경", f"{region} 거리"],
                    image_queries_en=[region, "korean rural town", "empty village korea"],
                    features={
                        "personal_relevance": 0.9,
                        "surprise": min(abs(change_pct) / 20, 1.0),
                        "magnitude": min(abs(last - first) / 50_000, 1.0),
                        "freshness": 0.8,
                    },
                )
            )

        candidates.sort(key=lambda c: c.features["surprise"], reverse=True)
        return candidates[:limit]
