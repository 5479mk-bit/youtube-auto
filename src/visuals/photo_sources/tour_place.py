"""한국관광공사 국문 관광정보 — 장소명으로 대표 사진을 찾는다.

데이터셋: https://www.data.go.kr/data/15101578/openapi.do
관광사진 검색이 비면 장소명(지명·시설명)으로 직접 찔러 firstimage 를 받는다.
"""

from __future__ import annotations

from loguru import logger

from ...datasource.datagokr import DataGoKrClient, DataGoKrError
from .base import MIN_WIDTH, PhotoResult

ENDPOINT = "/B551011/KorService2/searchKeyword2"
SOURCE_NAME = "한국관광공사 관광정보"
LICENSE_NAME = "공공누리 제1유형"


class TourPlaceSource:
    name = "tour_place"

    def __init__(self, client: DataGoKrClient):
        self.client = client

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def search(self, query_ko: str, query_en: str, limit: int) -> list[PhotoResult]:
        if not query_ko:
            return []
        try:
            rows = self.client.get(
                ENDPOINT,
                {
                    "MobileOS": "ETC",
                    "MobileApp": "youtube-auto",
                    "keyword": query_ko,
                    "arrange": "O",
                    "numOfRows": max(limit * 2, 10),
                    "pageNo": 1,
                },
            )
        except DataGoKrError as exc:
            logger.warning(f"[{self.name}] '{query_ko}' 검색 실패: {exc}")
            return []

        results: list[PhotoResult] = []
        for row in rows:
            url = str(row.get("firstimage") or row.get("firstimage2") or "").strip()
            if not url.startswith("http"):
                continue
            results.append(
                PhotoResult(
                    url=url,
                    source_name=SOURCE_NAME,
                    license_name=LICENSE_NAME,
                    page_url="https://www.data.go.kr/data/15101578/openapi.do",
                    author=str(row.get("title") or "").strip(),
                    width=MIN_WIDTH,
                )
            )
            if len(results) >= limit:
                break
        return results
