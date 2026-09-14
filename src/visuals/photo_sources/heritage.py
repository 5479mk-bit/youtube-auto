"""문화재청 국가문화유산포털 이미지.

문화재·전통 건축이 소재일 때만 의미가 있어 우선순위는 관광공사 다음이다.
데이터셋: https://www.data.go.kr/data/15000060/openapi.do
"""

from __future__ import annotations

from loguru import logger

from ...datasource.datagokr import DataGoKrClient, DataGoKrError
from .base import MIN_WIDTH, PhotoResult

ENDPOINT = "http://www.khs.go.kr/cha/SearchKindOpenapiList.do"
SOURCE_NAME = "국가유산청 국가문화유산포털"
LICENSE_NAME = "공공누리 제1유형"


class HeritageSource:
    name = "heritage"

    def __init__(self, client: DataGoKrClient):
        self.client = client

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def search(self, query_ko: str, query_en: str, limit: int) -> list[PhotoResult]:
        if not query_ko:
            return []
        try:
            rows = self.client.get(ENDPOINT, {"ccbaMnm1": query_ko, "pageUnit": limit})
        except DataGoKrError as exc:
            logger.warning(f"[{self.name}] '{query_ko}' 검색 실패: {exc}")
            return []

        results: list[PhotoResult] = []
        for row in rows:
            url = str(row.get("imageUrl") or row.get("ccimgUrl") or "").strip()
            if not url.startswith("http"):
                continue
            results.append(
                PhotoResult(
                    url=url,
                    source_name=SOURCE_NAME,
                    license_name=LICENSE_NAME,
                    page_url="https://www.heritage.go.kr",
                    author=str(row.get("ccbaMnm1") or "").strip(),
                    width=MIN_WIDTH,
                )
            )
            if len(results) >= limit:
                break
        return results
