"""한국관광공사 국문 관광정보 — overview 필드로 해설문을 보강한다.

국가유산청 해설문이 짧거나 비어 있을 때 쓰는 2순위 소스다. `overview` 는 관광지 소개문이라
문화재 해설문보다 느슨하지만, 일화·전설이 섞여 들어오는 경우가 많아 미스터리 스코어에도
도움이 된다.

데이터셋: https://www.data.go.kr/data/15101578/openapi.do (TourAPI 4.0, KorService2)
"""

from __future__ import annotations

from loguru import logger

from ..datasource.datagokr import DataGoKrClient, DataGoKrError

SEARCH_ENDPOINT = "/B551011/KorService2/searchKeyword2"
DETAIL_ENDPOINT = "/B551011/KorService2/detailCommon2"
SOURCE_NAME = "한국관광공사 관광정보"
SOURCE_URL = "https://www.data.go.kr/data/15101578/openapi.do"


class TourOverviewSource:
    name = "tour_overview"

    def __init__(self, client: DataGoKrClient):
        self.client = client

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def fetch_overview(self, name: str) -> str:
        """장소명으로 검색해 contentId 를 찾고, 상세 API 로 overview 문단을 받는다."""
        if not self.enabled or not name:
            return ""

        content_id = self._find_content_id(name)
        if not content_id:
            return ""

        try:
            rows = self.client.get(
                DETAIL_ENDPOINT,
                {
                    "MobileOS": "ETC",
                    "MobileApp": "youtube-auto",
                    "contentId": content_id,
                    "defaultYN": "Y",
                    "overviewYN": "Y",
                },
            )
        except DataGoKrError as exc:
            logger.warning(f"[{self.name}] '{name}' 상세 조회 실패: {exc}")
            return ""

        if not rows:
            return ""
        return str(rows[0].get("overview") or "").strip()

    def _find_content_id(self, name: str) -> str:
        try:
            rows = self.client.get(
                SEARCH_ENDPOINT,
                {
                    "MobileOS": "ETC",
                    "MobileApp": "youtube-auto",
                    "keyword": name,
                    "arrange": "O",
                    "numOfRows": 5,
                    "pageNo": 1,
                },
            )
        except DataGoKrError as exc:
            logger.warning(f"[{self.name}] '{name}' 검색 실패: {exc}")
            return ""

        for row in rows:
            content_id = str(row.get("contentid") or "").strip()
            if content_id:
                return content_id
        return ""
