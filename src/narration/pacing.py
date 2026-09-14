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

HOOK_RATE = "-8%"  # 첫 문장은 천천히
BODY_RATE = "+6%"
CLOSING_RATE = "-4%"  # 닫는 질문도 천천히


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
