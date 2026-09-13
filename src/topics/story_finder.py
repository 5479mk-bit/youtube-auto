"""여러 데이터셋 어댑터를 돌려 이야깃거리 후보를 모은다."""

from __future__ import annotations

from loguru import logger

from ..datasource.base import fetch_with_fallback
from ..datasource.datagokr import DataGoKrClient
from ..datasource.datasets import ADAPTERS
from ..models import StoryCandidate


def collect_candidates(
    client: DataGoKrClient, dataset_names: list[str], per_dataset: int
) -> list[StoryCandidate]:
    candidates: list[StoryCandidate] = []
    for name in dataset_names:
        adapter = ADAPTERS.get(name)
        if adapter is None:
            logger.warning(f"알 수 없는 데이터셋: {name}")
            continue
        found = fetch_with_fallback(adapter, client, per_dataset)
        logger.info(f"[{name}] 후보 {len(found)}건")
        candidates.extend(found)
    return candidates
