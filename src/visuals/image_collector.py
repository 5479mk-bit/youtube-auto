"""컷별 사진 확보.

소스 체인을 순서대로 물어 첫 번째로 쓸 만한 사진을 받아 캐시에 넣는다. 한 장도 못 구하면
단색으로 때우지 않고 그 사실을 그대로 알린다 — 1단계에서 단색 배경이 조용히 깔린 채로
영상이 나왔던 게 이 파이프라인의 가장 큰 문제였다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests
from loguru import logger
from PIL import Image

from ..models import PhotoCredit
from .photo_sources import PhotoResult, PhotoSource

USER_AGENT = "youtube-auto/0.2 (public-data documentary shorts)"


class PhotoNotFound(RuntimeError):
    """어떤 소스에서도 사진을 구하지 못했다."""


class ImageCollector:
    def __init__(
        self,
        sources: list[PhotoSource],
        cache_dir: Path,
        priority_images: list[PhotoResult] | None = None,
        fallback_queries: list[tuple[str, str]] | None = None,
    ):
        self.sources = sources
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.credits: list[PhotoCredit] = []
        self.failed_queries: list[str] = []
        # 문화재 항목에 공식 매핑된 사진(식별자 기반 조회). 검색어와 무관하게 소스 체인보다
        # 먼저 소진한다 — 텍스트 검색보다 화질·저작권이 확실하다.
        self._priority_pool: list[PhotoResult] = list(priority_images or [])
        # 이번 회차 주제의 대표 검색어(한국어, 영어). 컷 전용 검색어가 빗나갔을 때 쓴다.
        self.fallback_queries: list[tuple[str, str]] = list(fallback_queries or [])

    def collect(self, query_ko: str, query_en: str) -> tuple[Path, PhotoCredit] | None:
        """사진 한 장과 그 출처. 못 구하면 None 을 돌려주고 실패 검색어를 기록한다."""
        label = query_ko or query_en
        cached = self._cached(label)
        if cached:
            return cached

        found = self._from_priority_pool(label)
        if found:
            return found

        found = self._from_sources(query_ko, query_en, label)
        if found:
            return found

        # 컷 하나의 검색어가 빗나갔다고 회차 전체를 버리는 건 과하다. LLM 이 만든 검색어는
        # 실제 사진이 있는지 알 수 없으므로, 주제 대표 검색어(실존 확인된 것)로 한 번 더 본다.
        # 단색으로 때우는 게 아니라 같은 유적의 다른 실사 사진을 쓰는 것이라 성격이 다르다.
        for fallback_ko, fallback_en in self.fallback_queries:
            fallback_label = fallback_ko or fallback_en
            if not fallback_label or fallback_label == label:
                continue
            found = self._cached(fallback_label) or self._from_sources(
                fallback_ko, fallback_en, fallback_label
            )
            if found:
                logger.info(f"'{label}' 사진이 없어 '{fallback_label}' 로 대체합니다")
                return found

        logger.warning(f"'{label}' 사진을 어떤 소스에서도 찾지 못했습니다")
        self.failed_queries.append(label)
        return None

    def _from_priority_pool(self, label: str) -> tuple[Path, PhotoCredit] | None:
        while self._priority_pool:
            photo = self._priority_pool.pop(0)
            path = self._download(photo, label)
            if path is not None:
                return path, self._credit_for(photo, label)
        return None

    def _from_sources(
        self, query_ko: str, query_en: str, label: str
    ) -> tuple[Path, PhotoCredit] | None:
        for source in self.sources:
            if not source.enabled:
                continue
            for photo in source.search(query_ko, query_en, limit=3):
                path = self._download(photo, label)
                if path is None:
                    continue
                return path, self._credit_for(photo, label)
        return None

    def _credit_for(self, photo: PhotoResult, label: str) -> PhotoCredit:
        credit = PhotoCredit(
            query=label,
            source_name=photo.source_name,
            license_name=photo.license_name,
            page_url=photo.page_url,
            author=photo.author,
        )
        self._remember(credit)
        return credit

    def _cached(self, label: str) -> tuple[Path, PhotoCredit] | None:
        path = self._cache_path(label)
        if not path.exists():
            return None
        for credit in self.credits:
            if credit.query == label:
                return path, credit
        # 이전 실행이 남긴 파일. 출처를 모르면 쓰지 않는다.
        return None

    def _cache_path(self, label: str) -> Path:
        digest = hashlib.sha1(label.encode("utf-8")).hexdigest()[:12]
        return self.cache_dir / f"{digest}.jpg"

    def _remember(self, credit: PhotoCredit) -> None:
        if credit not in self.credits:
            self.credits.append(credit)

    def _download(self, photo: PhotoResult, label: str) -> Path | None:
        dest = self._cache_path(label)
        try:
            response = self.session.get(photo.url, timeout=60)
            response.raise_for_status()
            dest.write_bytes(response.content)
            with Image.open(dest) as image:
                image.verify()
            return dest
        except Exception as exc:
            logger.warning(f"사진 다운로드 실패({photo.url[:60]}): {exc}")
            dest.unlink(missing_ok=True)
            return None
