"""문장마다 쉼과 속도를 배정한다.

같은 목소리라도 처음부터 끝까지 같은 속도로, 숨도 안 쉬고 읽으면 기계가 읽는 것처럼
들린다. 사람은 중요한 숫자 뒤에서 쉬고, 질문을 던질 때 느려진다.

여기서 정한 쉼은 음원 뒤에 무음으로 붙어 문장 길이에 포함되므로, 자막 타임코드와 컷
길이가 자동으로 따라온다.
"""

from __future__ import annotations

from ..models import ScriptPart

HOOK_PAUSE = 0.5  # 훅 숫자가 꽂히도록 한 박자 쉰다
SENTENCE_PAUSE = 0.25
CLIFFHANGER_PAUSE = 0.6  # 질문을 던지기 전에 뜸을 들인다
CLOSING_PAUSE = 0.4
LAST_LINE_PAUSE = 0.3  # 끝나자마자 잘리면 급하게 들린다

# 0.85~0.9배속으로 낮췄더니 "멍청하게 느리고 자꾸 쉰다"는 반응이 나왔다 — 실제
# 샘플을 들어보고 1.1배속(기본보다 살짝 빠름)이 자연스럽다고 확인됐다. 문장 종류별
# 완급은 그 근처(1.05~1.1)에서만 준다.
HOOK_RATE = "+5%"  # 1.05배속 — 첫 문장은 살짝 무게감 있게
BODY_RATE = "+10%"  # 1.10배속 — 사용자가 청취로 확인한 자연스러운 속도
CLOSING_RATE = "+5%"  # 1.05배속


def _rate_to_percent(rate: str) -> float:
    """"-15%"/"+5%" 같은 문자열을 숫자(-15.0/5.0)로 바꾼다. 테스트에서 상대 속도
    비교에 쓴다."""
    return float(rate.strip().rstrip("%"))


def apply_pacing(part: ScriptPart) -> ScriptPart:
    last_index = len(part.lines) - 1

    for index, line in enumerate(part.lines):
        is_hook = index == 0
        is_last = index == last_index
        is_before_last = index == last_index - 1

        if is_hook:
            line.speech_rate = HOOK_RATE
            line.pause_after = HOOK_PAUSE
        elif is_last:
            line.speech_rate = CLOSING_RATE
            line.pause_after = LAST_LINE_PAUSE
        else:
            line.speech_rate = BODY_RATE
            line.pause_after = SENTENCE_PAUSE

        if is_before_last:
            # 1부의 클리프행어, 2부의 마지막 질문 앞에서 한 번 더 뜸을 들인다.
            line.pause_after = CLIFFHANGER_PAUSE if part.part == 1 else CLOSING_PAUSE

    return part
