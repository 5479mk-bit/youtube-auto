"""실록에서 '영상이 되는 기사'를 골라내는 키워드.

실록은 기사가 수십만 건인데 대부분은 인사 발령과 의례 기록이라 영상으로 만들 게 없다.
쇼츠가 되는 건 사람이 보고 놀란 사건들 — 하늘에 나타난 것, 괴이한 짐승, 독살 의혹,
역모, 재변 — 이다. 그걸 찾는 그물이 이 파일이다.

한문과 한글을 같이 두는 이유: 공공데이터포털에 공개된 실록은 **원문(한문)뿐**이고 국역
데이터셋이 없다. 그래서 사건은 한문으로 찾고, 대본 재료가 될 국역은 따로 구해야 한다.
같은 그물로 국역문도 훑을 수 있게 한글 짝을 같이 둔다.

`_HANJA` 는 정자와 약자를 같이 적는다 — 원문 표기가 갈리는 글자들이 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EventTopic:
    """사건 갈래 하나. 무게는 '쇼츠로 얼마나 셀까'를 뜻한다."""

    name: str
    weight: float
    hanja: tuple[str, ...]
    hangul: tuple[str, ...]


# 무게는 절대적인 중요도가 아니라 '쇼츠에서 먹히는 정도'다. 사관이 놀라서 적은 것일수록
# 세고, 행정 기록에 가까울수록 약하다.
EVENT_TOPICS: tuple[EventTopic, ...] = (
    EventTopic(
        name="괴물·요괴",
        weight=1.0,
        hanja=("怪物", "怪獸", "妖怪", "妖物", "異獸", "怪異", "妖言", "鬼物"),
        hangul=("괴물", "요괴", "괴수", "기이한 짐승", "요물", "귀물", "괴이"),
    ),
    EventTopic(
        name="하늘의 이변",
        weight=0.95,
        # 1609년 강원도 기사로 그물을 실제로 던져보니 하나도 안 걸렸다. 사관은 "彗星"
        # 같은 이름을 쓰지 않고 본 대로 적는다 — 소리(雷聲·天動)와 생김새(形如)로.
        hanja=(
            "彗星", "白虹", "日食", "月食", "赤氣", "光物", "天鼓", "星隕", "無雲而雷",
            "天動", "火光", "怪光", "有物在天", "靑天白日",
        ),
        hangul=(
            "혜성", "흰 무지개", "붉은 기운", "빛나는 물체", "별이 떨어", "구름 없이 천둥",
            "하늘에 나타", "우레 소리", "천둥 소리", "불덩이", "공중에 떠",
        ),
    ),
    EventTopic(
        name="독살·의문사",
        weight=0.95,
        hanja=("毒殺", "鴆", "暴薨", "暴卒", "不豫", "賜死"),
        hangul=("독살", "짐독", "갑자기 훙", "갑자기 죽", "사사", "의문의 죽음"),
    ),
    EventTopic(
        name="역모·옥사",
        weight=0.85,
        hanja=("逆謀", "謀反", "獄事", "鞫問", "告變", "誣告"),
        hangul=("역모", "모반", "옥사", "국문", "고변", "무고"),
    ),
    EventTopic(
        name="재변·천재지변",
        weight=1.0,
        # 23단계: 자연재해 소재로 축을 옮기며 가중치를 키우고(0.7→1.0), 기근·흉년
        # 쪽 키워드를 추가했다 — 경신대기근 기사(주리다/굶주/餓死/饑饉)가 하나도
        # 안 걸리던 구멍이었다.
        hanja=(
            "地震", "雷震", "大水", "旱災", "疫癘", "蝗", "火災",
            "饑饉", "凶年", "餓死", "飢民", "賑恤", "賑濟", "水害", "颱風", "暴風",
        ),
        hangul=(
            "지진", "벼락", "홍수", "가뭄", "전염병", "황충", "화재",
            "기근", "흉년", "굶주", "아사", "구휼", "진휼", "수해", "태풍", "폭풍",
        ),
    ),
    EventTopic(
        name="궁중 암투",
        weight=0.8,
        hanja=("巫蠱", "詛呪", "厭勝", "後宮", "廢妃", "廢世子"),
        hangul=("무고", "저주", "염승", "폐비", "폐세자", "굿"),
    ),
)

# 인사 발령·의례처럼 사건이 아닌 기사를 걸러낸다. 실록 기사 대부분이 여기 해당한다.
ROUTINE_MARKERS: tuple[str, ...] = (
    "除授", "拜", "遞差", "辭職", "上疏", "謝恩", "進賀", "祭享", "朝參",
    "제수", "체차", "사직", "상소", "사은", "진하", "제향", "조참",
)

_WHITESPACE = re.compile(r"\s+")


@dataclass
class EventMatch:
    """기사 하나를 훑은 결과."""

    score: float = 0.0
    topics: list[str] = field(default_factory=list)
    hits: dict[str, int] = field(default_factory=dict)
    is_routine: bool = False

    @property
    def matched(self) -> bool:
        return bool(self.topics) and not self.is_routine


def scan(*texts: str) -> EventMatch:
    """원문이든 국역이든 받아서 어떤 사건 갈래에 걸리는지 본다.

    같은 기사의 한문과 한글을 같이 넘겨도 된다 — 어느 쪽에서 걸리든 한 번만 센다.
    """
    joined = _WHITESPACE.sub(" ", " ".join(text for text in texts if text))
    if not joined:
        return EventMatch()

    match = EventMatch(is_routine=_is_routine(joined))
    for topic in EVENT_TOPICS:
        count = sum(joined.count(word) for word in topic.hanja + topic.hangul)
        if not count:
            continue
        match.topics.append(topic.name)
        match.hits[topic.name] = count
        # 같은 말이 열 번 나온다고 열 배 센 사건은 아니다. 처음 몇 번에 무게를 준다.
        match.score += topic.weight * min(count, 3) / 3

    return match


def _is_routine(text: str) -> bool:
    """인사·의례 기사인지. 사건 키워드가 있어도 이쪽이 압도적이면 영상감이 아니다."""
    routine = sum(text.count(marker) for marker in ROUTINE_MARKERS)
    return routine >= 3
