"""전 세계 자연재해·해외 인재(人災) 소재 — 위키백과 기반.

사람이 고른 제목 목록(config.yaml `topics.disaster.titles`)을 먼저 쓰고, 바닥나면
`discovery.py` 가 위키백과 분류에서 자동으로 발굴한 제목으로 이어간다.

**대한민국 내 인재는 원천 배제한다** — 세월호·천안함·삼풍백화점·성수대교·이태원 등.
유가족이 계신 민감한 사안이라는 원칙(23단계에서도 같은 이유로 국내 인재를 뺐다)을
대본 프롬프트 지시뿐 아니라 여기서도 한 번 더 막는다. LLM 이 프롬프트를 무시해도
애초에 후보 풀에 없으면 안전하다.
"""

from __future__ import annotations

import time

from loguru import logger

from ..heritage import wikipedia_enrich
from ..models import StoryCandidate

# 채널 언어를 스페인어로 전환(2026-09-30)하면서 공개 설명란에 노출되는 출처
# 표기도 스페인어로 바꿨다 — 실제 데이터는 여전히 한국어 위키백과에서 가져온다
# (fetch_full_text 는 ko.wikipedia.org 를 그대로 쓴다), 표기만 청중 언어에 맞춘다.
SOURCE_NAME = "Wikipedia"
MAX_CONTEXT_CHARS = 4000
MAX_FACTS = 20

# 프롬프트 지시와 별개의 코드 레벨 방어선. 표기 변형까지 다 막을 수는 없으니
# config.yaml 에 애초에 한국 인재를 적지 않는 게 1차 방어고, 이건 2차 방어다.
_BLOCKED_KR_TITLES = {
    "세월호 침몰 사고", "세월호",
    "천안함 피격 사건", "천안함",
    "삼풍백화점 붕괴 사고", "삼풍백화점",
    "성수대교 붕괴 사고", "성수대교",
    "이태원 압사 사고", "이태원 핼러윈 참사",
    "대구 지하철 참사",
    "씨랜드 청소년수련원 화재 사고",
}


# 발굴(discovery)한 제목은 사람이 확인하지 않았으니 본문 앞부분에 이 단어가 있으면 국내
# 사건으로 보고 버린다(세월호 등 목록에 없는 변형까지 거르는 마지막 방어선).
_KR_BODY_MARKERS = ("대한민국", "한국에서", "한국의 ", "서울특별시", "경상북도", "경상남도", "전라남도", "전라북도", "충청남도", "충청북도", "경기도", "강원도")
_KR_BODY_SCAN_CHARS = 500


def _looks_domestic(text: str) -> bool:
    head = text[:_KR_BODY_SCAN_CHARS]
    return any(marker in head for marker in _KR_BODY_MARKERS)


def collect_candidates(
    titles: list[str],
    used: set[str] = frozenset(),
    discovered: list[str] = (),
    max_candidates: int | None = None,
) -> list[StoryCandidate]:
    """`used`(이미 다룬 topic_key 집합)에 들어있는 제목은 위키백과 호출 자체를
    건너뛴다 — 목록이 길어질수록(지금 16개) 매 회차 전부를 순서대로 조회하다가
    위키미디어 429(Too Many Requests)에 한꺼번에 걸려 후보가 0개로 떨어진 사고가
    있었다(run #51, 2026-10-03). 안 쓸 제목을 안 부르는 게 가장 확실한 예방이고,
    남은 제목들 사이에도 0.3초를 둬 같은 초에 몰리지 않게 한다."""
    candidates = []
    first = True
    curated = set(titles)
    for title in [*titles, *(t for t in discovered if t not in curated)]:
        if max_candidates is not None and len(candidates) >= max_candidates:
            break
        # 사람이 고른 제목이 하나라도 후보가 됐으면 발굴 제목은 섞지 않는다 — 섞으면
        # 점수(본문 길이)로 밀려 사람이 지정한 주제가 안 나올 수 있다(2026-10-20).
        if title not in curated and candidates:
            break
        if title in _BLOCKED_KR_TITLES:
            logger.warning(f"[disasters] '{title}' 은 국내 인재라 후보에서 제외합니다")
            continue
        if f"disaster:{title}" in used:
            continue

        if not first:
            time.sleep(0.3)
        first = False

        text = wikipedia_enrich.fetch_full_text(title)
        if not text:
            logger.warning(f"[disasters] '{title}' 위키백과 본문을 가져오지 못했습니다")
            continue

        if title not in curated and _looks_domestic(text):
            logger.warning(f"[disasters] '{title}' 은 국내 사건으로 보여 제외합니다")
            continue

        facts = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
        candidate = StoryCandidate(
            dataset="disaster",
            topic_key=f"disaster:{title}",
            headline=title,
            headline_number="",
            subject=title,
            context=text[:MAX_CONTEXT_CHARS],
            facts=facts[:MAX_FACTS],
            source_name=SOURCE_NAME,
        )
        # topics.disaster.weights(story_density/freshness)가 실제로 뭔가를 보게
        # 채운다 — 안 채우면 모든 후보가 0점으로 묶여 topic_key 순서(=config.yaml
        # 에 적은 순서)로만 뽑힌다("맨 위 항목만 계속 나온다"의 원인 중 하나였다).
        # freshness 는 여기서 계산할 이력 정보가 없어 중립값으로 둔다 — 실제 중복
        # 방지는 select_topic()의 topic_key exclude_keys 가 맡는다.
        candidate.features = {
            "story_density": min(len(text) / 3000, 1.0),
            "freshness": 1.0,
        }
        candidates.append(candidate)
    return candidates
