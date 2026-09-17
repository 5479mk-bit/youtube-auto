"""1부/2부 분할 야사·비하인드 대본 생성.

1부는 유적의 기이한 디테일로 열어 공식 기록을 짚고, 설명되지 않는 의문을 남긴 채
클리프행어로 끝낸다. 2부는 그 의문에 대한 해석이나 전해지는 이야기를 들려주되,
정사(正史)와 전설을 문장에서 분명히 구분하고 출처를 밝힌 뒤 질문으로 닫는다.

facts(해설문에서 뽑은 문장)와 wiki_excerpt(위키백과 미스터리 보강) 밖의 사실은
LLM 이 지어내지 못하게 프롬프트에서 못박는다 — 대본은 이 재료 안에서만 움직인다.
"""

from __future__ import annotations

from ..models import Script, ScriptLine, ScriptPart, StoryCandidate
from ..text_ko import particle as _particle
from ..text_ko import read_aloud
from .llm_client import LLMProvider

HERITAGE_PROMPT = """당신은 스크롤을 멈추게 만드는 한국어 쇼츠 작가입니다.
아래 유적/문화재 하나를 30~40초짜리 세로 쇼츠 2편으로 나눠 씁니다.

시청자는 3초 안에 넘길지 말지 정합니다. 교양 프로그램 해설이 아니라, 친구한테 "야 이거
알아?" 하고 말 거는 톤으로 씁니다.

[문화유산]
이름: {headline}
지정 등급: {headline_number}
대상: {subject}
공식 해설(사실): {facts}
위키백과 보강(전설·논란 등, 사실과 구분 필요): {wiki_excerpt}
출처: {source_name}

[사실 검증 규칙 — 반드시 지킬 것]
- "공식 해설" 목록에 없는 사실을 지어내지 않는다. 숫자·연도·이름을 임의로 바꾸지 않는다.
- "위키백과 보강" 내용은 전설·설화·논란일 수 있다. 반드시 "전해지는 말로는", "~라는 이야기가
  있다", "학계에서는 ~라는 해석도 있다" 처럼 사실이 아닐 수 있음을 문장에서 드러낸다.
- 위키백과 보강이 비어 있으면 억지로 미스터리를 만들지 말고, 공식 해설 안에서 아직 설명되지
  않은 디테일(왜 이런 구조인지, 왜 이 자리인지)로 의문을 제기한다.

[작성 규칙]
- 1부: 첫 문장이 전부다. 가장 이상하고 믿기 힘든 디테일 하나를 배경 설명 없이 바로 던진다.
  "OO는 신라시대 유적입니다" 같은 소개로 시작하면 실패다. 이어서 공식 기록을 짧게 짚고,
  설명 안 되는 부분을 파고든 뒤, 마지막은 답을 주지 않고 끊는다.
- 2부: 1부의 의문을 한 문장으로 되짚고 바로 본론. 학계 해석이나 전해지는 이야기를 들려준 뒤,
  사실과 전설의 경계를 분명히 하고 출처를 밝힌다. 마지막은 시청자에게 묻고 끝낸다.
- **1부가 던진 질문에 2부는 반드시 정면으로 답한다.** 1부에서 "이건 어디로 사라졌을까"로
  끊었으면 2부는 그 사라진 것부터 다뤄야 한다. 자료에 답이 없으면 "기록에 남아있지 않다"고
  분명히 말한 뒤 다른 이야기로 넘어간다. 질문을 던져놓고 슬쩍 다른 주제로 바꾸면 안 된다.
- 1부 합계 약 {part1_chars}자, 2부 합계 약 {part2_chars}자.

[말투 — 이게 제일 중요하다]
글이 아니라 말을 쓴다. 소리 내어 읽었을 때 어색하면 틀린 것이다.
- 짧게 끊는다. 한 문장에 정보 하나. 20자 넘어가면 자를 데가 있는지 본다.
- 문장 길이를 섞는다. "근데 이상하죠." 같은 6자 문장과 25자 문장이 번갈아 나와야 말이 된다.
- 같은 어미를 세 번 연속 쓰지 않는다. "~습니다"만 반복하면 읽는 티가 난다.
- 백과사전 문체 금지: "~로 알려져 있다", "~라 할 수 있다", "~을 자랑한다", "~의 진수를
  보여준다" 같은 말은 쓰지 않는다.
- 숫자와 구체적인 물건 이름이 추상적인 형용사보다 세다. "뛰어난 기술"보다 "돌 360개".
- emphasis 에는 그 문장에서 자막으로 크게 강조할 짧은 조각만 넣는다.

[사진 검색어 — 없는 사진을 지어내지 말 것]
- visual_query_ko 는 실제로 사진이 존재해야 한다. 유적의 **정식 이름을 그대로** 쓰는 게
  가장 안전하다 (예: "석굴암", "첨성대", "불국사").
- "OO 전경", "OO 내부", "OO 표지석" 처럼 이름 뒤에 말을 붙여 지어내지 않는다. 그런 검색어는
  실제로 사진이 없어서 화면이 비거나 엉뚱한 사진이 들어간다.
- 유적 안의 유명한 개별 대상은 괜찮다 (예: "석굴암 본존불").
- visual_query_en 은 같은 대상의 영어 표기 2~4단어 (예: "Seokguram", "Cheomseongdae").

[출력 형식] 아래 JSON 만 출력한다.
{{
  "title_seed": "채널 제목에 쓸 한 줄",
  "part1": {{"hook_number": "화면에 크게 띄울 짧은 문구", "lines": [
    {{"text": "", "visual_query_ko": "", "visual_query_en": "", "emphasis": [""]}}
  ]}},
  "part2": {{"hook_number": "", "lines": [
    {{"text": "", "visual_query_ko": "", "visual_query_en": "", "emphasis": [""]}}
  ]}}
}}
"""


