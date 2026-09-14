"""소상공인시장진흥공단 상가(상권) 정보 — 업종별 생존/폐업 이야기를 뽑는다.

데이터셋: https://www.data.go.kr/data/15083033/openapi.do
"""

from __future__ import annotations

from collections import Counter

from ...models import StoryCandidate
from ..base import series_from_pairs
from ..datagokr import DataGoKrClient

ENDPOINT = "/B553077/api/open/sdsc2/storeListInDong"


class BusinessAdapter:
    name = "business"

    def fetch(self, client: DataGoKrClient, limit: int) -> list[StoryCandidate]:
        rows = list(client.paginate(ENDPOINT, {}, page_size=200, max_pages=5))

        by_region: dict[str, Counter] = {}
        for row in rows:
            region = str(row.get("adongNm") or row.get("signguNm") or "").strip()
            industry = str(row.get("indsSclsNm") or row.get("indsMclsNm") or "").strip()
            if region and industry:
                by_region.setdefault(region, Counter())[industry] += 1

        candidates: list[StoryCandidate] = []
        for region, counter in by_region.items():
            top = counter.most_common(5)
            if len(top) < 3:
                continue
            leader, leader_count = top[0]
            total = sum(counter.values())
            share = leader_count / total * 100

            candidates.append(
                StoryCandidate(
                    dataset=self.name,
                    topic_key=f"business:{region}",
                    headline=f"{region}에서 가장 많은 가게",
                    headline_number=f"{share:.0f}%",
                    subject=f"{region}의 {leader} 업종 밀집도",
                    context=(
                        f"{region}의 전체 점포 {total:,}곳 중 {leader}가 {leader_count:,}곳으로 "
                        f"{share:.1f}%를 차지한다. 상위 업종: "
                        + ", ".join(f"{name} {count:,}곳" for name, count in top)
                    ),
                    series=series_from_pairs([(name, float(count)) for name, count in top]),
                    series_unit="곳",
                    source_name="소상공인시장진흥공단 상가정보",
                    source_url="https://www.data.go.kr/data/15083033/openapi.do",
                    image_queries_ko=[region, f"{region} 상가", "전통시장"],
                    image_queries_en=[region, "korean street shops", "small business korea"],
                    features={
                        "personal_relevance": 0.85,
                        "surprise": min(share / 40, 1.0),
                        "magnitude": min(total / 5_000, 1.0),
                        "freshness": 0.7,
                    },
                )
            )

        candidates.sort(key=lambda c: c.features["surprise"], reverse=True)
        return candidates[:limit]
