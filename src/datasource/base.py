"""데이터셋 어댑터 공통 인터페이스.

어댑터는 원천 API 의 생김새를 숨기고, 파이프라인이 이해하는 ``StoryCandidate``
리스트만 돌려준다. 서비스키가 없거나 호출이 실패하면 번들 픽스처로 폴백해서
파이프라인 전체가 키 없이도 끝까지 돌 수 있게 한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from loguru import logger

from ..models import DataPoint, StoryCandidate
from .datagokr import DataGoKrClient, DataGoKrError

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


class DatasetAdapter(Protocol):
    name: str

    def fetch(self, client: DataGoKrClient, limit: int) -> list[StoryCandidate]: ...


def load_fixture(name: str) -> list[StoryCandidate]:
    path = FIXTURE_DIR / f"{name}.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        StoryCandidate(
            **{
                **item,
                "series": [DataPoint(**point) for point in item.get("series", [])],
                "is_sample": True,
            }
        )
        for item in raw
    ]


def fetch_with_fallback(
    adapter: DatasetAdapter, client: DataGoKrClient, limit: int
) -> list[StoryCandidate]:
    if not client.enabled:
        logger.warning(f"[{adapter.name}] 서비스키가 없어 픽스처 데이터를 사용합니다")
        return load_fixture(adapter.name)[:limit]

    try:
        candidates = adapter.fetch(client, limit)
    except DataGoKrError as exc:
        logger.warning(f"[{adapter.name}] API 실패, 픽스처로 폴백합니다: {exc}")
        return load_fixture(adapter.name)[:limit]

    if not candidates:
        logger.warning(f"[{adapter.name}] 후보가 비어 픽스처로 폴백합니다")
        return load_fixture(adapter.name)[:limit]
    return candidates


def to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def series_from_pairs(pairs: list[tuple[str, float]]) -> list[DataPoint]:
    return [DataPoint(label=label, value=value) for label, value in pairs]
