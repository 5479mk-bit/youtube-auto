"""1부/2부 분할 야사·비하인드 대본 생성.

1부는 유적의 기이한 디테일로 열어 공식 기록을 짚고, 설명되지 않는 의문을 남긴 채
클리프행어로 끝낸다. 2부는 그 의문에 대한 해석이나 전해지는 이야기를 들려주되,
정사(正史)와 전설을 문장에서 분명히 구분하고 출처를 밝힌 뒤 질문으로 닫는다.

facts(해설문에서 뽑은 문장)와 wiki_excerpt(위키백과 미스터리 보강) 밖의 사실은
LLM 이 지어내지 못하게 프롬프트에서 못박는다 — 대본은 이 재료 안에서만 움직인다.
"""

from __future__ import annotations

import re

from ..models import SCENE_TYPES, Script, ScriptLine, ScriptPart, StoryCandidate
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
- 1부: 첫 문장이 전부다. 기사에서 가장 기이하고 충격적인 장면 하나를 배경 설명 없이
  바로 던진다. **절대 연도, 날짜, 왕의 이름(예: "1609년", "광해군 때는")으로 시작하지
  않는다.** 마지막은 답을 주지 않고 끊는다.
- **행정 정보는 1부의 3~4번째 문장쯤에 딱 한 번만 쓴다.** "이건 언제 누가 임금에게
  보고한 내용이다"라는 출처를 밝히는 한 줄로 충분하다. 절대 첫 문장에 행정 정보를 넣지
  마라. 나머지 문장은 전부 **그날 무슨 일이 있었는지**에 쓴다. 여덟 문장 중 넷이 행정이면
  그건 장계 요약이지 이야기가 아니다.
- 2부: 1부의 의문을 한 문장으로 되짚고 바로 본론. **남은 목격담을 마저 읽는 게 아니라**
  "그래서 어떻게 됐나"로 축을 옮긴다 — 조정이 이 일을 무엇으로 받아들였는지(위 [실록 기사]의
  "분류" 가 그 단서다), 그리고 기록이 어디서 끊기는지. 마지막은 시청자에게 묻고 끝낸다.
  단, 시대 배경을 아는 척 지어내지 않는다. 이 기사에서 확인되는 것만 쓴다.
- **조정이나 사관의 속마음을 짐작하지 않는다.** "조정은 헛소문으로 치부하지 않았어",
  "사관은 전설 같은 말은 일부러 뺐지" 같은 문장은 기사 어디에도 없다. 기사에 조정이 뭘
  했는지가 안 적혀 있으면 "실록은 조정이 뭘 했는지까지는 안 적어놨어" 라고 말하면 된다.
  그게 오히려 이야기의 여운이 된다.
- **1부가 던진 질문에 2부는 반드시 정면으로 답한다.** 답이 기록에 없으면 "실록은 답을
  안 적어놨다"고 분명히 말한 뒤 이어간다. 질문을 던져놓고 다른 주제로 바꾸면 안 된다.
- 1부 합계 약 {part1_chars}자, 2부 합계 약 {part2_chars}자. **부마다 10~15개의 짧은
  문장(JSON object)으로 잘게 쪼갠다.** 문장 수가 적으면 영상 길이가 안 나온다. **실록
  원문을 인용할 때도 절대 한 문장(한 화면)에 길게 때려 넣지 말고, 1~2초 호흡으로 여러
  문장에 걸쳐 속도감 있게 나눠서 쓴다.**

[말투 — 이게 제일 중요하다]
글이 아니라 말을 쓴다. 소리 내어 읽었을 때 로봇 같거나 교양 다큐멘터리 같으면 실패다.
- **매우 짧게 끊는다 (줄글 자막 금지).** JSON의 `text` 하나는 영상에서 1~2초만 휙 지나가는
  '자막 한 칸'이다. 15~20자가 넘어가면 무조건 두 개의 문장(두 개의 JSON object)으로
  강제로 쪼갠다.
- **추임새와 감탄사 필수:** 문장 시작 부분에 "근데,", "진짜 소름 돋는 건,", "와, 미쳤지?",
  "이게 말이 돼?" 같은 구어체 추임새를 적극적으로 넣어서 진짜 사람이 썰을 푸는 것처럼
  만들어라.
