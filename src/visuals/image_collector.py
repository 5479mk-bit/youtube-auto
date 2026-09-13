"""위키미디어 커먼즈에서 문장별 배경 사진을 모은다.

커먼즈는 키가 필요 없고 라이선스 메타데이터를 함께 주기 때문에, 재사용 가능한
사진만 걸러낼 수 있다. 검색 결과가 없으면 단색 대체 이미지를 만들어 렌더가 멈추지
않게 한다.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import requests
from loguru import logger
from PIL import Image

USER_AGENT = "youtube-auto/0.1 (public-data documentary shorts)"
MIN_WIDTH = 900
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3


class ImageCollector:
    def __init__(
        self,
        endpoint: str,
        allowed_licenses: list[str],
        cache_dir: Path,
        width: int,
        height: int,
    ):
        self.endpoint = endpoint
        self.allowed_licenses = list(allowed_licenses)
        self.cache_dir = cache_dir
        self.width = width
        self.height = height
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.placeholder_count = 0

    def collect(self, query: str) -> Path:
        cached = self._cache_path(query)
        if cached.exists():
            return cached

        url = self._search(query)
        if url and self._download(url, cached):
            return cached

        logger.warning(f"'{query}' 이미지를 찾지 못해 대체 배경을 사용합니다")
        self.placeholder_count += 1
        # 대체 배경은 본 캐시와 다른 자리에 둔다. 같은 자리에 캐시하면 한 번 실패한
        # 검색어가 영영 단색 화면으로 굳는다.
        return self._placeholder(query, self._placeholder_path(query))

    def _cache_path(self, query: str) -> Path:
        return self.cache_dir / f"{self._digest(query)}.jpg"

    def _placeholder_path(self, query: str) -> Path:
        return self.cache_dir / "placeholder" / f"{self._digest(query)}.jpg"

    @staticmethod
    def _digest(query: str) -> str:
        return hashlib.sha1(query.encode("utf-8")).hexdigest()[:12]

    def _search(self, query: str) -> str | None:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6,
            "gsrlimit": 10,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|size",
            "iiurlwidth": self.width,
        }
        try:
            response = self._get_with_retry(self.endpoint, params)
            pages = response.json().get("query", {}).get("pages", {})
        except Exception as exc:
            logger.warning(f"커먼즈 검색 실패({query}): {exc}")
            return None

        for page in pages.values():
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("width", 0) < MIN_WIDTH:
                continue
            if not self._license_allowed(info):
                continue
            url = info.get("thumburl") or info.get("url")
            if url:
                return url
        return None

    def _get_with_retry(self, url: str, params: dict | None = None) -> requests.Response:
        """커먼즈와 프록시 모두 연속 호출에 429 를 돌려준다. 물러섰다 다시 묻는다."""
        last_error: Exception | None = None
        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = self.session.get(url, params=params, timeout=60)
                if response.status_code == 429:
                    raise requests.HTTPError("429 Too Many Requests", response=response)
                response.raise_for_status()
                return response
            except Exception as exc:
                last_error = exc
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise last_error  # type: ignore[misc]

    def _license_allowed(self, info: dict) -> bool:
        meta = info.get("extmetadata", {})
        short_name = str(meta.get("LicenseShortName", {}).get("value", ""))
        license_id = str(meta.get("License", {}).get("value", ""))
        haystack = _normalize_license(f"{short_name} {license_id}")
        return any(_normalize_license(allowed) in haystack for allowed in self.allowed_licenses)

    def _download(self, url: str, dest: Path) -> bool:
        try:
            response = self._get_with_retry(url)
            dest.write_bytes(response.content)
            with Image.open(dest) as image:
                image.verify()
            return True
        except Exception as exc:
            logger.warning(f"이미지 다운로드 실패: {exc}")
            dest.unlink(missing_ok=True)
            return False

    def _placeholder(self, query: str, dest: Path) -> Path:
        # 검색어마다 색이 달라지도록 해시에서 색을 만든다. 컷이 전부 같은 색이면
        # 무빙이 있어도 정지 화면처럼 보인다.
        digest = hashlib.sha1(query.encode("utf-8")).digest()
        color = (28 + digest[0] % 40, 30 + digest[1] % 40, 38 + digest[2] % 45)
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (self.width, self.height), color).save(dest, "JPEG", quality=90)
        return dest


def _normalize_license(value: str) -> str:
    """'CC BY-SA 4.0' 과 'cc-by-sa' 가 같은 것으로 읽히도록 구분자를 지운다."""
    return value.lower().replace("-", " ").replace("_", " ")
