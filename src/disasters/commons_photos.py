"""재난 문서에 실린 위키미디어 공용(Commons) 실제 사진 — 자유 라이선스만.

2026-10-27 사용자 결정("실제 사진 추가, 공용 사진만"). 레퍼런스(틱톡 lazarus_404 타이타닉 60만 회)는
AI 그림 사이에 실제 인물 사진·1912년 사진·지도를 섞어 "진짜 있었던 일"이라는 신뢰를 만든다.
예전에 재난 경로에서 웹 사진 검색을 끈 이유(유가족·저작권)는 그대로 지킨다:

- **그 문서에 실제로 실린 사진만** 쓴다(검색하지 않는다 — 엉뚱한 사진이 끌려오지 않는다).
- **공용(Commons) 저장소 + 자유 라이선스만**: 퍼블릭 도메인, CC0, CC BY, CC BY-SA.
  비자유(공정이용)·NC·ND·GFDL 단독은 버린다. 출처(작가·라이선스)는 설명란과 화면 끝에 표기한다.
- **희생자·시신·장례·부상 사진은 버린다**(파일명·설명의 낱말로 거른다 — 보수적으로).
"""

from __future__ import annotations

import hashlib
import html
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from loguru import logger
from PIL import Image, ImageEnhance, ImageFilter

from ..heritage.wikipedia_enrich import HEADERS, TIMEOUT, api_url
from ..models import ArchivePhoto

MAX_PHOTOS = 8
MIN_WIDTH = 500
MIN_HEIGHT = 400

_FREE_LICENSE = re.compile(
    r"^(public domain|dominio p[uú]blico|pd\b|pd-|cc0|cc[ -]by(-sa)?(\s|$|-?\d))", re.IGNORECASE
)
_NON_FREE = re.compile(r"\b(nc|nd)\b|non-?free|fair use|uso justo", re.IGNORECASE)

# 희생자·시신·부상·장례 — 유가족 원칙. 낱말 일부만 맞아도 버린다(보수적으로).
_SENSITIVE = (
    "cadáver", "cadaver", "cuerpo", "corpse", "bodies", "body ", "víctima", "victima", "victim",
    "muerto", "dead", "death", "fallecid", "funeral", "entierro", "burial", "fosa", "grave",
    "tumba", "morgue", "herido", "injured", "wounded", "sangre", "blood", "restos", "remains",
    "시신", "희생자", "사망자", "장례", "부상",
)
# 사진이 아닌 것(로고·깃발·문장·서명·아이콘).
_NOT_PHOTO = ("logo", "flag", "bandera", "escudo", "coat of arms", "icon", "signature", "firma", "seal")


def source_of(url: str) -> tuple[str, str] | None:
    """후보의 source_url(https://es.wikipedia.org/wiki/Título) → ("es", "Título")."""
    parsed = urlparse(url or "")
    host = parsed.netloc.split(".")
    if len(host) < 3 or host[1] != "wikipedia" or not parsed.path.startswith("/wiki/"):
        return None
    return host[0], unquote(parsed.path[len("/wiki/"):]).replace("_", " ")


def _clean(value: object, limit: int) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    text = " ".join(text.split())
    return text[:limit].rstrip()


def _meta(info: dict, key: str) -> str:
    return str((info.get("extmetadata", {}).get(key) or {}).get("value", ""))


def _describe(title: str, info: dict) -> str:
    description = _clean(_meta(info, "ImageDescription"), 160)
    if description:
        return description
    name = re.sub(r"^[^:]+:", "", title)
    name = re.sub(r"\.[a-zA-Z]{3,4}$", "", name)
    return name.replace("_", " ")[:160]


def is_free_license(name: str) -> bool:
    name = (name or "").strip()
    return bool(name) and bool(_FREE_LICENSE.match(name)) and not _NON_FREE.search(name)


def is_sensitive(title: str, description: str) -> bool:
    haystack = f" {title} {description} ".lower().replace("_", " ")
    return any(word in haystack for word in _SENSITIVE)


