"""음성 길이를 기준으로 자막 타임코드와 줄바꿈을 만든다.

쇼츠는 대부분 무음으로 재생되므로 자막이 곧 본문이다. 한 화면에 두 줄을 넘기지
않도록 어절 단위로 접고, 강조 조각은 렌더러가 색을 달리 칠할 수 있게 따로 들고 간다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import ScriptPart


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
            cues.append(
                SubtitleCue(
                    start=cursor,
                    end=cursor + line.duration,
                    lines=wrap_text(line.text, max_chars_per_line),
                    emphasis=line.emphasis,
                )
            )
        cursor += line.duration
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
