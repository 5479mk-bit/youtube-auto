"""한국관광공사 관광사진 정보 (포토코리아).

데이터셋: https://www.data.go.kr/data/15101914/openapi.do
공공누리 1유형이라 상업적 이용과 변형이 모두 허용된다. 대신 출처 표기가 이용 조건이므로
PhotoResult 에 반드시 담아 보낸다. 도시·거리·풍경 사진이 많아 이 채널의 기본 소스다.

활용신청 후 상세 문서의 경로가 다르면 ENDPOINT 만 맞추면 된다.
"""

from __future__ import annotations

from loguru import logger

from ...datasource.datagokr import DataGoKrClient, DataGoKrError
from .base import MIN_WIDTH, PhotoResult

ENDPOINT = "/B551011/PhotoGalleryService1/gallerySearchList1"
SOURCE_NAME = "한국관광공사 포토코리아"
LICENSE_NAME = "공공누리 제1유형"


class TourPhotoSource:
    name = "tour_photo"

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
                    "arrange": "A",
                    "numOfRows": max(limit * 2, 10),
                    "pageNo": 1,
                },
            )
        except DataGoKrError as exc:
            logger.warning(f"[{self.name}] '{query_ko}' 검색 실패: {exc}")
            return []

        results: list[PhotoResult] = []
        for row in rows:
            url = str(row.get("galWebImageUrl") or row.get("galPhotographyLocation") or "").strip()
            if not url.startswith("http"):
                continue
            results.append(
                PhotoResult(
                    url=url,
                    source_name=SOURCE_NAME,
                    license_name=LICENSE_NAME,
                    page_url="https://www.data.go.kr/data/15101914/openapi.do",
                    author=str(row.get("galPhotographer") or "").strip(),
                    width=MIN_WIDTH,
                )
            )
            if len(results) >= limit:
                break
        return results