SILLOK_PROMPT = """당신은 스크롤을 멈추게 만드는 한국어 쇼츠 작가입니다.
아래는 조선왕조실록에 실제로 실린 기사 하나입니다. 이걸 30~40초짜리 세로 쇼츠 2편으로
나눠 씁니다.

시청자는 3초 안에 넘길지 말지 정합니다. 교양 프로그램 해설이 아니라, 친구한테 "야 이거
실록에 진짜 적혀 있는 거야" 하고 말 거는 톤으로 씁니다.

[실록 기사]
제목: {headline}
때: {headline_number}
분류: {categories}
국역 전문(공식 번역, 이게 유일한 사실 재료다):
{context}

[사실 검증 규칙 — 반드시 지킬 것]
- 국역 전문에 없는 사실을 지어내지 않는다. 날짜·지명·인명·숫자를 바꾸지 않는다.
- 실록은 "보고받은 내용"을 적은 기록이다. 기사에 적힌 것이 곧 실제로 일어난 일이라고
  단정하지 않는다. "기록에는 이렇게 적혀 있다"는 층위를 지킨다.
- 현대적 해석(외계인, UFO, 초자연 등)을 사실처럼 말하지 않는다. 궁금증은 던지되 답을
  지어내지 않는다. 기록에 없는 건 "실록은 여기까지만 적어놨다"고 말한다.
- 목격자·장소·시각처럼 원문에 박힌 구체적인 것이 가장 센 재료다. 그걸 앞으로 꺼낸다.

[작성 규칙]
- 1부: 첫 문장이 전부다. 기사에서 가장 믿기 힘든 장면 하나를 배경 설명 없이 바로 던진다.
  "조선시대에는~" 같은 시대 소개로 시작하면 실패다. 이어서 언제·어디서·누가 보고했는지
  짧게 짚고, 마지막은 답을 주지 않고 끊는다.
- 2부: 1부의 의문을 한 문장으로 되짚고 바로 본론. 기사에 남은 나머지 내용을 들려주고,
  기록이 어디서 끊기는지 분명히 말한 뒤 시청자에게 묻고 끝낸다.
- **1부가 던진 질문에 2부는 반드시 정면으로 답한다.** 답이 기록에 없으면 "실록은 답을
  안 적어놨다"고 분명히 말한 뒤 이어간다. 질문을 던져놓고 다른 주제로 바꾸면 안 된다.
- 1부 합계 약 {part1_chars}자, 2부 합계 약 {part2_chars}자.

[말투 — 이게 제일 중요하다]
글이 아니라 말을 쓴다. 소리 내어 읽었을 때 어색하면 틀린 것이다.
- 짧게 끊는다. 한 문장에 정보 하나. 20자 넘어가면 자를 데가 있는지 본다.
- 문장 길이를 섞는다. "근데 이상하죠." 같은 6자 문장과 25자 문장이 번갈아 나와야 말이 된다.
- 같은 어미를 세 번 연속 쓰지 않는다.
- 백과사전 문체 금지: "~로 알려져 있다", "~라 할 수 있다", "~을 자랑한다" 같은 말은 안 쓴다.
- **반말로 쓴다.** "~했어", "~거든", "~였을까?" 처럼. "~습니다"로 끝내지 않는다.
- 국역의 옛 표현은 그대로 읽지 말고 지금 말로 바꾼다. 단, 뜻을 바꾸면 안 된다.
  (예: "치계하였다" → "장계를 올렸다", "사시(巳時)" → "오전 아홉 시에서 열한 시 사이")
- 기록에 적힌 사물 이름을 다른 물건으로 바꾸지 않는다. "동이"를 "항아리"로, "세숫대야"를
  "접시"로 바꾸면 그 순간 실록이 아니라 창작이 된다. 모르면 원문 표현을 그대로 쓴다.
- emphasis 에는 그 문장에서 자막으로 크게 강조할 짧은 조각만 넣는다.

[사진 검색어 — 이번 소재의 가장 어려운 부분]
실록 사건에는 사진이 없다. 하늘에 나타난 물체를 찍은 사진은 존재하지 않는다.
그러니 **사건과 연결된 실존 대상**으로 검색어를 만든다.
- 쓸 수 있는 것: 궁궐 이름("경복궁", "창덕궁"), 실록 책자("조선왕조실록", "정족산사고"),
  옛 그림·고지도("동궐도", "대동여지도"), 기사에 나오는 실제 지명("강릉", "춘천").
- 쓰면 안 되는 것: 장면을 묘사한 말("하늘의 붉은 물체", "밤하늘의 괴광"). 그런 사진은
  존재하지 않아 화면이 비거나 엉뚱한 사진이 들어간다.
- visual_query_en 은 같은 대상의 영어 표기 2~4단어 (예: "Gyeongbokgung", "Donggwoldo").

[출력 형식] 아래 JSON 만 출력한다.
{{
  "title_seed": "채널 제목에 쓸 한 줄",
  "part1": {{"hook_number": "화면에 크게 띄울 짧은 문구 (예: \"1609년 강원도\", \"광해 1년\").
                              \"01\" 같은 회차 번호가 아니다", "lines": [
    {{"text": "", "visual_query_ko": "", "visual_query_en": "", "emphasis": [""]}}
  ]}},
  "part2": {{"hook_number": "", "lines": [
    {{"text": "", "visual_query_ko": "", "visual_query_en": "", "emphasis": [""]}}
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
        raw = provider.complete_json(_prompt(candidate, part1_chars, part2_chars), temperature)
        script = _parse(raw, candidate)
        if script is not None:
            return script

    return (
        _sillok_template_script(candidate)
        if candidate.dataset == "sillok"
        else _template_script(candidate)
    )


def _prompt(candidate: StoryCandidate, part1_chars: int, part2_chars: int) -> str:
    """소재에 맞는 프롬프트를 고른다.

    문화재는 "이름/지정 등급/해설문", 실록은 "왕대·날짜/국역 전문"이라 재료의 모양이
    아예 다르다. `dataset` 하나로 갈린다.
    """
    if candidate.dataset == "sillok":
        return SILLOK_PROMPT.format(
            headline=candidate.headline,
            headline_number=candidate.headline_number,
            categories=", ".join(candidate.topic_tags) or "(없음)",
            context=candidate.context,
            part1_chars=part1_chars,
            part2_chars=part2_chars,
        )

    return HERITAGE_PROMPT.format(
        headline=candidate.headline,
        headline_number=candidate.headline_number,
        subject=candidate.subject,
        facts="\n".join(f"- {fact}" for fact in candidate.facts) or "(없음)",
        wiki_excerpt=candidate.wiki_excerpt or "(없음)",
        source_name=candidate.source_name,
        part1_chars=part1_chars,
        part2_chars=part2_chars,
    )


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
            # 낭독문은 LLM 이 준 걸 쓰지 않고 직접 만든다. 큰 수를 소리대로 적게 하면
            # 틀리기 쉬운데, 숫자 읽기는 규칙이 정해져 있으니 계산하는 편이 맞다.
            spoken_text=read_aloud(str(line["text"]).strip()),
            show_chart=False,
            emphasis=[str(item) for item in line.get("emphasis", []) if str(item).strip()],
        )
        for line in raw["lines"]
        if str(line.get("text", "")).strip()
    ]
    return ScriptPart(
        part=part,
        hook_number=_hook_number(raw.get("hook_number"), candidate),
        lines=lines,
    )


def _hook_number(raw: object, candidate: StoryCandidate) -> str:
    """화면에 크게 띄울 문구. LLM 이 "01" 같은 회차 번호를 주는 일이 있어 걸러낸다."""
    text = str(raw or "").strip()
    if not text or text.isdigit():
        return candidate.headline_number
    return text


def _first(items: list[str]) -> str:
    return items[0] if items else ""


def _template_script(candidate: StoryCandidate) -> Script:
    """LLM 없이도 파이프라인이 끝까지 돌도록 facts/wiki_excerpt 만으로 구성하는 대본.

    소리 내어 읽었을 때 어색하지 않은 게 기준이다. 문장 길이를 섞고, 어미를 돌려 쓰고,
    사실(facts)과 전설(wiki_excerpt)을 문장에서 분명히 구분한다.
    """
    queries_ko = candidate.image_queries_ko or [candidate.subject or "대한민국 문화유산"]
    queries_en = candidate.image_queries_en or ["korean heritage site"]
    subject = candidate.subject or candidate.headline
    grade = candidate.headline_number or "문화유산"
    facts = candidate.facts or ([candidate.context.strip()] if candidate.context else [])
    facts = [f for f in facts if f]

    def line(index: int, text: str, emphasis: list[str] | None = None) -> ScriptLine:
        return ScriptLine(
            text=text,
            spoken_text=read_aloud(text),
            visual_query_ko=queries_ko[index % len(queries_ko)],
            visual_query_en=queries_en[index % len(queries_en)],
            show_chart=False,
            emphasis=emphasis or [],
        )

    part1_lines = [
        line(0, f"{_particle(grade, '으로', '로')} 지정된 {subject}, 그런데 아직 풀리지 않은 게 있어요.",
             emphasis=[grade, subject]),
    ]
    for i, fact in enumerate(facts[:3], start=1):
        part1_lines.append(line(i, fact))
    part1_lines.append(line(4, "여기까진 기록이에요."))
    if candidate.wiki_excerpt:
        part1_lines.append(
            line(5, "그런데 전해지는 말로는, 설명이 다르다고 해요.", emphasis=["전해지는 말로는"])
        )
    else:
        part1_lines.append(
            line(5, "그런데 왜 하필 이 모습인지는, 지금도 정확히 설명되지 않아요.")
        )
    part1_lines.append(line(6, "이유가 뭘까요?", emphasis=["이유"]))

    part2_lines = [
        line(0, f"{subject}, 1부에서 남긴 의문이었죠.", emphasis=[subject]),
    ]
    if candidate.wiki_excerpt:
        excerpt_sentence = candidate.wiki_excerpt.split(".")[0].strip()
        for prefix in ("전해지는 말로는,", "전해지는 말로는", "일설에는", "일부에서는"):
            if excerpt_sentence.startswith(prefix):
                excerpt_sentence = excerpt_sentence[len(prefix):].strip()
                break
        if excerpt_sentence:
            part2_lines.append(
                line(1, f"전해지는 말로는, {excerpt_sentence}고 해요.", emphasis=["전해지는 말로는"])
            )
        part2_lines.append(line(2, "다만 이건 기록이 아니라 이야기로 전해지는 부분이에요."))
    else:
        part2_lines.append(line(1, "학계에서도 이 부분은 여러 해석이 갈려요."))
        part2_lines.append(line(2, "확정된 답은 아직 없어요."))
    for i, fact in enumerate(facts[3:5], start=3):
        part2_lines.append(line(i, fact))
    part2_lines.append(
        line(5, f"{candidate.source_name} 해설을 바탕으로 전해드렸어요.", emphasis=[candidate.source_name])
    )
    part2_lines.append(line(6, "정사와 전설, 어느 쪽이 더 마음에 드시나요?", emphasis=["정사와 전설"]))

    part1 = ScriptPart(part=1, hook_number=grade, lines=part1_lines)
    part2 = ScriptPart(part=2, hook_number=grade, lines=part2_lines)

    return Script(
        topic_key=candidate.topic_key,
        title_seed=candidate.headline,
        part1=part1,
        part2=part2,
        source_note=f"출처: {candidate.source_name}",
        is_sample=candidate.is_sample,
    )


def _sillok_template_script(candidate: StoryCandidate) -> Script:
    """LLM 없이 실록 기사만으로 짜는 대본.

    국역 문장을 그대로 나눠 쓴다 — 여기서 말을 바꾸면 그게 곧 오역이 된다. 다듬는 건
    LLM 이 할 일이고, 이 폴백은 "사실을 안 건드리는 것"만 지킨다.
    """
    queries_ko = candidate.image_queries_ko or ["조선왕조실록"]
    queries_en = candidate.image_queries_en or ["Annals of the Joseon Dynasty"]
    when = candidate.headline_number or "조선"
    facts = [fact for fact in candidate.facts if fact]

    def line(index: int, text: str, emphasis: list[str] | None = None) -> ScriptLine:
        return ScriptLine(
            text=text,
            spoken_text=read_aloud(text),
            visual_query_ko=queries_ko[index % len(queries_ko)],
            visual_query_en=queries_en[index % len(queries_en)],
            show_chart=False,
            emphasis=emphasis or [],
        )

    part1_lines = [line(0, f"{when}, 실록에 이런 게 적혀 있어.", emphasis=[when])]
    part1_lines += [line(i, fact) for i, fact in enumerate(facts[:3], start=1)]
    part1_lines.append(line(4, "지어낸 얘기가 아니야. 실록 원문에 있는 말이야.", emphasis=["실록 원문"]))
    part1_lines.append(line(5, "그래서 이게 뭐였을까?", emphasis=["뭐였을까"]))

    part2_lines = [line(0, "1부에서 남긴 질문이었지.")]
    part2_lines += [line(i, fact) for i, fact in enumerate(facts[3:7], start=1)]
    part2_lines.append(line(5, "실록은 여기까지만 적어놨어. 답은 안 남겼고.", emphasis=["여기까지만"]))
    part2_lines.append(line(6, "너는 이게 뭐였을 것 같아?"))

    return Script(
        topic_key=candidate.topic_key,
        title_seed=candidate.headline,
        part1=ScriptPart(part=1, hook_number=when, lines=part1_lines),
        part2=ScriptPart(part=2, hook_number=when, lines=part2_lines),
        source_note=f"출처: {candidate.source_name}",
        is_sample=candidate.is_sample,
    )