- **정적과 호흡(뜸 들이기):** 시청자를 긴장시키거나 충격적인 사실을 말하기 직전에는 반드시
  말줄임표(`...`)나 쉼표(`,`)를 넣어라. (예: "하늘을 봤는데...", "거기서 갑자기,", "두
  조각이 나더니...")
- 같은 어미를 세 번 연속 쓰지 않는다. 반말로 쓴다. ("~했어", "~거든", "~였을까?")
- 기록에 적힌 사물 이름이나 행동은 원문 그대로 쓰되, 뜻만 현대어로 바꾼다. 감정을 지어내지
  않는 원칙은 동일하다.
- emphasis 에는 그 문장에서 자막으로 크게 강조할 짧은 조각만 넣는다.

[화면 — '유출된 감시 영상(Found Footage)' 콘셉트 절대 엄수]
조선 사건에는 사진이 존재하지 않는다. 대신 우리는 "조선시대 현장에 숨겨진 CCTV나
바닥에 버려진 블랙박스에 찍힌 기괴한 유출 영상"이라는 콘셉트로 화면을 만든다. 웹
이미지 검색은 쓰지 않고, 문장마다 아래 네 갈래 중 하나를 네가 직접 정해서 화면을
만든다(`scene_type`).

- "VEO": 영상 전체를 통틀어 **딱 한 문장**에만 준다. 가장 센 장면(훅)이다. 보통
  1부 첫 문장.
- "AI_IMAGE": 사건·상황 묘사. 부마다 **2~3문장**.
- "HISTORICAL_DOC": 실록 원문을 화면에 띄우는 컷. 부마다 **1~2문장**. "실록에
  이렇게 적혀 있어" 류의 문장에 준다. `doc_title`(예: 光海君日記)과
  `doc_highlight`(밑줄 그을 한자 원문 구절, 5~12자)를 같이 채운다. **원문에
  실제로 있는 구절만 쓴다.**
- "MAP_MOTION": 지명이 나오는 문장. 부마다 **1문장**. `map_region` 과
  `map_spots`(고을 이름 한글)를 채운다.

`prompt` 는 VEO/AI_IMAGE 일 때만 채우는 영문 프롬프트다.
**[경고: 피사체 실종 방지]** 단순히 어둡고 노이즈 낀 빈 화면을 그리지 마라. 프롬프트
맨 앞에는 반드시 **'사건의 주된 피사체(예: 하늘에 뜬 거대한 발광 물체, 놀란 표정의
인물들, 흙바닥에 떨어진 기이한 물건)'**를 명확하고 구체적으로 묘사해야 한다.

그 피사체 묘사 뒤에 아래의 CCTV 앵글과 스타일 문구를 붙여서 완성해라.

1) CCTV 앵글 지시 (프롬프트 중간에 삽입):
- 벽 구석에 매달린 뷰: "corner-mounted security cam looking down at"
- 숨겨진 몰래카메라 뷰: "hidden spy cam recording from a dark crevice"
- 흙바닥 블랙박스 뷰: "extreme low angle discarded dashcam perspective on the dirt
  ground"

2) CCTV 스타일 문구 (프롬프트 맨 끝에 쉼표와 함께 무조건 덧붙일 것):
  ", raw unedited CCTV security footage, authentic 1980s surveillance camera
   perspective, harsh infrared night vision lighting, overexposed blown-out
   highlights, extreme fisheye lens distortion, heavy dark vignette, brutalist
   VHS static glitch, severely desaturated monochrome, terrifying found footage
   aesthetic, no text, no watermark"

**[VEO 무브먼트 지시]**
VEO 로 가는 문장(1부 첫 문장, 가장 센 장면)은 부드러운 영화적 줌인 대신, 아래와
같은 싸구려 기계식/에러 무브먼트를 반드시 지시한다.
- "stiff robotic security camera pan with motion blur" (뚝뚝 끊기며 기계적으로
  돌아가는 감시 카메라 패닝)
- "sudden signal loss glitch and heavy VHS tracking noise" (갑자기 신호가
  끊기며 화면이 지지직거리는 극단적 노이즈)
- "creepy digital zoom-in with heavy pixelation" (화질이 픽셀 단위로 자글자글
  깨지면서 억지로 줌인)

**사실 기반 원칙은 그대로다.** 바뀌는 건 "어떻게 찍을까"(싸구려 CCTV 앵글과
조명)이지 "무슨 일이 있었나"가 아니다. 실록에 없는 장면을 지어내 그리지 않는다.

