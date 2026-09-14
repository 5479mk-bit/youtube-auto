"""한국어 문장 조립과 낭독을 위한 도구.

자막에 쓰는 글과 입으로 읽는 글은 다르다. 화면에는 `12,480곳` 이라고 띄우는 게 맞지만,
TTS 에 그대로 넣으면 숫자를 어떻게 읽을지 엔진이 제멋대로 정한다. 그래서 낭독용 문장을
따로 만들어 넘긴다.
"""

from __future__ import annotations

import re

DIGITS = "영일이삼사오육칠팔구"
SMALL_UNITS = ["", "십", "백", "천"]
BIG_UNITS = ["", "만", "억", "조"]
NUMBER_PATTERN = re.compile(r"([-+]?)(\d[\d,]*)(\.\d+)?\s*(%|퍼센트)?")


def has_final_consonant(word: str) -> bool:
    """마지막 글자에 받침이 있는지. 한글이 아니면 없는 것으로 본다."""
    if not word:
        return False
    last = word[-1]
    if not ("가" <= last <= "힣"):
        return False
    return (ord(last) - ord("가")) % 28 != 0


def particle(word: str, with_final: str, without_final: str) -> str:
    """'카페이 2.9년로' 같은 문장이 나오지 않도록 조사를 골라 붙인다."""
    return f"{word}{with_final if has_final_consonant(word) else without_final}"


def sino_korean(number: int) -> str:
    """한자어 수 읽기. 곳·건·명·년 같은 단위 앞에 쓰는 방식이다."""
    if number == 0:
        return "영"

    groups: list[int] = []
    remaining = abs(number)
    while remaining > 0:
        groups.append(remaining % 10000)
        remaining //= 10000

    parts: list[str] = []
    for index in reversed(range(len(groups))):
        group = groups[index]
        if group == 0:
            continue
        # '일만 이천' 이 아니라 '만 이천' 이라고 말한다. 만 자리에서만 일을 떼어낸다.
        if index == 1 and group == 1:
            parts.append("만")
        else:
            parts.append(_read_group(group) + BIG_UNITS[index])

    text = " ".join(parts)
    return f"마이너스 {text}" if number < 0 else text


def read_aloud(text: str) -> str:
    """문장 안의 숫자를 소리 나는 대로 바꾼다. `2.9년` → `이 점 구 년`."""
    return NUMBER_PATTERN.sub(_replace_number, text)


def _replace_number(match: re.Match[str]) -> str:
    sign, integer_part, decimal_part, percent = match.groups()

    spoken = sino_korean(int(integer_part.replace(",", "")))
    if sign == "-":
        spoken = f"마이너스 {spoken}"

    if decimal_part:
        # 소수점 아래는 자리수가 아니라 숫자를 하나씩 읽는다.
        digits = " ".join(DIGITS[int(digit)] for digit in decimal_part[1:])
        spoken = f"{spoken} 점 {digits}"

    if percent:
        spoken = f"{spoken} 퍼센트"
        return spoken

    # 긴 숫자는 단위를 띄어 읽어야 알아듣는다 ('이 점 구 년'). 반대로 짧은 숫자는
    # 붙여 읽는 게 자연스럽다 ('일부', '삼십칠건').
    tail = match.string[match.end() :]
    if " " in spoken and tail and "가" <= tail[0] <= "힣":
        spoken = f"{spoken} "
    return spoken


def _read_group(group: int) -> str:
    text = ""
    for position in reversed(range(4)):
        digit = (group // 10**position) % 10
        if digit == 0:
            continue
        # 십·백·천 앞의 1은 말하지 않는다. '일십'이 아니라 '십'이다.
        text += SMALL_UNITS[position] if digit == 1 and position > 0 else DIGITS[digit] + SMALL_UNITS[position]
    return text
