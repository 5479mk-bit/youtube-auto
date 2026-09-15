"""문화재 후보의 이야깃거리를 수치화한다.

이 파일은 텍스트만 본다. API 호출은 heritage_source/tour_overview/wikipedia_enrich 가
하고, 여기서는 그 결과(해설문·위키 발췌·지정 등급)를 받아 네 축 점수를 매긴다.
"""

from __future__ import annotations

import re

# 이 단어들이 많을수록 "그냥 설명"이 아니라 "이야기"에 가깝다.
MYSTERY_KEYWORDS = [
    "전설", "설화", "일화", "이야기", "전해진다", "전해지는", "알려져",
    "의문", "미스터리", "수수께끼", "논란", "논쟁", "미확인", "추정",
    "사라진", "사라졌다", "행방", "저주", "기이", "괴이", "영험",
    "구전", "비화", "발견되었다", "발굴", "복원", "훼손", "화재",
]

# 국가유산 지정 등급의 대략적인 규모감. 등급 표기가 이것과 정확히 일치하지 않을 수 있어
# 부분 일치로 찾는다.
PRESTIGE_WEIGHTS = {
    "국보": 1.0,
    "보물": 0.75,
    "사적": 0.65,
    "명승": 0.55,
    "천연기념물": 0.55,
    "국가무형유산": 0.55,
    "시도유형문화유산": 0.35,
    "시도무형유산": 0.35,
    "시도기념물": 0.3,
    "문화유산자료": 0.2,
}
DEFAULT_PRESTIGE = 0.25


def mystery_score(*texts: str) -> float:
    """미스터리 키워드 밀도. 텍스트가 길수록 우연히 걸릴 확률도 높아 길이로 정규화한다."""
    joined = " ".join(text for text in texts if text)
    if not joined:
        return 0.0

    hits = sum(len(re.findall(re.escape(keyword), joined)) for keyword in MYSTERY_KEYWORDS)
    # 500자당 키워드 몇 개인지로 정규화. 대략 3개 이상이면 만점권.
    density = hits / (len(joined) / 500)
    return min(density / 3.0, 1.0)


def story_density(*texts: str) -> float:
    """풀어낼 문장이 얼마나 되는가. 너무 짧으면 대본 두 편을 못 채운다."""
    total_chars = sum(len(text) for text in texts if text)
    # 800자면 1부/2부를 채우기에 넉넉하다고 본다.
    return min(total_chars / 800, 1.0)


def prestige_score(category: str) -> float:
    if not category:
        return DEFAULT_PRESTIGE
    for name, weight in PRESTIGE_WEIGHTS.items():
        if name in category:
            return weight
    return DEFAULT_PRESTIGE


def extract_facts(text: str, max_facts: int = 8) -> list[str]:
    """해설문을 문장 단위로 쪼갠다. LLM 이 여기 없는 사실을 지어내지 못하게 하는 재료다."""
    if not text:
        return []

    sentences = re.split(r"(?<=[.다음]\.)\s+|(?<=다\.)\s+", text)
    facts = [sentence.strip() for sentence in sentences if len(sentence.strip()) >= 8]
    return facts[:max_facts]
