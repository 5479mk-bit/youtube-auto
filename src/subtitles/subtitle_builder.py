"""음성 길이를 기준으로 자막 타임코드와 줄바꿈을 만든다.

쇼츠는 대부분 무음으로 재생되므로 자막이 곧 본문이다. 한 화면에 두 줄을 넘기지
않도록 어절 단위로 접고, 강조 조각은 렌더러가 색을 달리 칠할 수 있게 따로 들고 간다.

VEO 문장은 이제 8초를 채우려고 40~55음절로 길어졌다 — 그 긴 문장을 통째로 한
화면에 띄워 8초 내내 고정해두면(줄이 4~5개로 쌓여) "말과 안 맞고 뭉텅이로
나온다"는 인상을 준다. 그래서 한 문장을 화면 두 줄씩 여러 묶음(청크)으로 나누고,
각 묶음이 그 글자 수 비율만큼만 화면에 떠 있게 시간을 쪼갠다 — 말하는 속도와
자막 전환이 맞아떨어진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import ScriptPart

MAX_SCREEN_LINES = 2  # 한 화면에 띄우는 자막 줄 수 상한


@dataclass
class SubtitleCue:
    start: float
    end: float
    lines: list[str]
    emphasis: list[str]


def build_cues(
    part: ScriptPart, max_chars_per_line: int, suppress_texts: list[str] | None = None
) -> list[SubtitleCue]:
    suppressed = {_key(text) for text in (suppress_texts or []) if text}

    cues: list[SubtitleCue] = []
    cursor = 0.0
    for line in part.lines:
        if _key(line.text) not in suppressed:
            cues.extend(
                _line_to_cues(line.text, line.emphasis, cursor, line.duration, max_chars_per_line)
            )
        cursor += line.duration
    return cues


def _line_to_cues(
    text: str, emphasis: list[str], start: float, duration: float, max_chars_per_line: int
) -> list[SubtitleCue]:
    """한 문장을 화면 단위(최대 2줄) 묶음으로 쪼개고, 글자 수 비율로 시간을 나눈다."""
    wrapped = wrap_text(text, max_chars_per_line)
    chunks = [
        wrapped[i : i + MAX_SCREEN_LINES] for i in range(0, len(wrapped), MAX_SCREEN_LINES)
    ]
    if len(chunks) <= 1:
        return [SubtitleCue(start=start, end=start + duration, lines=wrapped, emphasis=emphasis)]

    weights = [sum(len(text_line) for text_line in chunk) for chunk in chunks]
    total_weight = sum(weights) or 1

    cues: list[SubtitleCue] = []
    cursor = start
    for index, (chunk, weight) in enumerate(zip(chunks, weights)):
        is_last = index == len(chunks) - 1
        chunk_end = start + duration if is_last else cursor + duration * (weight / total_weight)
        cues.append(SubtitleCue(start=cursor, end=chunk_end, lines=chunk, emphasis=emphasis))
        cursor = chunk_end
    return cues


def _key(text: str) -> str:
    return text.strip().rstrip(".?! ").strip()


def wrap_text(text: str, max_chars_per_line: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars_per_line and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def write_srt(cues: list[SubtitleCue], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n"
            + "\n".join(cue.lines)
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _timestamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
