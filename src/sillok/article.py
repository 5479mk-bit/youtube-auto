"""실록 기사 한 건을 기사 ID 로 가져온다.

공개된 실록 데이터셋(data.go.kr 15053647)은 **원문(한문)뿐**이라 대본 재료가 안 된다.
국사편찬위원회 실록 사이트의 기사 페이지에는 공식 국역과 한문 원문이 같이 올라와 있어,
여기서 국역을 가져온다. 한문을 LLM 으로 번역하지 않는 이유는 분명하다 — 오역이 한 줄이라도
박히면 "사실 기반"이라는 채널 전제가 무너진다. 공식 번역만 쓴다.

페이지 구조(실제 응답으로 확인함):
    <p class="date">   광해군일기[정초본]20권, 광해 1년 9월 25일 계묘 3/3 기사 <span>/ 1609년 …</span>
    <div class="title-head"> … <h3>기사 제목</h3>
    <h4 class="view-title">국역</h4><div class="view-text"> … </div>
    <h4 class="view-title">원문</h4><div class="view-text"> … </div>
    <ul class="bot-info"> … 【분류】과학-천기(天氣)

주의할 점 두 가지도 실제로 확인했다.
- User-Agent 가 없으면 400 Request Blocked 로 막힌다.
- 첫 호출이 연결 리셋으로 끊기는 일이 잦다. 재시도하면 받아진다.
"""

from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests
from loguru import logger

from ..models import StoryCandidate
from . import event_keywords

BASE_URL = "https://sillok.history.go.kr/id/"
# 받아둔 기사 페이지를 남겨두는 곳. 실록 사이트로 나가는 길이 불안정하다.
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "sillok"
# 실록 사건에는 사진이 존재하지 않는다 — "하늘에 이상한 물체가 나타났다"를 찍은 사진은
# 없다. 그래서 컷별 검색어가 빗나갔을 때 돌아갈 곳으로 조선을 대표하는 **실존 대상**을
# 미리 정해둔다. 궁궐·회화·고지도는 위키미디어에 실제로 있는 것들이다.
# 아래 다섯은 `python main.py photos` 로 실제로 사진이 오는지 하나씩 확인한 것이다.
# "조선왕조실록", "정족산사고", "강원도" 는 한 장도 안 와서 뺐다 — 그럴듯해 보이는
# 검색어를 넣어두면 렌더 도중에 조용히 비는 컷이 생긴다.
DEFAULT_IMAGE_QUERIES_KO = ("경복궁", "창덕궁", "동궐도", "대동여지도", "숭례문")
DEFAULT_IMAGE_QUERIES_EN = (
    "Gyeongbokgung",
    "Changdeokgung",
    "Donggwoldo",
    "Daedongyeojido",
    "Sungnyemun",
)
# 국역은 한국고전번역원 번역을 국사편찬위원회가 서비스하는 것이다. 둘 다 밝힌다.
SOURCE_NAME = "조선왕조실록 (국사편찬위원회·한국고전번역원 국역)"
TIMEOUT = 40
MAX_ATTEMPTS = 6
# 기본 requests UA 는 400 으로 막힌다. 브라우저 UA 를 써야 본문이 온다.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}


class SillokError(RuntimeError):
    pass


@dataclass
class SillokArticle:
    """기사 하나. translation 이 대본의 유일한 사실 재료다."""

    article_id: str
    title: str  # "강원도에서 일어난 기이한 자연현상에 대해 강원 감사 이형욱이 치계하다"
    reign: str  # "광해군일기[정초본]20권, 광해 1년 9월 25일 계묘 3/3 기사"
    western_year: str  # "1609년 명 만력(萬曆) 37년"
    translation: str  # 국역 전문
    original: str  # 한문 원문
    categories: list[str] = field(default_factory=list)  # 【분류】 과학-천기(天氣)

    @property
    def url(self) -> str:
        return f"{BASE_URL}{self.article_id}"


