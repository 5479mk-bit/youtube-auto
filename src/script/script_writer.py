"""1부/2부 분할 대본 생성.

1부는 숫자를 던지고 질문을 남기고, 2부는 그 이유를 데이터로 답한다. 각 문장은
화면에서 무엇을 보여줄지(이미지 검색어/차트 여부)와 자막에서 무엇을 강조할지를
함께 들고 다닌다.
"""

from __future__ import annotations

from ..models import Script, ScriptLine, ScriptPart, StoryCandidate
from ..text_ko import particle as _particle
from ..text_ko import read_aloud
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
- 1부 합계 약 {part1_chars}자, 2부 합계 약 {part2_chars}자.
- 과장·추측 금지. 데이터에 없는 수치를 지어내지 말 것.

[말투 — 이게 제일 중요하다]
글이 아니라 말을 쓴다. 소리 내어 읽었을 때 어색하면 틀린 것이다.
- 문장 길이를 섞는다. 6자짜리 짧은 문장과 25자짜리 긴 문장이 번갈아 나와야 말처럼 들린다.
- 같은 어미를 세 번 연속 쓰지 않는다. "~입니다. ~입니다. ~입니다." 는 읽는 티가 난다.
- 명사만 나열하지 않는다. 사람이 실제로 하는 말로 쓴다.
- text 에는 숫자를 그대로 쓴다 (예: "2.9년", "12,480곳"). 소리 나는 대로 적지 말 것.
  낭독용 문장은 시스템이 따로 만든다.
- visual_query_ko 는 한국 공공데이터 사진 API 로 찾을 한국어 검색어. 지명·장소·시설 이름처럼
  실제로 사진이 있을 법한 말로 쓴다 (예: "남대문시장", "강남 거리 야경").
- visual_query_en 은 같은 장면을 영어로 쓴 보조 검색어 2~4단어.
- emphasis 에는 그 문장에서 자막으로 크게 강조할 짧은 조각(숫자·핵심어)만 넣는다.
- show_chart 는 그 문장에서 데이터 차트를 띄울지 여부.

