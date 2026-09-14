"""1부/2부 분할 대본 생성.

1부는 숫자를 던지고 질문을 남기고, 2부는 그 이유를 데이터로 답한다. 각 문장은
화면에서 무엇을 보여줄지(이미지 검색어/차트 여부)와 자막에서 무엇을 강조할지를
함께 들고 다닌다.
"""

from __future__ import annotations

from ..models import Script, ScriptLine, ScriptPart, StoryCandidate
from ..text_ko import particle as _particle
from .llm_client import LLMProvider

PROMPT = """당신은 공공데이터를 다루는 한국어 다큐멘터리 쇼츠 작가입니다.
아래 통계 하나를 30~40초짜리 세로 쇼츠 2편으로 나눠 씁니다.

[통계]
제목 후보: {headline}
핵심 숫자: {headline_number}
대상: {subject}
배경: {context}
데이터: {series}
출처: {source_name}

[작성 규칙]
- 1부: 첫 문장은 반드시 핵심 숫자 하나만 던지는 3초짜리 훅. 이어서 문제를 제기하고,
  데이터를 펼친 뒤, 마지막 문장은 답을 주지 않고 "왜?"로 끝나는 클리프행어.
- 2부: 첫 문장은 1부 핵심 숫자를 짧게 되짚고, 이유를 데이터로 설명한 뒤,
  마지막 문장에서 시청자에게 자기 동네/상황을 떠올리게 하는 질문으로 닫는다.
- 문장은 한 문장당 12~28자. 1부 합계 약 {part1_chars}자, 2부 합계 약 {part2_chars}자.
- 과장·추측 금지. 데이터에 없는 수치를 지어내지 말 것.
- visual_query_ko 는 한국 공공데이터 사진 API 로 찾을 한국어 검색어. 지명·장소·시설 이름처럼
  실제로 사진이 있을 법한 말로 쓴다 (예: "남대문시장", "강남 거리 야경").
- visual_query_en 은 같은 장면을 영어로 쓴 보조 검색어 2~4단어.
- emphasis 에는 그 문장에서 자막으로 크게 강조할 짧은 조각(숫자·핵심어)만 넣는다.
- show_chart 는 그 문장에서 데이터 차트를 띄울지 여부.

[출력 형식] 아래 JSON 만 출력한다.
{{
  "title_seed": "채널 제목에 쓸 한 줄",
  "part1": {{"hook_number": "화면에 크게 띄울 숫자", "lines": [
    {{"text": "", "visual_query_ko": "", "visual_query_en": "", "show_chart": false, "emphasis": [""]}}
  ]}},
  "part2": {{"hook_number": "", "lines": [
    {{"text": "", "visual_query_ko": "", "visual_query_en": "", "show_chart": false, "emphasis": [""]}}
  ]}}
}}
"""


def generate(
    candidate: StoryCandidate,
    provider: LLMProvider | None,
    part1_chars: int,
    part2_chars: int,
    temperature: float,
) -> Script:
    if provider is not None:
        prompt = PROMPT.format(
            headline=candidate.headline,
            headline_number=candidate.headline_number,
            subject=candidate.subject,
            context=candidate.context,
            series=", ".join(
                f"{point.label} {point.value:,.1f}{candidate.series_unit}"
                for point in candidate.series
            ),
            source_name=candidate.source_name,
            part1_chars=part1_chars,
            part2_chars=part2_chars,
        )
        raw = provider.complete_json(prompt, temperature)
        script = _parse(raw, candidate)
        if script is not None:
            return script

    return _template_script(candidate)


def _parse(raw: dict | None, candidate: StoryCandidate) -> Script | None:
    if not raw:
        return None
    try:
        part1 = _parse_part(raw["part1"], 1, candidate)
        part2 = _parse_part(raw["part2"], 2, candidate)
    except (KeyError, TypeError, ValueError):
        return None

    if not part1.lines or not part2.lines:
        return None

    return Script(
        topic_key=candidate.topic_key,
        title_seed=str(raw.get("title_seed") or candidate.headline),
        part1=part1,
        part2=part2,
        source_note=f"출처: {candidate.source_name}",
        is_sample=candidate.is_sample,
    )


def _parse_part(raw: dict, part: int, candidate: StoryCandidate) -> ScriptPart:
    lines = [
        ScriptLine(
            text=str(line["text"]).strip(),
            visual_query_ko=str(
                line.get("visual_query_ko") or _first(candidate.image_queries_ko)
            ),
            visual_query_en=str(
                line.get("visual_query_en") or _first(candidate.image_queries_en)
            ),
            show_chart=bool(line.get("show_chart", False)),
            emphasis=[str(item) for item in line.get("emphasis", []) if str(item).strip()],
        )
        for line in raw["lines"]
        if str(line.get("text", "")).strip()
    ]
    return ScriptPart(
        part=part,
        hook_number=str(raw.get("hook_number") or candidate.headline_number),
        lines=lines,
    )


def _first(items: list[str]) -> str:
    return items[0] if items else ""


def _fmt(value: float) -> str:
    if abs(value) >= 100 or float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:,.1f}"


