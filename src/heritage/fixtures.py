"""서비스키가 없거나 API 가 실패했을 때 쓰는 샘플 문화재 픽스처.

`datasource/base.py`의 `load_fixture`와 같은 패턴이다. 실제 해설문이 아니므로
`is_sample=True`로 표시해 업로드 단계에서 걸러지게 한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import StoryCandidate

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str = "heritage") -> list[StoryCandidate]:
    path = FIXTURE_DIR / f"{name}.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [StoryCandidate(**{**item, "is_sample": True}) for item in raw]
