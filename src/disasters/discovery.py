"""위키백과 분류(Category)에서 재난 문서 제목을 자동으로 발굴한다.

손으로 적은 제목 목록(config `topics.disaster.titles`)은 며칠이면 바닥났다(run #48,
#50, #53~#58, #66). 위키백과 분류 트리를 따라 내려가 사건 문서를 모으면 목록이 사실상
마르지 않는다. 결과는 캐시 파일에 남겨 매 회차 API 를 두드리지 않는다.

한국 내 사건은 여기서도 걸러낸다 — 분류 이름에 한국 관련 단어가 있으면 하위로 내려가지
않고, 문서 제목이 막힌 목록에 있으면 버린다(본문 검사는 `collect_candidates` 에서 한다).
"""

from __future__ import annotations

import json
import random
import re
import time
from datetime import date
from pathlib import Path

import requests
from loguru import logger

from ..heritage.wikipedia_enrich import API_URL, HEADERS, TIMEOUT

CACHE_TTL_SECONDS = 7 * 24 * 3600

# 이 단어가 든 분류는 내려가지 않는다 — 국내 사건이 섞이는 입구이거나 사건 문서가 아닌
# 메타 분류다.
_SKIP_CATEGORY_WORDS = (
    "대한민국", "한국의", "한국에서", "조선", "북한", "서울", "부산",
    "목록", "관련 정보", "위키프로젝트", "틀", "사람", "인물", "생존자", "희생자",
    "소설", "영화", "드라마", "게임", "음악", "만화", "다큐멘터리", "문화",
)

# 사건 문서 같은 제목만 통과시킨다 — 분류에는 '지진' 같은 일반 개념 문서도 섞여 있다.
_EVENT_SUFFIX = re.compile(
    r".{2,}(사고|참사|사건|붕괴|폭발|침몰|추락|충돌|화재|누출|지진|대지진|지진해일|해일|"
    r"대홍수|홍수|대기근|기근|분화|폭풍|태풍|허리케인|사이클론|산사태|눈사태|열파|"
    r"대공습|대화재|대지진|전염병|역병|재해|재난)$"
)
_YEAR = re.compile(r"\d{3,4}년")
_KR_TITLE_WORDS = ("한국", "대한민국", "조선", "북한", "서울", "부산", "제주")
_NOT_ARTICLE = ("목록", "분류:", "위키백과:", "틀:", "파일:")


def is_event_title(title: str) -> bool:
    if any(title.startswith(prefix) for prefix in _NOT_ARTICLE):
        return False
    if "(동음이의)" in title or "/" in title:
        return False
    if any(word in title for word in _KR_TITLE_WORDS):
        return False
    if _YEAR.search(title):
        return True
    # 연도가 없으면 "고유명 + 접미어"(공백 있는 여섯 글자 이상)만 — '역사지진' 같은 개념 문서를 거른다.
    return " " in title and len(title) >= 6 and bool(_EVENT_SUFFIX.search(title))


def _members(category: str, member_type: str) -> list[str] | None:
    """분류 하나의 하위 항목. 요청이 막히면 None(= 이번엔 포기, 빈 분류와 구분)."""
    titles: list[str] = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category,
        "cmtype": member_type,
        "cmnamespace": 14 if member_type == "subcat" else 0,
        "cmlimit": 500,
        "format": "json",
    }
    for attempt in range(3):
        try:
            response = requests.get(API_URL, params=params, timeout=TIMEOUT, headers=HEADERS)
            if response.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            response.raise_for_status()
            rows = response.json().get("query", {}).get("categorymembers", [])
            titles = [str(row["title"]) for row in rows]
            return titles
        except Exception as exc:
            logger.warning(f"[disasters] 분류 '{category}' 조회 실패: {exc}")
            return None
    logger.warning(f"[disasters] 분류 '{category}' 429 로 재시도 3회 모두 실패")
    return None


def crawl(roots: list[str], max_depth: int = 2, max_titles: int = 400) -> list[str]:
    """루트 분류들에서 `max_depth` 단계까지 내려가며 사건 제목을 모은다(너비 우선)."""
    seen_categories: set[str] = set()
    found: list[str] = []
    seen_titles: set[str] = set()
    queue: list[tuple[str, int]] = [(root, 0) for root in roots]

    while queue and len(found) < max_titles:
        category, depth = queue.pop(0)
        if category in seen_categories:
            continue
        seen_categories.add(category)

        pages = _members(category, "page")
        if pages is None:
            continue
        time.sleep(0.3)
        for title in pages:
            if title not in seen_titles and is_event_title(title):
                seen_titles.add(title)
                found.append(title)

        if depth >= max_depth:
            continue
        subcats = _members(category, "subcat") or []
        time.sleep(0.3)
        for sub in subcats:
            if not any(word in sub for word in _SKIP_CATEGORY_WORDS):
                queue.append((sub, depth + 1))

    return found[:max_titles]


def discover_titles(
    roots: list[str],
    cache_path: Path,
    max_depth: int = 2,
    max_titles: int = 400,
    now: float | None = None,
) -> list[str]:
    """캐시가 신선하면 캐시를, 아니면 새로 크롤링한다. 크롤링이 비면 묵은 캐시라도 쓴다."""
    now = time.time() if now is None else now
    cached: dict = {}
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass

    cached_roots = cached.get("roots")
    if (
        cached.get("titles")
        and cached_roots == roots
        and now - float(cached.get("fetched_at", 0)) < CACHE_TTL_SECONDS
    ):
        return list(cached["titles"])

    titles = crawl(roots, max_depth, max_titles)
    if not titles:
        logger.warning("[disasters] 분류 크롤링 결과가 비어 있어 묵은 캐시를 씁니다")
        return list(cached.get("titles", []))

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"fetched_at": now, "roots": roots, "titles": titles},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning(f"[disasters] 발굴 캐시를 저장하지 못했습니다: {exc}")
    logger.info(f"[disasters] 분류에서 재난 제목 {len(titles)}개를 발굴했습니다")
    return titles


def shuffled(titles: list[str], seed: str | None = None) -> list[str]:
    """날짜를 시드로 섞는다 — 같은 날엔 같은 순서(재실행 안정), 날마다 다른 주제."""
    ordered = list(titles)
    random.Random(seed or date.today().isoformat()).shuffle(ordered)
    return ordered