def _template_script(candidate: StoryCandidate) -> Script:
    """LLM 없이도 파이프라인이 끝까지 돌도록 데이터만으로 구성하는 대본."""
    queries_ko = candidate.image_queries_ko or ["대한민국 도시"]
    queries_en = candidate.image_queries_en or ["korean city"]

    def query_ko(index: int) -> str:
        return queries_ko[index % len(queries_ko)]

    def query_en(index: int) -> str:
        return queries_en[index % len(queries_en)]

    top = candidate.series[0]
    bottom = candidate.series[-1]
    unit = candidate.series_unit

    middle = candidate.series[len(candidate.series) // 2]
    gap = abs(top.value - bottom.value)

    part1 = ScriptPart(
        part=1,
        hook_number=candidate.headline_number,
        lines=[
            ScriptLine(
                text=f"{candidate.headline_number}.",
                visual_query_ko=query_ko(0),
                visual_query_en=query_en(0),
                emphasis=[candidate.headline_number],
            ),
            ScriptLine(
                text=f"{candidate.subject}에서 나온 숫자입니다.",
                visual_query_ko=query_ko(1),
                visual_query_en=query_en(1),
                emphasis=[candidate.subject],
            ),
            ScriptLine(
                text="그냥 지나칠 숫자가 아닙니다.",
                visual_query_ko=query_ko(2),
                visual_query_en=query_en(2),
            ),
            ScriptLine(
                text=f"{_particle(top.label, '이', '가')} {_fmt(top.value)}{_particle(unit, '으로', '로')} 가장 앞섭니다.",
                visual_query_ko=query_ko(3),
                visual_query_en=query_en(3),
                show_chart=True,
                emphasis=[top.label, f"{_fmt(top.value)}{unit}"],
            ),
            ScriptLine(
                text=f"중간에 놓인 {_particle(middle.label, '은', '는')} {_fmt(middle.value)}{unit}입니다.",
                visual_query_ko=query_ko(4),
                visual_query_en=query_en(4),
                show_chart=True,
                emphasis=[middle.label],
            ),
            ScriptLine(
                text=f"끝에 선 {_particle(bottom.label, '은', '는')} {_fmt(bottom.value)}{unit}까지 내려갑니다.",
                visual_query_ko=query_ko(5),
                visual_query_en=query_en(5),
                show_chart=True,
                emphasis=[bottom.label, f"{_fmt(bottom.value)}{unit}"],
            ),
            ScriptLine(
                text=f"양 끝의 차이는 {_fmt(gap)}{unit}입니다.",
                visual_query_ko=query_ko(6),
                visual_query_en=query_en(6),
                show_chart=True,
                emphasis=[f"{_fmt(gap)}{unit}"],
            ),
            ScriptLine(
                text="같은 조건인데 왜 이렇게 갈릴까요?",
                visual_query_ko=query_ko(7),
                visual_query_en=query_en(7),
                emphasis=["왜"],
            ),
        ],
    )

    part2 = ScriptPart(
        part=2,
        hook_number=candidate.headline_number,
        lines=[
            ScriptLine(
                text=f"{candidate.headline_number}, 1부에서 본 숫자입니다.",
                visual_query_ko=query_ko(0),
                visual_query_en=query_en(0),
                emphasis=[candidate.headline_number],
            ),
            ScriptLine(
                text=f"{_particle(candidate.subject, '을', '를')} 다시 펼쳐봅니다.",
                visual_query_ko=query_ko(1),
                visual_query_en=query_en(1),
                show_chart=True,
                emphasis=[candidate.subject],
            ),
            ScriptLine(
                text=f"{_particle(top.label, '과', '와')} {_particle(bottom.label, '은', '는')} 같은 표에 있습니다.",
                visual_query_ko=query_ko(2),
                visual_query_en=query_en(2),
                show_chart=True,
                emphasis=[top.label, bottom.label],
            ),
            ScriptLine(
                text=f"그런데 격차는 {_fmt(gap)}{unit}까지 벌어집니다.",
                visual_query_ko=query_ko(3),
                visual_query_en=query_en(3),
                show_chart=True,
                emphasis=[f"{_fmt(gap)}{unit}"],
            ),
            ScriptLine(
                text="이 숫자는 추정이 아니라 집계된 기록입니다.",
                visual_query_ko=query_ko(4),
                visual_query_en=query_en(4),
            ),
            ScriptLine(
                text=f"{candidate.source_name} 자료입니다.",
                visual_query_ko=query_ko(5),
                visual_query_en=query_en(5),
                emphasis=[candidate.source_name],
            ),
            ScriptLine(
                text="숫자는 결론이 아니라 질문에 가깝습니다.",
                visual_query_ko=query_ko(6),
                visual_query_en=query_en(6),
            ),
            ScriptLine(
                text="당신의 동네는 어느 쪽에 서 있나요?",
                visual_query_ko=query_ko(7),
                visual_query_en=query_en(7),
                emphasis=["당신의 동네"],
            ),
        ],
    )

    return Script(
        topic_key=candidate.topic_key,
        title_seed=candidate.headline,
        part1=part1,
        part2=part2,
        source_note=f"출처: {candidate.source_name}",
        is_sample=candidate.is_sample,
    )
