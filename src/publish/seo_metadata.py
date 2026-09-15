"""후킹 제목 후보와 설명·태그를 만든다.

제목은 세 개를 만든다. 텔레그램 검수에서 사람이 고를 수 있어야 하기 때문이다.
설명에는 반드시 출처를 적는다. 데이터 채널의 신뢰는 거기서 나온다.
"""

from __future__ import annotations

from ..models import Script, ScriptPart, StoryCandidate, VideoMetadata
from ..script.llm_client import LLMProvider
from ..text_ko import particle

PROMPT = """다음 쇼츠 영상의 유튜브 제목 후보 3개와 설명을 쓴다.

주제: {title_seed}
화면 문구: {hook_number}
대상: {subject}
부: {part}부 (전체 2부작)
내레이션: {narration}

규칙:
- 제목은 28자 이내. 숫자를 앞쪽에 배치하고, 낚시가 아니라 실제 내용과 일치해야 한다.
- 과장 금지. 데이터에 없는 주장 금지.
- 설명은 3줄 이내. 마지막 줄에 출처를 적는다.

JSON 만 출력한다:
{{"titles": ["", "", ""], "description": "", "tags": ["", ""]}}
"""


def generate(
    script: Script,
    part: ScriptPart,
    candidate: StoryCandidate,
    provider: LLMProvider | None,
    default_tags: list[str],
) -> VideoMetadata:
    narration = " ".join(line.text for line in part.lines)

    if provider is not None:
        raw = provider.complete_json(
            PROMPT.format(
                title_seed=script.title_seed,
                hook_number=part.hook_number,
                subject=candidate.subject,
                part=part.part,
                narration=narration,
            ),
            temperature=0.8,
        )
        metadata = _parse(raw, script, part, candidate, default_tags)
        if metadata is not None:
            return metadata

    return _template_metadata(script, part, candidate, default_tags)


def _parse(
    raw: dict | None,
    script: Script,
    part: ScriptPart,
    candidate: StoryCandidate,
    default_tags: list[str],
) -> VideoMetadata | None:
    if not raw:
        return None
    titles = [str(title).strip() for title in raw.get("titles", []) if str(title).strip()]
    description = str(raw.get("description", "")).strip()
    if not titles or not description:
        return None

    titles = [_with_part_suffix(title, part.part) for title in titles]
    tags = [str(tag).strip() for tag in raw.get("tags", []) if str(tag).strip()]

    return VideoMetadata(
        title=titles[0],
        title_alternatives=titles[1:],
        description=_with_source(description, script, candidate),
        tags=list(dict.fromkeys(tags + default_tags))[:15],
    )


def _template_metadata(
    script: Script, part: ScriptPart, candidate: StoryCandidate, default_tags: list[str]
) -> VideoMetadata:
    titles = [
        _with_part_suffix(f"{part.hook_number}, {script.title_seed}", part.part),
        _with_part_suffix(f"{script.title_seed} ({part.hook_number})", part.part),
        _with_part_suffix(f"{candidate.subject}에 얽힌 이야기", part.part),
    ]
    description = f"{particle(candidate.subject, '을', '를')} 국가유산청 해설과 위키백과로 들여다봤습니다."

    return VideoMetadata(
        title=titles[0],
        title_alternatives=titles[1:],
        description=_with_source(description, script, candidate),
        tags=list(dict.fromkeys(default_tags))[:15],
    )


def _with_part_suffix(title: str, part: int) -> str:
    suffix = f" ({part}부)"
    trimmed = title[: 100 - len(suffix)]
    return f"{trimmed}{suffix}"


def _with_source(description: str, script: Script, candidate: StoryCandidate) -> str:
    lines = [description, "", script.source_note or f"출처: {candidate.source_name}"]
    if candidate.source_url:
        lines.append(candidate.source_url)
    if script.is_sample:
        lines.insert(0, "[샘플 데이터로 만든 테스트 영상입니다. 실제 해설문이 아닙니다.]")
    return "\n".join(lines)