def _usable(page: dict) -> ArchivePhoto | None:
    info = (page.get("imageinfo") or [{}])[0]
    title = str(page.get("title", ""))
    if page.get("imagerepository") != "shared":  # 각 위키 로컬 파일(비자유 포함)은 쓰지 않는다
        return None
    if info.get("mime") not in ("image/jpeg", "image/png"):
        return None
    if int(info.get("width") or 0) < MIN_WIDTH or int(info.get("height") or 0) < MIN_HEIGHT:
        return None
    license_name = _clean(_meta(info, "LicenseShortName"), 40)
    if not is_free_license(license_name):
        return None
    description = _describe(title, info)
    lowered = f"{title} {description}".lower()
    if any(word in lowered for word in _NOT_PHOTO) or is_sensitive(title, description):
        return None
    url = info.get("thumburl") or info.get("url")
    if not url:
        return None
    return ArchivePhoto(
        title=title,
        url=str(url),
        description=description,
        author=_clean(_meta(info, "Artist"), 60) or "Autor desconocido",
        license=license_name,
        license_url=_clean(_meta(info, "LicenseUrl"), 120),
    )


def fetch_article_photos(lang: str, title: str, limit: int = MAX_PHOTOS) -> list[ArchivePhoto]:
    """문서에 실린 사진 중 쓸 수 있는 것. 실패하면 빈 목록(사진 없이도 영상은 만들어진다)."""
    params = {
        "action": "query",
        "format": "json",
        "formatversion": 2,
        "redirects": 1,
        "titles": title,
        "generator": "images",
        "gimlimit": 50,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": 1080,
        "iiextmetadatafilter": "LicenseShortName|Artist|ImageDescription|LicenseUrl",
        "iiextmetadatalanguage": "es",
    }
    for attempt in range(3):
        try:
            response = requests.get(api_url(lang), params=params, headers=HEADERS, timeout=TIMEOUT)
            if response.status_code == 429:
                time.sleep(3.0 * (attempt + 1))
                continue
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", [])
            break
        except Exception as exc:
            logger.warning(f"[photos] '{title}' 사진 목록 조회 실패: {exc}")
            return []
    else:
        logger.warning(f"[photos] '{title}' 사진 목록 429 — 사진 없이 진행합니다")
        return []

    photos = [photo for photo in (_usable(page) for page in pages) if photo]
    photos.sort(key=lambda photo: photo.title)
    logger.info(f"[photos] '{title}' 공용 사진 {len(photos)}장 사용 가능(문서 수록 {len(pages)}장 중)")
    return photos[:limit]


def download(url: str, cache_dir: Path) -> Path | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.jpg"
    if path.exists() and path.stat().st_size > 0:
        return path
    for attempt in range(3):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            if response.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            response.raise_for_status()
            path.write_bytes(response.content)
            Image.open(path).verify()
            return path
        except Exception as exc:
            logger.warning(f"[photos] 사진 내려받기 실패: {exc}")
            path.unlink(missing_ok=True)
            return None
    return None


def compose_vertical(photo_path: Path, out_path: Path, size: tuple[int, int]) -> Path:
    """가로·정사각 사진을 9:16 에 — 같은 사진을 흐리고 어둡게 깔고, 원본은 가운데에 통째로.

    레퍼런스 영상과 같은 방식이다. 꽉 채우려고 자르면 인물 얼굴·배 전체가 잘려 실제 사진의 힘이
    사라진다. 세로 사진이면 그대로 꽉 채운다.
    """
    width, height = size
    with Image.open(photo_path) as source:
        photo = source.convert("RGB")
    ratio = photo.width / photo.height
    if ratio <= width / height * 1.15:
        scale = max(width / photo.width, height / photo.height)
        cover = photo.resize((int(photo.width * scale) + 1, int(photo.height * scale) + 1))
        left = (cover.width - width) // 2
        top = (cover.height - height) // 2
        frame = cover.crop((left, top, left + width, top + height))
    else:
        scale = max(width / photo.width, height / photo.height)
        background = photo.resize((int(photo.width * scale) + 1, int(photo.height * scale) + 1))
        left = (background.width - width) // 2
        top = (background.height - height) // 2
        background = background.crop((left, top, left + width, top + height))
        background = background.filter(ImageFilter.GaussianBlur(radius=28))
        background = ImageEnhance.Brightness(background).enhance(0.45)
        # 사진은 화면 위쪽(가운데 32% 높이)에, 높이는 화면의 42% 까지 — 아래 58% 부근은 자막 자리다.
        fitted_width = int(width * 0.96)
        fitted_height = int(fitted_width / ratio)
        if fitted_height > height * 0.42:
            fitted_height = int(height * 0.42)
            fitted_width = int(fitted_height * ratio)
        fitted = photo.resize((fitted_width, fitted_height))
        background.paste(fitted, ((width - fitted.width) // 2, int(height * 0.32 - fitted.height / 2)))
        frame = background
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.save(out_path, quality=92)
    return out_path
