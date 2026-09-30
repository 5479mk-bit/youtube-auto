"""전 세계 자연재해·해외 인재(人災) 소재 — 위키백과 기반.

`src/sillok/` 과 같은 원칙이다: 전체 자동 발굴은 아직 없고, 사람이 고른 실제 사건
제목 목록(config.yaml `topics.disaster.titles`)으로 시작한다.

**대한민국 내 인재는 원천 배제한다** — 세월호·천안함·삼풍백화점·성수대교·이태원 등.
유가족이 계신 민감한 사안이라는 원칙(23단계에서도 같은 이유로 국내 인재를 뺐다)을
대본 프롬프트 지시뿐 아니라 여기서도 한 번 더 막는다. LLM 이 프롬프트를 무시해도
애초에 후보 풀에 없으면 안전하다.
"""

from __future__ import annotations

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


def collect_candidates(titles: list[str]) -> list[StoryCandidate]:
    candidates = []
    for title in titles:
        if title in _BLOCKED_KR_TITLES:
            logger.warning(f"[disasters] '{title}' 은 국내 인재라 후보에서 제외합니다")
            continue

        text = wikipedia_enrich.fetch_full_text(title)
        if not text:
            logger.warning(f"[disasters] '{title}' 위키백과 본문을 가져오지 못했습니다")
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
