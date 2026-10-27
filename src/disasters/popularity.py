"""재난 주제 우선순위 — 기념일(시의성)과 스페인어권 인기도(위키백과 조회수).

2026-10-27 개편(한 달 평균 조회수 300): 큰 원인 하나가 주제였다. 분류 트리에서 무작위로
발굴한 "2010년 오가사와라 지진" 같은 사건은 중남미 시청자가 들어본 적이 없어 첫 1초에
넘긴다. 그래서
1. 이미 아는 사건 — 스페인어 위키백과 최근 60일 조회수가 높은 문서,
2. 기념일이 가까운 사건 — 검색·관심이 실제로 몰리는 날,
을 먼저 고른다. 본문도 가능하면 스페인어 위키백과에서 직접 가져온다(고유명사가 현지
표기 그대로 나오고, 한국어를 거쳐 번역하며 생기는 오역이 없다).

config `topics.disaster.titles` 항목 형식:
- `노트르담 대성당 화재`                          — 한국어 문서(예전 형식 그대로 동작)
- `es:Tragedia de Armero | 1985-11-13`            — 스페인어 문서 + 사건 날짜
- `타이타닉호 | 1912-04-15 | es:Hundimiento del RMS Titanic`
      — 이력 키는 한국어 제목 그대로(이미 다룬 주제가 다시 나오지 않게), 본문은 스페인어판.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from loguru import logger

from ..heritage.wikipedia_enrich import HEADERS, TIMEOUT, api_url

PAGEVIEWS_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "{lang}.wikipedia/all-access/user/{article}/daily/{start}/{end}"
)
PAGEVIEW_DAYS = 60
CACHE_TTL_DAYS = 14
LOOKUP_GAP_SECONDS = 0.15
# 조회수 6만(60일)이면 거의 만점 — 타이타닉·체르노빌급. 수백 회면 0.4 안팎(무명 사건).
_POPULARITY_LOG_CEILING = 5.0

# 날짜는 중남미 시청자 기준(멕시코시티 UTC-6)으로 센다 — 업로드가 KST 낮 12~13시 =
# 중남미 전날 밤이라 한국 날짜로 세면 기념일이 하루 어긋난다.
AUDIENCE_UTC_OFFSET_HOURS = -6

MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LANG_RE = re.compile(r"^([a-z]{2}):(.+)$")


@dataclass(frozen=True)
class TopicEntry:
    key: str  # topic_key(이력·중복 방지)에 쓰는 제목
    lang: str  # 본문을 가져올 위키백과 언어
    title: str  # 본문을 가져올 문서명
    when: date | None = None
    fallback_title: str = ""  # lang 문서를 못 가져오면 대신 쓸 한국어 문서명

    @property
    def es_title(self) -> str:
        return self.title if self.lang == "es" else ""


def _split_lang(text: str) -> tuple[str, str]:
    match = _LANG_RE.match(text.strip())
    if match:
        return match.group(1), match.group(2).strip()
    return "ko", text.strip()


def parse_entry(raw: str) -> TopicEntry:
    parts = [part.strip() for part in str(raw).split("|") if part.strip()]
    if not parts:
        return TopicEntry(key="", lang="ko", title="")
    lang, title = _split_lang(parts[0])
    when: date | None = None
    source: tuple[str, str] | None = None
    for part in parts[1:]:
        if _DATE_RE.fullmatch(part):
            try:
                when = date.fromisoformat(part)
            except ValueError:
                logger.warning(f"[topics] '{raw}' 날짜 형식이 잘못돼 무시합니다")
        else:
            source = _split_lang(part)
    if source and source != (lang, title):
        return TopicEntry(
            key=title,
            lang=source[0],
            title=source[1],
            when=when,
            fallback_title=title if lang == "ko" else "",
        )
    return TopicEntry(key=title, lang=lang, title=title, when=when)


def wiki_url(lang: str, title: str) -> str:
    return f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"


# ── 기념일 ────────────────────────────────────────────────────────────────


def audience_today(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(hours=AUDIENCE_UTC_OFFSET_HOURS)).date()


def _in_year(when: date, year: int) -> date:
    try:
        return when.replace(year=year)
    except ValueError:  # 2월 29일
        return date(year, 3, 1)


def _days_until(when: date, today: date) -> int:
    upcoming = _in_year(when, today.year)
    if upcoming < today:
        upcoming = _in_year(when, today.year + 1)
    return (upcoming - today).days


def _days_since(when: date, today: date) -> int:
    last = _in_year(when, today.year)
    if last > today:
        last = _in_year(when, today.year - 1)
    return (today - last).days


def anniversary_score(when: date | None, today: date) -> float:
    """당일이 최고, 1~2일 전이 그다음(유튜브가 퍼뜨리는 데 하루쯤 걸린다). 일주일 넘게
    남았으면 0 — 너무 일찍 써버리면 정작 당일에 쓸 게 없다."""
    if when is None:
        return 0.0
    until = _days_until(when, today)
    since = _days_since(when, today)
    if until == 0:
        return 1.0
    if until <= 2:
        return 0.9
    if until <= 4:
        return 0.5
    if since <= 2:
        return 0.4
    if until <= 7:
        return 0.2
    return 0.0


def anniversary_hint(when: date | None, today: date) -> str:
    """대본 프롬프트의 'Pistas' 에 넣는 문장. 기념일이 멀면 빈 문자열."""
    if when is None:
        return ""
    until = _days_until(when, today)
    since = _days_since(when, today)
    if until <= 7:
        target = today + timedelta(days=until)
    elif since <= 2:
        target = today - timedelta(days=since)
    else:
        return ""
    years = target.year - when.year
    if years <= 0:
        return ""
    return (
        f"aniversario: el {target.day} de {MESES[target.month - 1]} de {target.year} se cumplen "
        f"{years} años del suceso ({when.day} de {MESES[when.month - 1]} de {when.year}); "
        f'puedes decir "Hace {years} años"'
    )


# ── 인기도(스페인어 위키백과 조회수) ─────────────────────────────────────────


class _RateLimited(Exception):
    pass


def popularity_score(views: int) -> float:
    return min(math.log10(max(views, 0) + 1) / _POPULARITY_LOG_CEILING, 1.0)


def _load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(path: Path, cache: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[topics] 인기도 캐시 저장 실패: {exc}")


def _fresh(record: dict | None, today: date) -> bool:
    if not record:
        return False
    try:
        at = date.fromisoformat(record.get("at", ""))
    except ValueError:
        return False
    return (today - at).days < CACHE_TTL_DAYS


def _pageviews(es_title: str, today: date) -> int:
    end = today - timedelta(days=1)
    start = end - timedelta(days=PAGEVIEW_DAYS - 1)
    url = PAGEVIEWS_URL.format(
        lang="es",
        article=quote(es_title.replace(" ", "_"), safe=""),
        start=start.strftime("%Y%m%d"),
        end=end.strftime("%Y%m%d"),
    )
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    if response.status_code == 404:  # 문서가 없거나 조회 기록이 없다
        return 0
    if response.status_code == 429:
        raise _RateLimited()
    response.raise_for_status()
    return sum(int(item.get("views", 0)) for item in response.json().get("items", []))


def es_titles_for(ko_titles: list[str]) -> dict[str, str]:
    """한국어 문서 → 스페인어 문서명(langlinks). 50개씩 한 번에 묻는다. 스페인어판이
    없는 문서는 결과에 빠진다 — 그 자체로 '중남미가 모르는 사건'이라는 신호다."""
    result: dict[str, str] = {}
    for start in range(0, len(ko_titles), 50):
        batch = ko_titles[start : start + 50]
        try:
            response = requests.get(
                api_url("ko"),
                params={
                    "action": "query",
                    "prop": "langlinks",
                    "lllang": "es",
                    "lllimit": "max",
                    "titles": "|".join(batch),
                    "redirects": 1,
                    "format": "json",
                    "formatversion": 2,
                },
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            query = response.json().get("query", {})
        except Exception as exc:
            logger.warning(f"[topics] 스페인어 문서명 조회 실패: {exc}")
            continue
        original: dict[str, str] = {}
        for item in query.get("normalized", []):
            original[item["to"]] = item["from"]
        for item in query.get("redirects", []):
            original[item["to"]] = original.get(item["from"], item["from"])
        for page in query.get("pages", []):
            links = page.get("langlinks") or []
            if links and links[0].get("title"):
                title = page.get("title", "")
                result[original.get(title, title)] = links[0]["title"]
    return result


def popularity_scores(
    entries: list[TopicEntry], cache_path: Path, today: date | None = None
) -> dict[str, float]:
    """entry.key → 인기도(0~1). 조회 실패한 항목은 빠진다(점수 0 취급).

    조회수는 14일 캐시한다(.cache 는 회차 사이에 이어진다) — 매 회차 수십 번씩 두드리지
    않는다. 429 를 한 번 받으면 이번 회차 조회는 거기서 멈춘다(주제 선정이 막히면 안 된다).
    """
    today = today or audience_today()
    cache = _load_cache(cache_path)
    scores: dict[str, float] = {}

    need_es = [e.key for e in entries if not e.es_title and not _fresh(cache.get(f"ko2es:{e.key}"), today)]
    if need_es:
        found = es_titles_for(need_es)
        for key in need_es:
            cache[f"ko2es:{key}"] = {"title": found.get(key, ""), "at": today.isoformat()}

    lookups = 0
    for entry in entries:
        es_title = entry.es_title or (cache.get(f"ko2es:{entry.key}") or {}).get("title", "")
        if not es_title:
            continue
        record = cache.get(f"views:{es_title}")
        if not _fresh(record, today):
            if lookups:
                time.sleep(LOOKUP_GAP_SECONDS)
            lookups += 1
            try:
                record = {"views": _pageviews(es_title, today), "at": today.isoformat()}
            except _RateLimited:
                logger.warning("[topics] 조회수 API 429 — 이번 회차 인기도 조회를 멈춥니다")
                break
            except Exception as exc:
                logger.warning(f"[topics] '{es_title}' 조회수 조회 실패: {exc}")
                continue
            cache[f"views:{es_title}"] = record
        scores[entry.key] = popularity_score(int(record.get("views", 0)))

    _save_cache(cache_path, cache)
    return scores


def es_source_of(key: str, cache_path: Path) -> str:
    """popularity_scores() 가 캐시에 남긴 한국어→스페인어 문서명."""
    return (_load_cache(cache_path).get(f"ko2es:{key}") or {}).get("title", "")


# ── 시청자 적합도 ─────────────────────────────────────────────────────────
# 2026-10-27 유튜브 스튜디오(최근 28일): 시청자 멕시코 35% · 미국 17%(대부분 히스패닉) · 아르헨티나·
# 칠레·페루 순, 남성 77%, 55세 이상 61%. 조회수 상위가 지진 영상("8.6 TERREMOTO" 982회,
# "69.000 Muertes: Sismo Asia" 606회), 검색어도 "temblor"·"terremoto"·"aviones que se chocan".
# 그래서 지진·멕시코·중남미·항공 사건에 가산점을 준다(값이 클수록 우선).
_AUDIENCE_KEYWORDS: tuple[tuple[float, tuple[str, ...]], ...] = (
    (1.0, ("terremoto", "sismo", "temblor", "tsunami", "지진", "해일")),
    (1.0, ("méxico", "mexico", "멕시코", "puebla", "chiapas", "michoacán", "guerrero", "oaxaca",
           "colima", "guadalajara", "ixhuatepec", "pasta de conchos", "otis", "paricutín",
           "chichón", "patricia", "gilbert", "wilma", "pauline")),
    (1.0, ("chicxulub",)),  # 유카탄 — 공룡을 끝낸 운석, 멕시코 시청자에게 '우리 땅' 이야기
    (0.8, ("vuelo", "aéreo", "avión", "항공", "hindenburg", "transbordador", "우주왕복선", "apolo")),
    # '다시 일어나면?' 류(검색어 "cómo el mundo podría terminar", 레퍼런스 60만 회 채널의 단골 소재)
    (0.8, ("tormenta solar", "yellowstone", "tambora", "bermudas", "mary celeste")),
    (0.7, ("armero", "chile", "perú", "áncash", "ecuador", "guatemala", "managua", "mocoa",
           "vargas", "cromañón", "kiss", "joelma", "brumadinho", "valdivia", "uruguaya", "lamia",
           "ara san juan", "haití", "아이티", "페루", "mitch", "fuego", "maría", "san josé", "tam ")),
)


def audience_fit(entry: TopicEntry) -> float:
    haystack = f"{entry.key} {entry.title}".lower()
    return max(
        (score for score, words in _AUDIENCE_KEYWORDS if any(word in haystack for word in words)),
        default=0.0,
    )


def prescore(
    entry: TopicEntry, today: date, popularity: dict[str, float], weights: dict[str, float]
) -> float:
    """본문을 받기 전에 매기는 점수 — 본문 조회(429 위험)를 상위 몇 개로만 줄이려고."""
    return (
        weights.get("anniversary", 0.0) * anniversary_score(entry.when, today)
        + weights.get("popularity", 0.0) * popularity.get(entry.key, 0.0)
        + weights.get("audience_fit", 0.0) * audience_fit(entry)
    )
