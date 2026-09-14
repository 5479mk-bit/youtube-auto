"""사진 소스 공통 인터페이스.

소스마다 검색 방식과 응답 껍데기가 다르지만, 파이프라인은 "검색어를 주면 쓸 수 있는
사진 후보가 나온다"만 알면 된다. 출처와 라이선스는 후보에 붙여서 끝까지 들고 간다.
공공누리 1유형은 출처 표기가 이용 조건이라 여기서 놓치면 뒤에서 복구할 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

MIN_WIDTH = 800


@dataclass
class PhotoResult:
    url: str
    source_name: str
    license_name: str = ""
    page_url: str = ""
    author: str = ""
    width: int = 0
    height: int = 0


class PhotoSource(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    def search(self, query_ko: str, query_en: str, limit: int) -> list[PhotoResult]: ...