[출력 형식] 아래 JSON 만 출력한다.
{{
  "title_seed": "채널 제목에 쓸 한 줄",
  "part1": {{"hook_number": "화면에 크게 띄울 숫자", "lines": [
    {{"text": "", "visual_query_ko": "", "visual_query_en": "",
      "show_chart": false, "emphasis": [""]}}
  ]}},
  "part2": {{"hook_number": "", "lines": [
    {{"text": "", "visual_query_ko": "", "visual_query_en": "",
      "show_chart": false, "emphasis": [""]}}
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
            # 낭독문은 LLM 이 준 걸 쓰지 않고 직접 만든다. 모델이 큰 수를 소리대로
            # 적게 하면 '육억'을 '유걱'이라고 쓰는 식으로 틀리는데, 그대로 읽히면
            # 말이 되지 않는다. 숫자 읽기는 규칙이 정해져 있으니 계산하는 편이 맞다.
            spoken_text=read_aloud(str(line["text"]).strip()),
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


def _runner_up_line(line, candidate: StoryCandidate, unit: str) -> list:
    """2위 항목. 항목이 넷 이상일 때만 말한다. 셋 이하면 중간값과 겹친다."""
    if len(candidate.series) < 4:
        return []
    runner_up = candidate.series[1]
    return [
        line(
            4,
            f"바로 뒤가 {_particle(runner_up.label, '이', '가')} {_fmt(runner_up.value)}{unit},",
            chart=True,
            emphasis=[runner_up.label],
        )
    ]


def _ratio_line(line, top_value: float, bottom_value: float) -> list:
    """몇 배 차이인지는 계산해서 말한다. 0 으로 나눌 수 없거나 차이가 미미하면 말하지 않는다."""
    smaller, larger = sorted((abs(top_value), abs(bottom_value)))
    if smaller <= 0 or larger / smaller < 1.2:
        return []
    ratio = larger / smaller
    return [line(4, f"{_fmt(ratio)}배 차이예요.", chart=True, emphasis=[f"{_fmt(ratio)}배"])]


def _template_script(candidate: StoryCandidate) -> Script:
    """LLM 없이도 파이프라인이 끝까지 돌도록 데이터만으로 구성하는 대본.

    소리 내어 읽었을 때 어색하지 않은 게 기준이다. 문장 길이를 섞고, 어미를 돌려 쓰고,
    숫자는 낭독용으로 따로 적는다.
    """
    queries_ko = candidate.image_queries_ko or ["대한민국 도시"]
    queries_en = candidate.image_queries_en or ["korean city"]

    top = candidate.series[0]
    bottom = candidate.series[-1]
    middle = candidate.series[len(candidate.series) // 2]
    unit = candidate.series_unit
    gap = abs(top.value - bottom.value)

    def line(index: int, text: str, chart: bool = False, emphasis: list[str] | None = None):
        return ScriptLine(
            text=text,
            spoken_text=read_aloud(text),
            visual_query_ko=queries_ko[index % len(queries_ko)],
            visual_query_en=queries_en[index % len(queries_en)],
            show_chart=chart,
            emphasis=emphasis or [],
        )

    number = candidate.headline_number
    part1 = ScriptPart(
        part=1,
        hook_number=number,
        lines=[
            line(0, f"{number}.", emphasis=[number]),
            line(1, f"{_particle(candidate.subject, '을', '를')} 들여다본 숫자예요.",
                 emphasis=[candidate.subject]),
            line(2, "잠깐 볼까요."),
            line(3, f"같은 기준으로 잰 {len(candidate.series)}개를 나란히 놓으면,"),
            line(4, f"{_particle(top.label, '이', '가')} {_fmt(top.value)}{_particle(unit, '으로', '로')} 가장 앞이고,",
                 chart=True, emphasis=[top.label, f"{_fmt(top.value)}{unit}"]),
            *_runner_up_line(line, candidate, unit),
            line(5, f"{_particle(middle.label, '은', '는')} {_fmt(middle.value)}{unit},",
                 chart=True, emphasis=[middle.label]),
            line(6, f"{_particle(bottom.label, '은', '는')} {_fmt(bottom.value)}{unit}까지 갑니다.",
                 chart=True, emphasis=[bottom.label, f"{_fmt(bottom.value)}{unit}"]),
            line(7, f"양 끝이 {_fmt(gap)}{unit} 차이 나요.",
                 chart=True, emphasis=[f"{_fmt(gap)}{unit}"]),
            line(8, "한 해 이야기가 아닙니다."),
            line(9, "같은 조건인데, 왜 이렇게 갈릴까요?", emphasis=["왜"]),
        ],
    )

    part2 = ScriptPart(
        part=2,
        hook_number=number,
        lines=[
            line(0, f"{number}. 1부에서 본 숫자죠.", emphasis=[number]),
            line(1, "다시 펼쳐봅니다.", chart=True),
            line(2, f"{_particle(top.label, '과', '와')} {_particle(bottom.label, '은', '는')} 같은 표에 있는데,",
                 chart=True, emphasis=[top.label, bottom.label]),
            line(3, f"격차는 {_fmt(gap)}{unit}.", chart=True, emphasis=[f"{_fmt(gap)}{unit}"]),
            *_ratio_line(line, top.value, bottom.value),
            line(5, f"가운데 있는 {_particle(middle.label, '은', '는')} {_fmt(middle.value)}{unit}.",
                 chart=True, emphasis=[middle.label]),
            line(6, "추정이 아니라 집계된 기록이에요."),
            line(7, f"{candidate.source_name} 자료입니다.", emphasis=[candidate.source_name]),
            line(7, "어느 쪽이 맞는지는 숫자가 말해주지 않습니다."),
            line(8, "숫자는 답이 아니라 질문에 가깝죠."),
            line(9, "당신 동네는 어느 쪽인가요?", emphasis=["당신 동네"]),
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
