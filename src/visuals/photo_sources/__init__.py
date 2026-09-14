"""소스 우선순위 체인.

한국 공공 API 를 먼저 묻고, 비면 위키미디어로 내려간다. 어느 소스에서 왔는지는
PhotoCredit 으로 남겨 영상 말미와 설명란에 표기한다.
"""

from __future__ import annotations

from ...datasource.datagokr import DataGoKrClient
from .base import PhotoResult, PhotoSource
from .heritage import HeritageSource
from .tour_photo import TourPhotoSource
from .tour_place import TourPlaceSource
from .wikimedia import WikimediaSource

__all__ = [
    "PhotoResult",
    "PhotoSource",
    "build_chain",
    "HeritageSource",
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
        HeritageSource(client),
        WikimediaSource(allowed_licenses, thumb_width),
    ]
