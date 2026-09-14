"""국토교통부 아파트 실거래가 — 가격 급변 이야기를 뽑는다.

데이터셋: https://www.data.go.kr/data/15058747/openapi.do
"""

from __future__ import annotations

from ...models import StoryCandidate
from ..base import series_from_pairs, to_float
from ..datagokr import DataGoKrClient

ENDPOINT = "/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"


class RealEstateAdapter:
    name = "realestate"

    def fetch(self, client: DataGoKrClient, limit: int) -> list[StoryCandidate]:
        rows = list(client.paginate(ENDPOINT, {}, page_size=100, max_pages=5))

        by_complex: dict[str, list[tuple[str, float]]] = {}
        for row in rows:
            name = str(row.get("aptNm") or row.get("아파트") or "").strip()
            year = str(row.get("dealYear") or row.get("년") or "").strip()
            month = str(row.get("dealMonth") or row.get("월") or "").strip().zfill(2)
            amount = to_float(row.get("dealAmount") or row.get("거래금액"))
            if name and year and amount:
                by_complex.setdefault(name, []).append((f"{year}-{month}", amount))

        candidates: list[StoryCandidate] = []
        for name, points in by_complex.items():
            points.sort()
            if len(points) < 3:
                continue
            first, last = points[0][1], points[-1][1]
            if first <= 0:
                continue
            change_pct = (last - first) / first * 100

            candidates.append(
                StoryCandidate(
                    dataset=self.name,
                    topic_key=f"realestate:{name}",
                    headline=f"{name} 실거래가 변화",
                    headline_number=f"{change_pct:+.1f}%",
                    subject=f"{name}의 아파트 실거래가 흐름",
                    context=(
                        f"{name}의 실거래가가 {points[0][0]} {first:,.0f}만원에서 "
                        f"{points[-1][0]} {last:,.0f}만원으로 {change_pct:+.1f}% 움직였다."
                    ),
                    series=series_from_pairs(points[-6:]),
                    series_unit="만원",
                    source_name="국토교통부 아파트 실거래가",
                    source_url="https://rt.molit.go.kr",
                    image_queries_ko=[name, "아파트 단지", "도시 주거지"],
                    image_queries_en=[name, "korean apartment complex", "seoul apartment"],
                    features={
                        "personal_relevance": 0.95,
                        "surprise": min(abs(change_pct) / 30, 1.0),
                        "magnitude": min(abs(last - first) / 30_000, 1.0),
                        "freshness": 0.9,
                    },
                )
            )

        candidates.sort(key=lambda c: c.features["surprise"], reverse=True)
        return candidates[:limit]
