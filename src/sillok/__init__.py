"""조선왕조실록 기사를 이야깃거리 후보로 바꾸는 층.

문화재 해설문(`src/heritage/`)으로 한 편을 만들어보니 "8각 기단, 16엽 연판" 같은 건축
도감이라 서사가 없었다. 실록에는 인물·날짜·사건이 원문에 박혀 있어 야사의 재료가 된다.

지금은 **샘플 한 편** 단계라 기사를 사람이 지목한다(`config.yaml` 의
`topics.sillok.article_ids`). 왕대 전체를 훑는 색인은 샘플을 보고 판단한 뒤에 만든다.
"""

from __future__ import annotations

from loguru import logger

from ..models import StoryCandidate
from . import article as article_module


def collect_candidates(article_ids: list[str]) -> list[StoryCandidate]:
    """지목된 기사들을 후보로 만든다. 하나가 실패해도 나머지는 살린다."""
    candidates: list[StoryCandidate] = []
    for article_id in article_ids:
        try:
            candidates.append(article_module.to_candidate(article_module.fetch(article_id)))
        except Exception as exc:
            logger.warning(f"[sillok] {article_id} 수집 실패: {exc}")
    return candidates
