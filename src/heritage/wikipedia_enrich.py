"""위키백과 한국어판 — 요약과 '전설/설화' 계열 섹션을 보강한다.

API 키가 필요 없는 유일한 소스라 국가유산청/관광공사가 막혀도 폴백으로 쓸 수 있다.
문서 전체가 아니라 미스터리 키워드가 섞인 섹션만 골라 wiki_excerpt 로 넘긴다 — 대본이
"전해지는 말로는" 식으로 구분해서 쓸 재료다.
"""

from __future__ import annotations

import re
import time

import requests
from loguru import logger

from ..models import StoryCandidate
from .story_scorer import MYSTERY_KEYWORDS, mystery_score, story_density

API_URL = "https://ko.wikipedia.org/w/api.php"
SOURCE_NAME = "위키백과"
TIMEOUT = 15
MAX_EXCERPT_CHARS = 1200
# 위키미디어는 User-Agent 없는 요청(파이썬 requests 기본값 포함)을 403 으로 막는다.
# 이걸 빠뜨려서 wiki_excerpt 가 늘 비어 있었다 — 야사 콘텐츠의 재료가 통째로 없던 셈이다.
# 2026-10-27: 위키미디어 User-Agent 정책은 "연락 가능한 정보(URL/메일)"를 요구한다 — 그게 없는
# 범용 UA 는 429 를 훨씬 자주 받는다(run #82: 분류 크롤링·본문 조회가 수십 번 429 로 막혀
# 지정 주제가 전부 탈락했다). 저장소 주소를 연락처로 넣는다.
HEADERS = {
    "User-Agent": "SecretosDelColapsoBot/1.0 (https://github.com/5479mk-bit/youtube-auto)"
}


def api_url(lang: str = "ko") -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"

# 이 이름이 붙은 섹션은 통째로 미스터리 후보다. 제목이 정확히 일치하지 않아도
# 부분 일치로 잡는다 ("전설과 설화" 같은 변형 대응).
MYSTERY_SECTION_TITLES = ["전설", "설화", "일화", "미스터리", "논란", "유래", "수수께끼"]


def fetch_summary(title: str) -> str:
    """짧은 개요. 문서가 없거나 동음이의 페이지면 빈 문자열을 돌려준다."""
    if not title:
        return ""
    try:
        response = requests.get(
            f"https://ko.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title)}",
            timeout=TIMEOUT,
            headers={**HEADERS, "accept": "application/json"},
        )
        if response.status_code != 200:
            return ""
        data = response.json()
    except Exception as exc:
        logger.warning(f"[wikipedia] '{title}' 요약 조회 실패: {exc}")
        return ""

    if data.get("type") == "disambiguation":
        return ""
    return str(data.get("extract") or "").strip()


def fetch_mystery_excerpt(title: str) -> str:
    """문서 전체를 받아 전설/설화/논란 계열 섹션만 뽑는다."""
    text = _fetch_plaintext(title)
    if not text:
        return ""

    sections = _split_sections(text)
    picked: list[str] = []
    for heading, body in sections:
        if any(keyword in heading for keyword in MYSTERY_SECTION_TITLES):
            picked.append(body.strip())

    if not picked:
        # 섹션 제목으로 못 찾으면, 본문 전체에서 미스터리 키워드가 있는 문단만 줍는다.
        for paragraph in text.split("\n\n"):
            if any(keyword in paragraph for keyword in MYSTERY_KEYWORDS):
                picked.append(paragraph.strip())

    excerpt = "\n\n".join(part for part in picked if part)
    return excerpt[:MAX_EXCERPT_CHARS]


def _fetch_plaintext(title: str, lang: str = "ko") -> str:
    if not title:
        return ""
    # disasters.collect_candidates() 가 후보 목록 전체를 쉴 틈 없이 연속 호출하면
    # (run #51 실제로 6개 요청이 1초 안에 몰렸다) 위키미디어가 429 로 전부 거부해
    # "선정할 주제 후보가 없습니다"로 회차가 통째로 중단된다 — 제목 자체는 멀쩡해도
    # 그랬다. 429 만 짧게 재시도한다(그 외 오류는 재시도해도 같은 결과라 바로 포기).
    for attempt in range(4):
        try:
            response = requests.get(
                api_url(lang),
                params={
                    "action": "query",
                    "prop": "extracts",
                    "explaintext": 1,
                    "format": "json",
                    "titles": title,
                    "redirects": 1,
                },
                timeout=TIMEOUT,
                headers=HEADERS,
            )
            if response.status_code == 429:
                wait = 3.0 * (attempt + 1)
                logger.warning(
                    f"[wikipedia] '{title}' 요청이 429(Too Many Requests) — {wait:.1f}초 뒤 재시도"
                )
                time.sleep(wait)
                continue
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning(f"[wikipedia] '{title}' 본문 조회 실패: {exc}")
            return ""

        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            if "missing" in page:
                return ""
            return str(page.get("extract") or "")
        return ""

    logger.warning(f"[wikipedia] '{title}' 429 로 재시도 4회 모두 실패")
    return ""


def _split_sections(text: str) -> list[tuple[str, str]]:
    """'== 제목 ==' 마커로 위키 본문을 (제목, 내용) 쌍으로 나눈다.

    re.split 은 캡처 그룹이 있으면 그 그룹들도 결과에 끼워 넣는다. 매치마다
    (등호, 제목) 두 그룹씩 늘어나니, parts[0] 이 도입부고 그 뒤로는
    (등호, 제목, 본문) 세 개씩 반복된다.
    """
    parts = re.split(r"\n(={2,4})\s*([^=]+?)\s*\1\n", text)
    sections: list[tuple[str, str]] = [("개요", parts[0])]
    for i in range(1, len(parts), 3):
        heading = parts[i + 1].strip() if i + 1 < len(parts) else ""
        body = parts[i + 2].strip() if i + 2 < len(parts) else ""
        if heading:
            sections.append((heading, body))
    return sections


def fetch_full_text(title: str, lang: str = "ko") -> str:
    """문서 전문(요약이 아니라 본문 전체)을 그대로 돌려준다.

    재난 소재처럼 "전설/설화" 필터 없이 본문 전체를 사실 재료로 쓰는 경우를 위한
    공개 진입점이다 — `_fetch_plaintext` 를 외부 모듈에서 직접 import하지 않도록 감싼다.
    """
    return _fetch_plaintext(title, lang)


def enrich(candidate: StoryCandidate) -> StoryCandidate:
    """후보의 wiki_excerpt 를 채우고, 해설문+위키 합산으로 mystery_score/story_density 를
    다시 매긴다. 위키에 문서가 없거나 네트워크가 막혀도 해설문만으로 후보는 유효하다 —
    여기서 실패해도 candidate 를 그대로 돌려준다.
    """
    excerpt = fetch_mystery_excerpt(candidate.subject)
    if not excerpt:
        excerpt = fetch_summary(candidate.subject)
    if not excerpt:
        return candidate

    candidate.wiki_excerpt = excerpt
    candidate.features["mystery_score"] = mystery_score(candidate.context, excerpt)
    candidate.features["story_density"] = story_density(candidate.context, excerpt)
    return candidate