`sfx` 는 그 문장이 시작될 때 때릴 소리다. 문장이 10~15개로 늘었으니 **부마다 3~5문장에
골고루 배정**한다 — 한두 개뿐이면 나머지 구간이 너무 밋밋하고, 매 문장에 넣으면 반대로
아무 문장도 안 센 게 된다. 충격적인 장면이 몰려 있는 구간에 몰아주고, 잔잔한 설명
문장에는 넣지 않는다.
- "sub_boom": 충격적인 사실·장면 전환이 나오는 문장
- "paper_rustle": 사료(HISTORICAL_DOC) 컷 — 이 컷이 나올 때마다 넣는다
- "braam": 1부 마지막 클리프행어 한 곳에만
- "none": 나머지 전부

[출력 형식] 아래 JSON 만 출력한다.
{{
  "title_seed": "채널 제목에 쓸 한 줄",
  "part1": {{"hook_number": "화면 상단에 크게 띄울 호기심 자극 타이틀 (예: '하늘을 찢은
                              굉음', '사라진 호리병'). 절대 연도나 날짜, 회차 번호를
                              쓰지 마라.", "lines": [
    {{"text": "내레이션 한 문장 (반드시 15~20자 이내로 짧게)",
     "scene_type": "VEO | AI_IMAGE | HISTORICAL_DOC | MAP_MOTION",
     "prompt": "VEO/AI_IMAGE 일 때만: 위 스타일 문구로 끝나는 영문 프롬프트 (주의: 이미지에
                이상한 글씨가 생기지 않도록 프롬프트 끝에 항상 ', no text, no watermark'를
                붙일 것)",
     "doc_data": {{"title": "光海君日記", "highlight_text": "밑줄 그을 한자 원문 구절
                    (5~12자로 짧게)"}},
     "map_data": {{"region": "GANGWON | HANYANG | GYEONGSANG | JEOLLA | PYEONGAN",
                  "spots": ["강릉", "춘천"]}},
     "sfx": "sub_boom | braam | paper_rustle | none",
     "emphasis": ["자막에서 크게 강조할 조각"]}}
  ]}},
  "part2": {{"hook_number": "2부 화면 상단 타이틀 (예: '왕의 반응은?', '기록의 미스터리').
              역시 연도나 날짜 금지.", "lines": [ ...위와 같은 구조... ]}}
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
        _parse_line(line, candidate)
        for line in raw["lines"]
        if str(line.get("text", "")).strip()
    ]
    return ScriptPart(
        part=part,
        hook_number=_hook_number(raw.get("hook_number"), candidate),
        lines=lines,
    )


def _parse_line(raw: dict, candidate: StoryCandidate) -> ScriptLine:
    """문장 하나. 화면 종류(scene_type)와 그에 딸린 값들을 같이 받는다.

    LLM 이 갈래 이름을 틀리게 주는 일이 있어(소문자, 없는 이름) 모르는 값은 AI_IMAGE 로
    떨어뜨린다 — 화면이 비는 것보다 낫다.
    """
    text = str(raw["text"]).strip()
    doc = raw.get("doc_data") or {}
    map_data = raw.get("map_data") or {}
    scene_type = str(raw.get("scene_type", "")).strip().upper()
    if scene_type not in SCENE_TYPES:
        scene_type = "AI_IMAGE"

    return ScriptLine(
        text=text,
        visual_query_ko=str(raw.get("visual_query_ko") or _first(candidate.image_queries_ko)),
        visual_query_en=str(raw.get("visual_query_en") or _first(candidate.image_queries_en)),
        # 낭독문은 LLM 이 준 걸 쓰지 않고 직접 만든다. 큰 수를 소리대로 적게 하면
        # 틀리기 쉬운데, 숫자 읽기는 규칙이 정해져 있으니 계산하는 편이 맞다.
        spoken_text=read_aloud(text),
        show_chart=False,
        emphasis=[str(item) for item in raw.get("emphasis", []) if str(item).strip()],
        scene_type=scene_type,
        prompt=str(raw.get("prompt") or "").strip(),
        doc_title=str(doc.get("title") or "").strip(),
        doc_highlight=str(doc.get("highlight_text") or "").strip(),
        map_region=str(map_data.get("region") or "").strip().upper(),
        map_spots=[str(spot).strip() for spot in map_data.get("spots", []) if str(spot).strip()],
        sfx=_sfx(raw.get("sfx")),
    )


def _sfx(raw: object) -> str:
    name = str(raw or "none").strip().lower()
    return name if name in ("sub_boom", "braam", "paper_rustle") else "none"


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


# 생성 컷의 고정 스타일. LLM 이 프롬프트를 못 준 컷에도 이 톤은 유지된다.
CINEMATIC_STYLE = (
    "corner-mounted security cam looking down at, raw unedited CCTV security "
    "footage, authentic 1980s surveillance camera perspective, harsh infrared "
    "night vision lighting, overexposed blown-out highlights, extreme fisheye "
    "lens distortion, heavy dark vignette, brutalist VHS static glitch, "
    "severely desaturated monochrome, terrifying found footage aesthetic, "
    "no text, no watermark"
)
FALLBACK_IMAGE_PROMPT = (
    f"a strange glowing object hanging in the night sky over a Joseon village, "
    f"{CINEMATIC_STYLE}"
)

_HANJA = re.compile(r"[\u4e00-\u9fff]{2,}")


def _first_hanja_phrase(text: str, max_chars: int = 10) -> str:
    """국역 안에 괄호로 붙어 있는 한자 중 첫 구절. 사료 컷에 띄울 글자다."""
    match = _HANJA.search(text or "")
    return match.group(0)[:max_chars] if match else ""


def _sillok_template_script(candidate: StoryCandidate) -> Script:
    """LLM 없이 실록 기사만으로 짜는 대본.

    국역 문장을 그대로 나눠 쓴다 — 여기서 말을 바꾸면 그게 곧 오역이 된다. 다듬는 건
    LLM 이 할 일이고, 이 폴백은 "사실을 안 건드리는 것"만 지킨다.
    """
    queries_ko = candidate.image_queries_ko or ["조선왕조실록"]
    queries_en = candidate.image_queries_en or ["Annals of the Joseon Dynasty"]
    when = candidate.headline_number or "조선"
    facts = [fact for fact in candidate.facts if fact]

    def line(
        index: int,
        text: str,
        emphasis: list[str] | None = None,
        scene_type: str = "AI_IMAGE",
        sfx: str = "none",
    ) -> ScriptLine:
        return ScriptLine(
            text=text,
            spoken_text=read_aloud(text),
            visual_query_ko=queries_ko[index % len(queries_ko)],
            visual_query_en=queries_en[index % len(queries_en)],
            show_chart=False,
            emphasis=emphasis or [],
            scene_type=scene_type,
            # 폴백이라 프롬프트를 지어내지 않는다. 생성 컷은 고정 스타일만 준다.
            prompt=FALLBACK_IMAGE_PROMPT if scene_type in ("VEO", "AI_IMAGE") else "",
            doc_title=candidate.headline_number,
            doc_highlight=_first_hanja_phrase(candidate.context),
            sfx=sfx,
        )

    part1_lines = [line(0, f"{when}, 실록에 이런 게 적혀 있어.", emphasis=[when], scene_type="VEO")]
    part1_lines += [line(i, fact) for i, fact in enumerate(facts[:3], start=1)]
    part1_lines.append(
        line(
            4,
            "지어낸 얘기가 아니야. 실록 원문에 있는 말이야.",
            emphasis=["실록 원문"],
            scene_type="HISTORICAL_DOC",
            sfx="paper_rustle",
        )
    )
    part1_lines.append(line(5, "그래서 이게 뭐였을까?", emphasis=["뭐였을까"], sfx="braam"))

    part2_lines = [line(0, "1부에서 남긴 질문이었지.")]
    part2_lines += [line(i, fact) for i, fact in enumerate(facts[3:7], start=1)]
    part2_lines.append(
        line(
            5,
            "실록은 여기까지만 적어놨어. 답은 안 남겼고.",
            emphasis=["여기까지만"],
            scene_type="HISTORICAL_DOC",
            sfx="sub_boom",
        )
    )
    part2_lines.append(line(6, "너는 이게 뭐였을 것 같아?"))

    return Script(
        topic_key=candidate.topic_key,
        title_seed=candidate.headline,
        part1=ScriptPart(part=1, hook_number=when, lines=part1_lines),
        part2=ScriptPart(part=2, hook_number=when, lines=part2_lines),
        source_note=f"출처: {candidate.source_name}",
        is_sample=candidate.is_sample,
    )