def fetch(article_id: str, cache_dir: Path | None = None) -> SillokArticle:
    """기사 ID 하나로 국역·원문·제목·날짜를 가져온다.

    받은 페이지는 캐시에 남긴다. 실록 사이트로 나가는 길이 자주 끊겨(재시도 6번이 전부
    실패한 적도 있다) 그때마다 회차 전체가 날아가는 건 과하다 — 한 번 받아둔 기사는
    다음 실행에서 네트워크 없이 그대로 쓴다.
    """
    cache = (cache_dir or CACHE_DIR) / f"{article_id}.html"
    if cache.exists() and cache.stat().st_size > 1000:
        try:
            return parse(article_id, cache.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[sillok] 캐시가 깨져 다시 받습니다: {exc}")

    html = _get(f"{BASE_URL}{article_id}")
    article = parse(article_id, html)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(html, encoding="utf-8")
    return article


def to_candidate(article: SillokArticle) -> StoryCandidate:
    """대본 생성기가 받는 형태로 옮긴다.

    문화재 후보와 같은 그릇(`StoryCandidate`)을 쓰고 `dataset` 으로만 갈린다 —
    대본 프롬프트·SEO 문구가 이 값 하나를 보고 분기한다.
    """
    match = event_keywords.scan(article.translation, article.original)
    facts = split_sentences(article.translation)

    return StoryCandidate(
        dataset="sillok",
        topic_key=f"sillok:{article.article_id}",
        headline=article.title,
        headline_number=_short_date(article.reign, article.western_year),
        subject=article.title,
        context=article.translation,
        source_name=SOURCE_NAME,
        source_url=article.url,
        facts=facts,
        topic_tags=list(article.categories),
        image_queries_ko=list(DEFAULT_IMAGE_QUERIES_KO),
        image_queries_en=list(DEFAULT_IMAGE_QUERIES_EN),
        features={
            "event_score": round(min(match.score, 1.0), 3),
            "story_density": min(len(article.translation) / 800, 1.0),
            "freshness": 1.0,
        },
    )


def split_sentences(text: str, max_facts: int = 12) -> list[str]:
    """국역을 문장으로 쪼갠다.

    실록 국역은 인용부호 안에 보고문이 통째로 들어있고 '…하였습니다.' 로 끝나는 문장이
    많아, 문화재 해설문용 `heritage.story_scorer.extract_facts` 의 '다.' 기준으로는 잘
    안 갈린다. 여기서는 마침표·물음표 뒤 공백을 기준으로 자른다.
    """
    if not text:
        return []
    sentences = re.split(r"(?<=[.?!])\s+", text)
    facts = [sentence.strip() for sentence in sentences if len(sentence.strip()) >= 8]
    return facts[:max_facts]


def parse(article_id: str, html: str) -> SillokArticle:
    title = _first(html, r'<div class="title-head".*?<h3>(.*?)</h3>')
    date_block = _first(html, r'<p class="date">(.*?)</p>')
    reign, western_year = _split_date(date_block)

    bodies = _view_texts(html)
    translation = bodies.get("국역", "")
    original = bodies.get("원문", "")
    if not translation:
        raise SillokError(f"{article_id}: 국역을 찾지 못했다")

    return SillokArticle(
        article_id=article_id,
        title=title,
        reign=reign,
        western_year=western_year,
        translation=translation,
        original=original,
        categories=_categories(html),
    )


def _get(url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                logger.warning(f"[sillok] 재시도 {attempt}/{MAX_ATTEMPTS}: {exc}")
    raise SillokError(f"{url} 호출 실패: {last_error}")


def _view_texts(html: str) -> dict[str, str]:
    """`<h4 class="view-title">국역</h4><div class="view-text">…</div>` 짝을 모은다.

    같은 내용이 PC/모바일 레이아웃으로 두 번 들어있어서 먼저 나온 것만 쓴다.
    """
    found: dict[str, str] = {}
    pattern = re.compile(
        r'<h4 class="view-title">(국역|원문)</h4>\s*<div class="view-text">(.*?)</div>',
        re.S,
    )
    for label, body in pattern.findall(html):
        if label not in found:
            found[label] = _strip_footer(_plain_text(body))
    return found


def _strip_footer(text: str) -> str:
    """본문 끝에 붙는 서지·분류·저작권 표시를 떼어낸다.

    `【정족산사고본】 5책 20권 12장 B면 … 【분류】과학-천기(天氣) ⓒ 한국고전번역원` 이
    본문 div 안에 같이 들어있다. 이게 facts 에 섞이면 대본이 서지 정보를 읽는다.
    """
    return re.split(r"【|ⓒ", text)[0].strip()


def _categories(html: str) -> list[str]:
    block = _first(html, r"【분류】(.*?)</li>")
    if not block:
        return []
    return [part for part in re.split(r"\s*/\s*", _plain_text(block)) if part]


def _split_date(block: str) -> tuple[str, str]:
    """`p.date` 는 '왕대·권·날짜 <span>/ 서기년</span>' 형태다."""
    year = _plain_text(_first(block, r"<span>(.*?)</span>")).lstrip("/ ").strip()
    reign = _plain_text(re.sub(r"<span>.*?</span>", "", block, flags=re.S))
    return reign, year


def _first(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.S)
    return match.group(1) if match else ""


def _plain_text(fragment: str) -> str:
    """태그와 주석을 걷어내고 공백을 정리한다.

    본문 안에 인명·지명이 <a>/<span> 으로 감싸여 있어 태그를 지우면 그대로 읽히는 글이
    된다. <p> 는 문장 구분이라 공백으로 바꾼다.
    """
    text = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    # 문단·줄바꿈 태그만 공백으로 바꾼다. 인명·지명을 감싼 <span>/<a> 까지 공백으로 바꾸면
    # "이형욱(李馨郁) 이 치계하였다" 처럼 조사가 떨어져 나가 읽을 때 어색해진다.
    text = re.sub(r"</?(?:p|br|div|li|ul|tr|td|h\d)\b[^>]*>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _short_date(reign: str, western_year: str) -> str:
    """화면에 띄울 짧은 표기. '광해 1년 9월 25일' + '1609년' 정도면 충분하다."""
    match = re.search(r"([가-힣]+\s*\d+년\s*\d+월\s*\d+일)", reign)
    day = match.group(1) if match else reign
    year = re.match(r"(\d{3,4}년)", western_year)
    return f"{day} ({year.group(1)})" if year else day
