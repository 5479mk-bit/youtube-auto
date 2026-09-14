"""위키미디어 커먼즈 — 공공 API 가 못 채우는 소재의 폴백.

키가 필요 없는 대신 공용 IP 에 스로틀(429)이 걸리기 쉽다. 물러섰다 다시 묻고,
재사용 가능한 라이선스만 통과시킨다.
"""

from __future__ import annotations

import time

import requests
from loguru import logger

from .base import MIN_WIDTH, PhotoResult

ENDPOINT = "https://commons.wikimedia.org/w/api.php"
SOURCE_NAME = "위키미디어 커먼즈"
USER_AGENT = "youtube-auto/0.2 (public-data documentary shorts)"
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3


class WikimediaSource:
    name = "wikimedia"

    def __init__(self, allowed_licenses: list[str], thumb_width: int):
        self.allowed_licenses = list(allowed_licenses)
        self.thumb_width = thumb_width
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    @property
    def enabled(self) -> bool:
        return True

    def search(self, query_ko: str, query_en: str, limit: int) -> list[PhotoResult]:
        query = query_en or query_ko
        if not query:
            return []

        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6,
            "gsrlimit": max(limit * 2, 10),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|size",
            "iiurlwidth": self.thumb_width,
        }
        try:
            response = self._get_with_retry(params)
            pages = response.json().get("query", {}).get("pages", {})
        except Exception as exc:
            logger.warning(f"[{self.name}] '{query}' 검색 실패: {exc}")
            return []

        results: list[PhotoResult] = []
        for page in pages.values():
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("width", 0) < MIN_WIDTH:
                continue
            license_name = str(
                info.get("extmetadata", {}).get("LicenseShortName", {}).get("value", "")
            )
            if not self._license_allowed(info, license_name):
                continue
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue
            results.append(
                PhotoResult(
                    url=url,
                    source_name=SOURCE_NAME,
                    license_name=license_name,
                    page_url=str(info.get("descriptionurl") or ""),
                    width=int(info.get("width", 0)),
                    height=int(info.get("height", 0)),
                )
            )
            if len(results) >= limit:
                break
        return results

    def _license_allowed(self, info: dict, license_name: str) -> bool:
        license_id = str(info.get("extmetadata", {}).get("License", {}).get("value", ""))
        haystack = normalize_license(f"{license_name} {license_id}")
        return any(normalize_license(item) in haystack for item in self.allowed_licenses)

    def _get_with_retry(self, params: dict) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = self.session.get(ENDPOINT, params=params, timeout=60)
                if response.status_code == 429:
                    raise requests.HTTPError("429 Too Many Requests", response=response)
                response.raise_for_status()
                return response
            except Exception as exc:
                last_error = exc
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise last_error  # type: ignore[misc]


def normalize_license(value: str) -> str:
    """'CC BY-SA 4.0' 과 'cc-by-sa' 가 같은 것으로 읽히도록 구분자를 지운다."""
    return value.lower().replace("-", " ").replace("_", " ")
