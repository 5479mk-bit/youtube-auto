"""소스 우선순위 체인.

한국 공공 API 를 먼저 묻고, 비면 위키미디어로 내려간다. 어느 소스에서 왔는지는
PhotoCredit 으로 남겨 영상 말미와 설명란에 표기한다.

`heritage.py` 의 `HeritageSource` 는 여기 체인에 넣지 않는다 — 국가유산청 이미지 API 는
이름 검색이 아니라 (ccbaKdcd, ccbaAsno, ccbaCtcd) 식별자 조합으로만 조회되는데, 이 체인의
`search(query_ko, query_en, limit)` 시그니처엔 식별자가 없다. 문화재 항목의 공식 이미지는
대신 `src/heritage/heritage_media.py`가 식별자로 미리 가져와 `ImageCollector` 에 우선
후보 풀로 주입한다(`pipeline.build_collector`). `heritage.py` 는 이름 기반 추측이라 정확도가
떨어져 더는 쓰지 않지만, 참고용으로 파일은 남겨둔다.
"""

from __future__ import annotations

from ...datasource.datagokr import DataGoKrClient
from .base import PhotoResult, PhotoSource
from .tour_photo import TourPhotoSource
from .tour_place import TourPlaceSource
from .wikimedia import WikimediaSource

__all__ = [
    "PhotoResult",
    "PhotoSource",
    "build_chain",
    "TourPhotoSource",
    "TourPlaceSource",
    "WikimediaSource",
]


def build_chain(
    service_key: str, allowed_licenses: list[str], thumb_width: int
) -> list[PhotoSource]:
    client = DataGoKrClient(service_key)
    return [
        TourPhotoSource(client),
        TourPlaceSource(client),
        WikimediaSource(allowed_licenses, thumb_width),
    ]
