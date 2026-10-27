"""1부/2부 분할 야사·비하인드 대본 생성.

1부는 유적의 기이한 디테일로 열어 공식 기록을 짚고, 설명되지 않는 의문을 남긴 채
클리프행어로 끝낸다. 2부는 그 의문에 대한 해석이나 전해지는 이야기를 들려주되,
정사(正史)와 전설을 문장에서 분명히 구분하고 출처를 밝힌 뒤 질문으로 닫는다.

facts(해설문에서 뽑은 문장)와 wiki_excerpt(위키백과 미스터리 보강) 밖의 사실은
LLM 이 지어내지 못하게 프롬프트에서 못박는다 — 대본은 이 재료 안에서만 움직인다.
"""

from __future__ import annotations

import re

from loguru import logger

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

[화면 — '고품질 실사 역사 재난 드라마' 콘셉트 절대 엄수]
조선 사건에는 사진이 존재하지 않는다. 대신 우리는 "영화 한 장면처럼 극적이고
선명한, 포토리얼리스틱한 역사 재난 드라마"라는 콘셉트로 화면을 만든다. 웹
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
**[경고: 피사체 실종 방지]** 단순히 텅 빈 배경만 그리지 마라. 프롬프트 맨 앞에는
반드시 **'사건의 주된 피사체(예: 굶주려 쓰러진 백성들, 곡식을 나눠 주는 관리,
말라붙은 논밭, 눈보라 속 피난민)'**를 명확하고 구체적으로 묘사해야 한다.

그 피사체 묘사 뒤에 아래의 카메라 구도와 스타일 문구를 붙여서 완성해라.

1) 영화적 카메라 구도 (프롬프트 중간에 삽입, 장면마다 다르게 골라 단조롭지 않게):
- 압도적 스케일을 보여주는 부감/드론 샷: "sweeping aerial drone shot revealing"
- 인물 눈높이의 몰입형 로우앵글: "dramatic low-angle shot looking up at"
- 감정을 클로즈업하는 구도(얼굴은 피하고 손·발·소지품 위주): "intimate close-up on
  weathered hands clutching"

2) 영화적 재난 드라마 스타일 문구 (프롬프트 맨 끝에 쉼표와 함께 무조건 덧붙일 것):
  ", cinematic historical disaster drama, photorealistic 8k detail, dramatic
   stormy sky with heavy clouds and lightning, warm torchlight against cold
   famine-stricken landscape, rich saturated color grading, epic movie still,
   painterly realism, no text, no watermark"

**[VEO 무브먼트 지시]**
VEO 로 가는 문장(1부 첫 문장, 가장 센 장면)은 아래와 같은 웅장한 영화적 무브먼트를
반드시 지시한다.
- "slow dramatic push-in revealing the scale of the disaster" (재해의 규모가
  드러나도록 천천히 다가가는 줌인)
- "sweeping cinematic pan across the devastated landscape" (황폐해진 땅을
  가로지르는 웅장한 패닝)
- "slow motion with dust and embers drifting through the air" (먼지와 잔불이
  느리게 흩날리는 슬로모션)

**사실 기반 원칙은 그대로다.** 바뀌는 건 "어떻게 찍을까"(영화적 구도와 조명)이지
"무슨 일이 있었나"가 아니다. 실록에 없는 장면을 지어내 그리지 않는다.

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


# 조회수 부진에 대한 특단의 조치(사용자 지시, 2026-09-30): 채널 언어를 한국어에서
# 스페인어로 전환한다. 아래 GLOBAL_DISASTER_PROMPT_KO_UNUSED 는 폐기가 아니라
# 설정으로 빠진 것뿐이다 — 나중에 한국어로 되돌릴 수도 있으니 지우지 않는다.
# 실제로 쓰이는 건 그 아래 스페인어판 GLOBAL_DISASTER_PROMPT다.
GLOBAL_DISASTER_PROMPT_KO_UNUSED = """당신은 3D 엔지니어링 인포그래픽 스타일의 공학 해설 채널의 한국어 쇼츠 작가입니다.
아래는 위키백과에 실린 실제 재난·사고 하나입니다. **이 영상은 90초 안에 끝나는 완결된
세로 쇼츠 한 편입니다 — 두 편으로 나뉘어 따로 올라가는 시리즈가 아닙니다.** 아래
[구조]에서 편의상 "1부"/"2부"라고 부르는 건 글 쓰는 사람 기준의 내부 구간 구분일
뿐이고, 시청자는 처음부터 끝까지 끊김 없이 이어지는 하나의 영상으로 봅니다.

**절대 규칙**: 생성하는 어떤 문장에도 "1부", "2부", "다음 편", "다음 이야기",
"이어서 알아보겠습니다", "2부에서 확인", "계속" 처럼 영상이 여러 편으로 나뉜다는
암시를 주는 표현을 쓰지 않습니다. [구조]의 "1부 마지막 문장은 궁금한 지점에서
끊는다"는 지시는 문장 안에서 궁금증만 남기고 다음 문장(같은 영상 안에서 바로
이어짐)으로 자연스럽게 넘어가라는 뜻이지, "다음 편을 봐 달라"는 안내가 아닙니다.

톤의 기준은 "차분한데 멈출 수 없는 공학 해설"입니다. 흥분한 친구 말투가 아니라, 믿을 만한
해설자가 담담하게 사실을 쌓아 올리다 한 번씩 뒤집는 방식입니다.

[소재]
제목: {headline}
위키백과 본문(이게 유일한 사실 재료다):
{context}

[사실 검증 규칙 — 반드시 지킬 것]
- 본문에 없는 사실을 지어내지 않는다. 날짜·지명·인명·숫자를 바꾸지 않는다.
- 사망자 수·높이·무게·길이처럼 본문에 박힌 구체적인 숫자가 가장 센 재료다. 그걸 앞으로
  꺼내고, 가능하면 체감되는 비교("아파트 30층을 훌쩍 넘는 높이죠")로 한 번 더 풀어준다.
  비교에 쓰는 기준(아파트 한 층 약 3m 등)은 상식 수준에서만 쓴다.
- **대한민국 내에서 발생한 사건이나 인재(人災)는 절대 다루지 마시오.** 글로벌
  자연재해나 해외의 역사적 공학 실패 사례만 다루시오. 소재 자체가 한국 국내 사건이면
  이 프롬프트를 거부하고 빈 결과를 내야 한다.

[구조 — 레퍼런스 채널의 흐름을 그대로 따른다]
1부:
- **첫 문장**: 지금(또는 사고 직전)의 모습을 장면으로 던진다. 배경 설명 없이
  "여기 ○○에는 한때 ~던 ~가 있습니다" 처럼 바로 장면에 세운다.
- **둘째~셋째 문장**: 뒤집는다. "그런데 이 ○○, ~가 아닙니다" — 시청자가 예상한 것과
  반대되는 사실 하나.
- 그다음 **숫자 하나를 체감시키고**, "이 사고의 문제는 딱 한 줄로 요약됩니다" 같은
  **한 줄 요약**으로 이야기의 축을 못박는다.
- 이어서 시간 순서대로 어떻게 여기까지 왔는지 쌓는다. 중반에 "그랬더니 이번엔 다른 게
  터졌습니다" 류의 **문제 연쇄**를 한 번 넣는다.
- 1부 마지막 문장은 답을 주지 않고 가장 궁금한 지점에서 끊는다.
2부:
- 첫 문장에서 1부의 의문을 한 문장으로 되짚고 바로 본론.
- **왜 그런 공학적·구조적 실패가 일어났는지**를 구조물 내부를 들여다보듯 설명한다
  (본문에 근거가 있는 범위 안에서). "~라고요", "~거든요"로 이유를 짧게 붙인다.
- 후반부에 가장 결정적인 사실 하나를 클라이맥스로 둔다.
- 마지막은 현재진행형 여운으로 닫는다("그리고 이 문제는 지금도 끝나지 않았습니다" 류 —
  본문에 근거가 있을 때만). 근거가 없으면 담담한 결론 한 문장으로 닫는다.
- **1부가 던진 질문에 2부는 반드시 정면으로 답한다.** 답이 본문에 없으면 "정확한 원인은
  아직도 밝혀지지 않았습니다"라고 분명히 말한 뒤 이어간다.
- 1부 합계 약 {part1_chars}자, 2부 합계 약 {part2_chars}자. **부마다 8~10개의
  문장(JSON object)** 으로 쓴다. **글자 수 목표가 다른 모든 규칙보다 우선한다** —
  현재 내레이션 속도로는 이 글자 수를 넘기면 영상이 90초 상한을 넘어간다. 문장을
  다 쓰고 나서 글자 수를 스스로 세어보고, 목표를 20% 넘게 넘어섰으면 문장을 합치거나
  덜 중요한 문장을 통째로 뺀다(단, [구조]의 필수 지점 — 첫 문장 훅, 반전, 클라이맥스,
  마지막 문장 — 는 빼지 않는다).

[말투 — 이게 제일 중요하다]
- **합니다체가 기본이다.** 문장의 절반 이상은 "~습니다/~입니다"로 끝낸다.
- 사이사이 설명을 덧붙일 때만 "~거든요", "~이죠", "~고요", "~는데요"를 섞어 리듬을 만든다.
  같은 어미를 세 번 연속 쓰지 않는다.
- **반말 금지.** "~야", "~거야", "~지?" 같은 어미를 쓰지 않는다.
- **감탄사·간투사 금지.** "와", "야", "진짜", "미쳤", "소름 돋는" 같은 말을 쓰지 않는다.
  놀라움은 사실과 숫자 자체로 전달한다.
- 문장 하나는 **8~16음절**(소리 내어 읽는 기준, 약 1.5~3초)로 짧게 끊는다. 말줄임표는
  쓰지 않고, 쉼표는 한 문장에 최대 하나.
- 문장과 문장은 끊김 없이 이어서 읽힌다. "그런데", "그래서", "그랬더니" 같은 접속어로
  다음 문장을 끌고 가는 건 좋다(문장 맨 앞 한 단어, 감탄사가 아니다).
- emphasis 에는 그 문장에서 자막으로 강조할 짧은 조각만 넣는다.
- `overlay` 에는 그 문장에서 **화면 한가운데 크게 박을 숫자나 짧은 단어**(예: "1978",
  "98m", "1,514명", "균열")를 넣는다. 부마다 **3~4문장에만** 넣고 나머지는 빈 문자열.
  반드시 그 문장 안에 실제로 나오는 숫자·단어여야 한다.

[화면 — '3D 엔지니어링 디오라마' 콘셉트 절대 엄수]
실제 사진을 쓰지 않는다. 사고 현장을 **회색 격자 스튜디오 바닥 위에 올린 3D 미니어처
디오라마**로 만들고, 필요한 순간 **땅·선체·벽을 잘라낸 단면(cutaway)** 으로 속을 보여준다.
문장마다 아래 두 갈래 중 하나를 정한다(`scene_type`).

- "VEO": 실제로 움직이는 4초짜리 영상. **부마다 3~4문장**, 되도록 AI_IMAGE 와 번갈아
  배치한다(VEO 가 세 번 연속 이어지지 않게). 1부 첫 문장은 반드시 VEO. 물이 흐르고, 가스가
  차오르고, 구조물이 갈라지는 **움직임이 핵심인 문장**에 준다.
- "AI_IMAGE": 나머지. 렌더 단계에서 카메라 이동이 자동으로 걸린다.

화면은 문장 순서에 따라 **세 가지 거리를 오간다** — 같은 거리를 세 번 연속 쓰지 않는다.
- 원경: 디오라마 전체를 내려다보는 항공 시점("aerial view of a miniature diorama of ...")
- 단면: 구조물을 잘라 내부 층·배관·물길을 보여주는 시점("cross-section cutaway of ...")
- 근접: 균열·기포·계기판 바늘 같은 디테일 클로즈업("macro close-up of ...")

`prompt` 는 VEO/AI_IMAGE 일 때만 채우는 영문 프롬프트다.
**[경고: 피사체 실종 방지]** 프롬프트 맨 앞에는 반드시 **그 문장이 말하는 구조물·장면**을
구체적으로 묘사한다(예: "cross-section cutaway of the Titanic hull with seawater flooding
the forward compartments"). 그 뒤에 **빨간 기술 주석**을 장면에 맞게 한 줄 지시한다
("bold red arrows showing the water flow, red dimension lines measuring the hull breach,
red pressure gauge" 처럼 — 화살표·치수선·게이지·X 표시 중 장면에 맞는 것).
그리고 맨 끝에 아래 스타일 문구를 쉼표와 함께 그대로 덧붙인다:
  ", stylized 3D miniature diorama on a neutral gray grid studio floor, clean
   clay-render materials, soft studio lighting, bold red technical annotations,
   photoreal engineering explainer, 8k, no watermark"

**[VEO 무브먼트 지시]**
VEO 문장의 prompt 에는 아래 중 장면에 맞는 카메라 무브먼트를 하나 넣는다. 카메라는
부드럽게 움직이고, **움직임은 장면 안의 물·연기·잔해·기포가 만든다.**
- "slow aerial drone push-in toward ..." (원경에서 천천히 다가가기)
- "slow orbit around the diorama" (디오라마 주위를 천천히 돌기)
- "smooth dolly along the cross-section" (단면을 따라 옆으로 미끄러지기)
- "slow push-in to macro detail" (디테일로 천천히 파고들기)

**사실 기반 원칙은 그대로다.** 바뀌는 건 "어떻게 보여줄까"이지 "무슨 일이 있었나"가
아니다. 본문에 없는 장면을 지어내 그리지 않는다.

`sfx` 는 그 문장(컷)이 시작될 때 때릴 소리다. **"효과음이 난잡하다"는 지적이 있었다** —
매 컷마다 넣지 않는다. 부마다 **2~3문장에만**, 가장 센 장면(반전·숫자 공개·클라이맥스)에
골라 배정하고, **같은 소리를 연속 두 문장에 붙이지 않는다**(사이에 최소 한 문장은 "none").
잔잔한 설명 문장에는 넣지 않는다 — 나머지 문장은 전부 "none"이고, 그게 정상이다.
- "sub_boom": 충격적인 사실·숫자 공개, 큰 장면 전환
- "crack": 구조물이 갈라지거나 부서지는 순간
- "whoosh": 물·가스·잔해가 흐르거나 카메라가 이동하는 전환
- "alarm": 경보·위험 신호, 대피 상황
- "braam": 1부 마지막 클리프행어 한 곳에만
- "none": 나머지 전부(대부분의 문장이 여기 속해야 한다)

[출력 형식] 아래 JSON 만 출력한다.
{{
  "title_seed": "이 이야기를 한 줄로 요약한 문구. 채널명·브랜드명은 절대 쓰지 않는다"
            "('신비한 건축사전' 같은 표현 금지 — 이 대본은 특정 채널 브랜드에 묶이지 않는다).",
  "part1": {{"hook_number": "화면 상단에 띄울 호기심 자극 타이틀. 연도·날짜·회차 번호 금지. '(상)'/'(하)'/'1부'/'2부' 같은 회차 구분 표기도 절대 쓰지 마라 — 영상 하나로 합쳐 쓰인다.",
             "lines": [
    {{"text": "내레이션 한 문장 (합니다체, 12~22음절)",
     "scene_type": "VEO | AI_IMAGE",
     "prompt": "위 스타일 문구로 끝나는 영문 프롬프트",
     "sfx": "sub_boom | crack | whoosh | alarm | braam | none",
     "overlay": "화면 중앙에 크게 띄울 숫자/단어 또는 빈 문자열",
     "emphasis": ["자막에서 강조할 조각"]}}
  ]}},
  "part2": {{"hook_number": "part1 과 같은 자리에 쓰일 타이틀 후보(실제로는 part1 것만 쓰인다). 역시 연도·날짜·회차 구분 표기 금지.",
              "lines": [ ...위와 같은 구조... ]}}
}}
"""


GLOBAL_DISASTER_PROMPT = """Eres el guionista principal de "Secretos del Colapso", un canal de Shorts
verticales en español latinoamericano sobre desastres reales. Tu único objetivo: que nadie deslice
en el primer segundo, que lo vean entero y que lo vuelvan a ver. Escribes para alguien que va
pasando videos con el pulgar y te da medio segundo.

El video dura **35 a 45 segundos** y es una sola pieza continua. Del mismo guion sale también
una **versión extendida de más de 60 segundos para TikTok** (ver [VERSIÓN TIKTOK]). Abajo, "parte 1"/"parte 2" son
solo una división interna del guion — **nunca** escribas "parte 1", "parte 2", "continuará",
"en el próximo video" ni nada que sugiera otra entrega.

[MATERIAL]
Título: {headline}
Pistas: {hints}
Texto de Wikipedia (puede estar en español o en coreano; si está en coreano, tradúcelo con
exactitud). Es la **única** fuente de hechos:
{context}

[REGLAS DE VERACIDAD — no negociables]
- No inventes nada que no esté en el texto. Fechas, lugares, nombres y cifras, exactos.
- Los números concretos (muertos, minutos, metros, toneladas) son tu arma más fuerte. Si puedes,
  hazlos sentir con una comparación de sentido común ("más alto que un edificio de 30 pisos").
- Si el texto menciona una advertencia ignorada, un error humano, algo ocultado o una pregunta
  sin respuesta, es tu mejor material. Si no lo menciona, no lo inventes: usa la ironía de los
  hechos reales (lo que creían seguro y no lo era).
- **Nunca trates un desastre ocurrido dentro de Corea del Sur.** Si el material lo es, no
  generes nada.

[ESTRUCTURA — apertura en caliente (cold open), siempre así]
1. **Frase 1 (segundo 0)** — elige UNA de estas dos aperturas:
   **A. ¿Y si...?** (la que más comparten los canales grandes de misterio en español): si el texto
   menciona una teoría, una sospecha, algo nunca explicado o un fenómeno que podría repetirse, abre
   con la pregunta prohibida: "¿Y si el Titanic no se hundió por un accidente?" / "¿Qué pasaría si
   esta tormenta solar golpeara hoy?". 6 a 12 palabras.
   **B. El momento más brutal**, en presente y sin contexto.
   Empieza en plena catástrofe con un número o una imagen imposible de ignorar. 6 a 10 palabras.
   Ejemplos del tono: "En 18 horas, una ciudad entera queda sepultada viva." / "El barco
   'imposible de hundir' tiene dos horas de vida." **Prohibido** empezar con contexto, fechas
   de calendario, "Aquí, en...", "Hubo una vez", "En este video", saludos o preguntas genéricas.
   Si en Pistas hay un aniversario, úsalo en la frase 2 o 3 con la cifra exacta de la pista
   ("Hace 41 años, este volcán...") — nunca "hoy" ni "un día como hoy" (cada quien lo ve un día
   distinto).
2. **Frase 2: la brecha de curiosidad.** Algo que obligue a quedarse: "Y lo peor: pudo
   evitarse." / "Pero nadie escuchó la advertencia." / la pregunta corta del gancho.
3. **Rebobina** ("Horas antes...", "Todo empezó con...") y cuenta en orden, en **presente
   histórico**, cada frase subiendo la tensión. Detalles concretos y sensoriales: lo que se ve,
   se oye, la hora exacta.
4. **A mitad del video, re-gancho** para frenar el abandono: "Y es aquí donde todo se vuelve
   extraño." / "Lo más inquietante es que..." / "Lo perturbador es que..." / "Pero eso no fue lo
   peor."
5. **Clímax**: el hecho más decisivo, con su número. Si el fenómeno puede repetirse (sismos,
   volcanes, tormentas solares, presas), conéctalo con el presente solo si el texto lo respalda:
   "Y los científicos dicen que puede volver a pasar."
6. **Última frase: una pregunta directa al espectador que lo obligue a comentar y que enlace con
   la frase 1** (bucle): "¿Accidente... o algo más?" / "¿Tú habrías escapado?" / "¿Tú qué crees?".
   Nada de despedidas, "suscríbete" ni moralejas largas.

[TEORÍAS Y MISTERIOS — lo que más se comparte, con una regla de hierro]
- Si el texto menciona teorías, sospechas, advertencias ignoradas, encubrimientos o cosas nunca
  explicadas, son tu mejor material: dales espacio y tensión.
- **Siempre como teoría, nunca como hecho**: "hay quienes creen que...", "una teoría dice que...",
  "nunca se supo por qué...". Los hechos comprobados, como hechos; las sospechas, como sospechas.
- Si el texto no menciona ninguna teoría, **no la inventes**: usa la pregunta sin respuesta que
  dejan los hechos reales.
- Prohibido: teorías sobre política actual, vacunas o salud, y acusar de un crimen a una persona
  viva.

**Cifra en pantalla (`hook_stat` de la parte 1)**: la cifra más brutal del texto en **máximo 3
palabras y en mayúsculas** ("200.000 MUERTOS", "8.6 GRADOS", "36 TONELADAS", "2 HORAS"). Sale
enorme en amarillo encima de la pregunta **desde el fotograma 0** — es lo que detiene el pulgar
aunque el video esté sin sonido. El número tiene que estar **literalmente** en el texto, y la frase 1
o la 2 lo debe decir. Si el texto no tiene ninguna cifra impactante, cadena vacía.

**Gancho en pantalla (`hook_number` de la parte 1)**: una pregunta de **máximo 4 palabras** que
sale en grande en el centro durante los 3 primeros segundos y luego queda arriba. Debe picar
sin explicar: "¿Por qué nadie huyó?", "¿Quién dio la orden?", "¿Sabías esto?". En español
latinoamericano, directa.

Longitud (versión YouTube, solo frases normales): parte 1 unos {part1_chars} caracteres, parte 2
unos {part2_chars} caracteres. **Cada parte, entre 6 y 7 frases normales (objetos JSON).** El
límite de caracteres manda sobre todo lo demás: si te pasas más de un 15%, fusiona o elimina
frases (nunca la frase 1, el re-gancho, el clímax ni la última).

[VERSIÓN TIKTOK — frases extra]
TikTok solo paga videos de **más de 60 segundos**. Además de las frases normales, intercala en cada
parte **{extra_lines} frases extra** marcadas con `"solo_tiktok": true` (unos {extra_chars} caracteres
por parte). La versión YouTube las quita; la de TikTok las incluye todas en su lugar.
- Aportan **más hechos concretos del texto**: cifras, horas, nombres, lo que vieron los testigos,
  un segundo detalle inquietante. Nunca relleno ni repetición.
- **Nunca** son la frase 1, la 2, el re-gancho, el clímax ni la última frase.
- Las frases normales deben entenderse perfectamente **sin** las extra: ninguna frase normal
  puede referirse a algo que solo dice una frase extra.
- Las frases extra usan `scene_type` "AI_IMAGE" o "FOTO" (nunca "VEO") y `sfx` "none".

[EL ÁNGULO — por qué los videos de este nicho llegan a millones]
Los videos de misterio en español que pasan del millón (ej. "¿El hundimiento del Titanic fue
realmente un accidente?": 600 mil vistas, 24 mil compartidos) no cuentan la historia que todos ya
conocen: toman un suceso **que todos reconocen en medio segundo** y revelan **lo que casi nadie
sabe**. Reconocer + descubrir = no se puede deslizar. Contar otra vez lo conocido = se desliza.
1. Antes de escribir, elige el ángulo y escríbelo en `angulo`: "Todos creen que ___, pero casi
   nadie sabe que ___". La segunda parte sale **del texto**: una advertencia ignorada, una
   coincidencia inquietante, una decisión humana que lo causó, algo ocultado, una teoría (como
   teoría), el detalle extraño de un sobreviviente.
2. **Todo el video gira alrededor de ese ángulo**: la frase 1 lo plantea, el re-gancho lo
   complica, el clímax lo revela, la pregunta final lo pone a votación.
3. **Un responsable con nombre** si el texto lo da (empresa, gobierno, capitán, ingeniero jefe):
   la indignación ("lo sabían y no hicieron nada") es lo que más se comparte. Solo hechos del texto.
4. **Pregunta final de dos opciones**, que se responde con una palabra: "¿Accidente... o plan?",
   "¿Negligencia o mala suerte?", "¿Tú habrías huido o te quedabas?". Más fácil de contestar =
   más comentarios = más alcance.

[MOTORES DE INTERÉS — obligatorios, el video aburrido es el enemigo]
Usa **todos** los que el texto permita (sin inventar nada):
1. **Una persona**: si el texto nombra a alguien (sobreviviente, capitán, ingeniero que avisó,
   víctima, rescatista), ponle nombre y haz que el espectador sienta lo que vivió. La gente se
   queda por personas, no por edificios.
2. **Dos bucles abiertos**: la frase 2 abre una pregunta que solo se responde en el clímax; a
   mitad del video abre una segunda ("Pero lo más extraño aún no había pasado.").
3. **El reloj**: horas, minutos y cuentas regresivas exactas del texto ("A las 23:40...",
   "Les quedan 160 minutos.").
4. **El dato que casi nadie conoce**: el hecho más sorprendente y poco conocido del texto,
   guardado como premio después de la mitad.
5. **Contraste brutal / ironía**: lo que creían seguro contra lo que pasó.
6. **Ponte ahí**: una sola vez, mete al espectador en la escena ("Imagina oír ese crujido bajo
   tus pies.").
7. **Giro final**: la última frase cambia el sentido de la primera y obliga a volver a verla.
Regla de oro: **cada frase tiene que hacer que quieran oír la siguiente.** Si una frase solo da
contexto, reescríbela como tensión o bórrala.

[TONO]
- **Español latinoamericano neutro** (de México a Argentina): "ustedes", nunca "vosotros";
  "carro"/"auto", "computadora", "celular"; nada de "vale", "tío", "guay", "che", "güey".
- **Voz intensa, urgente y cinematográfica**, como el narrador de un tráiler de catástrofe.
  Nunca suena a clase ni a enciclopedia.
- Habla al espectador ("Imagina estar ahí"), usa cuentas regresivas ("Les quedan 40 segundos"),
  contrastes brutales ("Lo creían indestructible. Duró 3 horas.").
- Cada frase: **entre 6 y 10 palabras**, una idea, máximo una coma, sin puntos suspensivos.
  Máximo 2 exclamaciones en todo el video.
- Prohibido el relleno y las muletillas de IA ("en un giro inesperado", "cabe destacar",
  "sin embargo", "a lo largo de la historia"). Si una frase no da tensión, curiosidad o un
  dato que golpea, bórrala.
- En `emphasis` va la palabra o número clave de la frase (se pinta en amarillo en el subtítulo).
- En `overlay` va un número o palabra corta que aparece **literalmente** en esa frase y se muestra
  enorme en el centro ("1985", "98 m", "23.000"). Solo en 2 o 3 frases por parte; el resto vacío.

[PANTALLA — fotografía cinematográfica hiperrealista a escala épica]
Cada plano es una imagen hiperrealista, como una toma aérea de cine del desastre real: escala
abrumadora, cielo tormentoso, niebla, luz dramática. Nada de maquetas, diagramas ni texto.

- "VEO": video real en movimiento. **Mínimo 4 frases por parte (mínimo 8 en total).** La frase 1
  y la frase de clímax de cada parte siempre son VEO.
- "AI_IMAGE": el resto (se le agrega movimiento de cámara en el render).
- "FOTO": una **foto real** de la lista [FOTOS REALES] de abajo, con `"foto": número`.

[FOTOS REALES — Wikimedia Commons, libres]
{photos}
Las fotos reales hacen sentir "esto pasó de verdad" (los canales grandes de misterio mezclan
fotos de época e inventadas). Usa **2 a 4 frases "FOTO"** cuando la lista no esté vacía, y **solo**
en la frase que habla exactamente de lo que muestra la foto: la persona que nombras, el barco o el
lugar antes del desastre, un mapa, un documento. Nunca en la frase 1. Escribe igual su `prompt`
(respaldo por si la foto falla). Si la lista dice "ninguna", no uses "FOTO".

**La frase 1 es la portada**: el plano más espectacular e "imposible" del video, una vista amplia
donde la escala del desastre se entienda de un golpe, con un solo sujeto claro en el centro.

Alterna tres tipos de plano, sin repetir el mismo tres veces seguidas: aéreo épico ("epic aerial
drone shot of ..."), contrapicado monumental ("low-angle shot looking up at the colossal ..."),
detalle real ("close-up photo of ...").

`prompt` va **en inglés** y solo cuando `scene_type` es VEO o AI_IMAGE. Empieza describiendo con
detalle el sujeto real de esa frase (ej. "epic aerial drone shot of the Titanic breaking in half
in the dark North Atlantic, lifeboats scattered on the black water") y termina siempre, tal cual:
  ", hyper-realistic cinematic photograph, epic sense of scale, dramatic overcast stormy sky,
   moody natural color grading, volumetric mist, ultra-detailed, shot on ARRI Alexa, 8k,
   photorealistic, no text, no watermark"
En la frase de clímax de cada parte añade antes del cierre: "dramatic catastrophic collapse,
structure disintegrating into dust and debris, intense destruction".
En las frases VEO, la escena **ya está en plena acción desde el primer fotograma** y lo que se
mueve en grande es la escena misma (la estructura que se parte, el agua que arrasa, el fuego que
se extiende); añade un movimiento de cámara rápido ("rapid FPV drone dive into ...", "fast whip
pan across ...", "sudden crash zoom into ..."). Muestra solo lo que dice el texto.

`sfx` suena al empezar la frase. Úsalo en 2 o 3 frases por parte (giro, número, clímax), nunca
dos seguidas iguales; el resto "none".
- "sub_boom": revelación o número que golpea. "crack": algo se rompe. "whoosh": agua, gas,
  escombros o transición. "alarm": el momento más tenso, una sola vez. "braam": el clímax.

[FORMATO DE SALIDA] Responde solo con este JSON (textos en español; nombres de campo y valores de
`scene_type`/`sfx` tal cual).
{{
  "angulo": "Todos creen que ___, pero casi nadie sabe que ___ (sale del texto).",
  "title_seed": "Una frase en español que resuma la historia (sin nombre de canal ni marcas).",
  "part1": {{"hook_number": "Pregunta de máximo 4 palabras para el centro de la pantalla.",
             "hook_stat": "CIFRA BRUTAL en máximo 3 palabras, o cadena vacía",
             "lines": [
    {{"text": "Frase de narración en español (6-10 palabras)",
     "scene_type": "VEO | AI_IMAGE | FOTO",
     "foto": 0,
     "prompt": "prompt en inglés que termina con la frase de estilo de arriba",
     "sfx": "sub_boom | crack | whoosh | alarm | braam | none",
     "overlay": "número/palabra para el centro de la pantalla, o cadena vacía",
     "emphasis": ["palabra clave"],
     "solo_tiktok": false}}
  ]}},
  "part2": {{"hook_number": "Otra pregunta corta (solo se usa la de part1).",
              "lines": [ ...misma estructura... ]}}
}}
"""


RETENTION_EDITOR_PROMPT = """Eres el editor de retención de un canal de Shorts de desastres en español
latinoamericano. El 90% de los videos de este nicho mueren porque son aburridos. Tu trabajo: que
este guion no se pueda dejar de ver.

[MATERIAL — única fuente de hechos; no agregues nada que no esté aquí]
Título: {headline}
Pistas: {hints}
{context}

[GUION ACTUAL (JSON)]
{script_json}

Paso 1 — Califica del 1 al 10, con dureza: ángulo (¿es algo que casi nadie sabe de un suceso que
todos reconocen, y todo el video gira alrededor de él?), gancho de la frase 1 (¿detiene el pulgar
en medio segundo?), bucles abiertos, persona/emoción, dato sorprendente o misterio/teoría (siempre como
teoría), ritmo (ninguna frase de relleno), pregunta final que provoque comentarios y enlace con la
frase 1 (mejor si es de dos opciones: "¿Accidente... o plan?").
Paso 2 — Escribe 5 frases 1 alternativas (al menos 2 del tipo "¿Y si...?" si el material tiene
una teoría o algo sin explicar; las demás, el momento más brutal en presente con un número) y elige
la más fuerte (o la actual si es mejor).
Paso 3 — Reescribe **solo lo que esté por debajo de 9**: frases planas, de contexto, repetidas o
con muletillas. Mantén exactamente la estructura JSON, la cantidad aproximada de caracteres, los
campos (scene_type, prompt, sfx, overlay, emphasis, solo_tiktok) y todas las reglas del guion
original (35 a 45 segundos en la versión YouTube, frases de 6 a 10 palabras, español latino
neutro, nunca "hoy", nada de "parte 1/2" ni "suscríbete"). Si cambias el sentido de una frase,
ajusta su `prompt` en inglés para que la imagen muestre lo nuevo, con la misma frase de estilo final.
Cada `overlay` debe aparecer literalmente en su frase.

Responde solo con JSON:
{{"scores": {{"angulo": 0, "gancho": 0, "bucles": 0, "persona": 0, "dato": 0, "ritmo": 0, "giro": 0}},
  "aperturas": ["", "", "", "", ""],
  "script": {{ ...el guion completo con el mismo formato que el GUION ACTUAL... }}}}
"""


def _retention_pass(
    script: Script, candidate: StoryCandidate, provider: LLMProvider, raw: dict
) -> Script:
    """초안을 '시청 지속률 편집자' 관점에서 한 번 더 고친다(2026-10-27, 평균 조회수 300 개편).

    한 번에 쓴 대본은 문장 몇 개가 늘 맥이 빠진다. 점수를 매기고 9점 미만만 고치게 한다.
    고친 대본이 구조 검사(_parse)를 통과하지 못하면 초안을 그대로 쓴다 — 편집이 대본을
    망가뜨리는 일은 없어야 한다.
    """
    import json

    try:
        edited = provider.complete_json(
            RETENTION_EDITOR_PROMPT.format(
                headline=candidate.headline,
                hints="; ".join(candidate.topic_tags) or "ninguna",
                context=candidate.context,
                script_json=json.dumps(raw, ensure_ascii=False),
            ),
            0.7,
        )
    except Exception as exc:
        logger.warning(f"[script] 지속률 편집 호출 실패 — 초안을 씁니다: {exc}")
        return script
    if not isinstance(edited, dict):
        return script
    logger.info(f"[script] 지속률 편집 점수: {edited.get('scores')}")
    improved = _parse(edited.get("script") if isinstance(edited.get("script"), dict) else None, candidate)
    if improved is None:
        logger.warning("[script] 편집본이 구조 검사를 통과하지 못해 초안을 씁니다")
        return script
    return improved


def generate(
    candidate: StoryCandidate,
    provider: LLMProvider | None,
    part1_chars: int,
    part2_chars: int,
    temperature: float,
) -> Script:
    if provider is not None:
        prompt = _prompt(candidate, part1_chars, part2_chars)
        raw = provider.complete_json(prompt, temperature)
        script = _parse(raw, candidate)
        if script is not None:
            if candidate.dataset == "disaster":
                logger.info(f"[script] 각도: {(raw or {}).get('angulo')}")
                return _retention_pass(script, candidate, provider, raw)
            return script
        # 대본이 구조 요건(최소 문장 수 등)에 못 미쳐 거절됐을 수 있다 — 그렇다고
        # 바로 템플릿 폴백으로 넘기면 너무 비싸다: 폴백(특히 disaster)은 번역 없이
        # 위키백과 한국어 원문을 그대로 읽고, 길이 상한도 느슨해 몇 분짜리 영상이
        # 될 수 있다(실제로 7문장짜리 대본이 거절되어 3분 37초짜리 한국어 혼입
        # 영상이 나온 사고가 있었다, 2026-10-02). LLM 이 방금 응답했다는 건
        # 자격증명·네트워크가 살아있다는 뜻이니, 폴백보다 재시도 한 번이 싸다.
        logger.warning("[script] 1차 응답이 구조 요건에 못 미쳐 한 번 더 시도합니다")
        raw = provider.complete_json(prompt, temperature)
        script = _parse(raw, candidate)
        if script is not None:
            if candidate.dataset == "disaster":
                return _retention_pass(script, candidate, provider, raw)
            return script

    if candidate.dataset == "sillok":
        return _sillok_template_script(candidate)
    if candidate.dataset == "disaster":
        return _disaster_template_script(candidate)
    return _template_script(candidate)


def _prompt(candidate: StoryCandidate, part1_chars: int, part2_chars: int) -> str:
    """소재에 맞는 프롬프트를 고른다.

    문화재는 "이름/지정 등급/해설문", 실록은 "왕대·날짜/국역 전문", 재난은
    "제목/위키백과 본문"이라 재료의 모양이 아예 다르다. `dataset` 하나로 갈린다.
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

    if candidate.dataset == "disaster":
        return GLOBAL_DISASTER_PROMPT.format(
            headline=candidate.headline,
            # 주제 엔진이 붙인 힌트(오늘이 기념일인지 등). 없으면 "ninguna".
            hints="; ".join(candidate.topic_tags) or "ninguna",
            context=candidate.context,
            part1_chars=part1_chars,
            part2_chars=part2_chars,
            extra_lines=TIKTOK_EXTRA_LINES_PER_PART,
            extra_chars=TIKTOK_EXTRA_CHARS_PER_PART,
            photos=_photo_menu(candidate),
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



# GLOBAL_DISASTER_PROMPT가 요구하는 "부마다 10~15개의 짧은 문장"의 안전 하한. LLM이
# 지시를 무시하고 2~5문장짜리 얇은 대본을 내면(3중 후킹 구조가 통째로 들어갈 자리가
# 없다), 파싱은 성공해도 여기서 걸러 템플릿 폴백으로 넘긴다 — "검증 없이 통과되던
# 얇은 대본"이 실제 원인이었다.
_DISASTER_MIN_LINES_PER_PART = 5  # 2026-10-27: 35~45초 포맷으로 부당 6~7문장(틱톡 전용 문장 제외)
# 틱톡 확장판(2026-10-27 사용자 지시: 틱톡은 60초 초과, 유튜브는 35~45초). 1.0배속 약 12.7자/초
# 기준으로 유튜브판 550자 + 확장 2×170자 ≈ 890자 ≈ 68초 — 모자라도 61초 위로 남게 여유를 둔다.
TIKTOK_EXTRA_LINES_PER_PART = "3 o 4"
TIKTOK_EXTRA_CHARS_PER_PART = 170
# VEO 컷은 4초짜리 영상이 통째로 생성된다. 문장이 너무 짧으면 대사가 1~2초 만에 끝나
# 나머지는 무음으로 채워진다. 12자 미만이면 4초를 채우기엔 짧다고 보고 템플릿 폴백으로
# 넘긴다(프롬프트는 12~22음절을 요구한다).
_VEO_MIN_CHARS = 12


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

    if candidate.dataset == "disaster":
        _sanitize_tiktok_only(part1, part2)

    core1 = [line for line in part1.lines if not line.tiktok_only]
    core2 = [line for line in part2.lines if not line.tiktok_only]
    if candidate.dataset == "disaster" and (
        len(core1) < _DISASTER_MIN_LINES_PER_PART
        or len(core2) < _DISASTER_MIN_LINES_PER_PART
    ):
        logger.warning(
            f"[script] 3중 후킹 구조를 담기엔 대본이 너무 짧습니다 "
            f"(1부 {len(part1.lines)}문장·2부 {len(part2.lines)}문장, "
            f"최소 {_DISASTER_MIN_LINES_PER_PART}문장 필요) — 템플릿 폴백으로 넘깁니다"
        )
        return None

    if candidate.dataset == "disaster":
        short_veo = [
            line.text
            for part in (part1, part2)
            for line in part.lines
            if line.scene_type == "VEO" and len(line.text) < _VEO_MIN_CHARS
        ]
        if short_veo:
            logger.warning(
                f"[script] VEO 문장이 너무 짧아 클립 길이를 채우지 못합니다 "
                f"(최소 {_VEO_MIN_CHARS}자 필요): {short_veo} — 템플릿 폴백으로 넘깁니다"
            )
            return None

    source_note = (
        f"Fuente: {candidate.source_name}"
        if candidate.dataset == "disaster"
        else f"출처: {candidate.source_name}"
    )
    return Script(
        topic_key=candidate.topic_key,
        title_seed=str(raw.get("title_seed") or candidate.headline),
        part1=part1,
        part2=part2,
        source_note=source_note,
        is_sample=candidate.is_sample,
    )


def _photo_menu(candidate: StoryCandidate) -> str:
    photos = getattr(candidate, "archive_photos", None) or []
    if not photos:
        return "ninguna"
    return "\n".join(f"{index}. {photo.description}" for index, photo in enumerate(photos, start=1))


def _sanitize_tiktok_only(part1: ScriptPart, part2: ScriptPart) -> None:
    """틱톡 전용 문장 규칙을 코드로도 지킨다 — 첫 두 문장·각 부의 마지막 문장(클리프행어,
    루프)은 유튜브판에 꼭 있어야 하고, 전용 문장은 Veo 클립을 더 쓰지 않게 정지컷으로."""
    for index in (0, 1):
        if index < len(part1.lines):
            part1.lines[index].tiktok_only = False
    # 첫 문장은 표지(가장 압도적인 생성 영상)다 — 실제 사진이 오면 Veo 로 되돌린다.
    if part1.lines and part1.lines[0].scene_type == "FOTO":
        part1.lines[0].scene_type = "VEO"
        part1.lines[0].photo_url = part1.lines[0].photo_credit = ""
    for part in (part1, part2):
        if part.lines:
            part.lines[-1].tiktok_only = False
        for line in part.lines:
            if line.tiktok_only and line.scene_type == "VEO":
                line.scene_type = "AI_IMAGE"
                line.sfx = "none"


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
        hook_stat=_hook_stat(raw.get("hook_stat"), candidate, lines) if part == 1 else "",
    )


_HOOK_STAT_MAX_CHARS = 22


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _hook_stat(raw: object, candidate: StoryCandidate, lines: list[ScriptLine]) -> str:
    """첫 화면의 큰 숫자. 숫자가 없거나 길거나, 원문·대본 어디에도 없는 숫자면 버린다 —
    첫 화면에 지어낸 숫자가 박히면 사실 기반 원칙이 통째로 깨진다."""
    text = " ".join(str(raw or "").split()).upper()
    digits = _digits(text)
    if not digits or len(text) > _HOOK_STAT_MAX_CHARS or len(text.split()) > 3:
        return ""
    sources = [candidate.context, *candidate.facts, *(line.text for line in lines)]
    if not any(digits in _digits(source) for source in sources if source):
        logger.warning(f"[script] 첫 화면 숫자 '{text}' 가 원문에 없어 뺍니다")
        return ""
    return text


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

    sfx = _sfx(raw.get("sfx"))
    # VEO 컷은 편당 4개뿐인 가장 센 장면인데, LLM 이 sfx 를 깜빡 "none"으로 두는 일이
    # 있다 — 가장 임팩트가 커야 할 컷이 조용히 넘어가면 효과음 시스템이 죽어 보인다.
    # 가장 범용적인 타격음으로 강제해 항상 무언가는 터지게 한다.
    if scene_type == "VEO" and sfx == "none":
        sfx = "sub_boom"

    # 실제 사진은 후보에 실린 목록(번호)에서만 — 목록에 없는 번호면 생성 그림으로.
    photo_url = photo_credit = ""
    if scene_type == "FOTO":
        photos = getattr(candidate, "archive_photos", None) or []
        try:
            index = int(raw.get("foto") or 0)
        except (TypeError, ValueError):
            index = 0
        if 1 <= index <= len(photos):
            photo_url = photos[index - 1].url
            photo_credit = photos[index - 1].credit()
        else:
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
        sfx=sfx,
        overlay=_overlay(raw.get("overlay"), text),
        tiktok_only=str(raw.get("solo_tiktok")).strip().lower() == "true",
        photo_url=photo_url,
        photo_credit=photo_credit,
    )


_OVERLAY_MAX_CHARS = 8


def _overlay(raw: object, text: str) -> str:
    """화면 중앙에 크게 박을 숫자/단어. 문장에 실제로 없는 말이면 버린다 — 자막과 다른
    숫자가 화면에 뜨면 사실 기반 원칙이 깨진다."""
    value = str(raw or "").strip()
    if not value or len(value) > _OVERLAY_MAX_CHARS or value not in text:
        return ""
    return value


# LLM 이 프롬프트에 없는 이름을 쓰더라도, 실제 에셋(assets/audio/*.wav) 중 가장
# 가까운 걸로 매핑한다 — 예전엔 허용 목록에 없는 이름이 오면 그냥 "none"으로
# 떨어뜨려 효과음이 조용히 사라졌다("SFX 가 죽어 있다"는 지적의 실제 원인 중 하나).
# sub_boom(초저역 타격)이 가장 범용적인 폴백이라 인식 못한 태그는 전부 여기로
# 보낸다 — 완전한 무음보다 낫다. crack/whoosh/alarm 은 "효과음이 단조롭다"는
# 지적에 맞춰 추가한 갈래다(download_assets.sh 참고).
_SFX_ALIASES = {
    "sub_boom": "sub_boom",
    "boom": "sub_boom",
    "impact": "sub_boom",
    "explosion": "sub_boom",
    "collapse": "sub_boom",
    "hit": "sub_boom",
    "braam": "braam",
    "rumble": "braam",
    "drone_hit": "braam",
    "paper_rustle": "paper_rustle",
    "rustle": "paper_rustle",
    "crack": "crack",
    "crack_snap": "crack",
    "fracture": "crack",
    "structural_crack": "crack",
    "whoosh": "whoosh",
    "water_rushing": "whoosh",
    "rush": "whoosh",
    "sweep": "whoosh",
    "alarm": "alarm",
    "warning": "alarm",
    "siren": "alarm",
}


def _sfx(raw: object) -> str:
    name = str(raw or "none").strip().lower()
    if name in ("", "none", "null"):
        return "none"
    return _SFX_ALIASES.get(name, "sub_boom")


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
    "cinematic historical disaster drama, photorealistic 8k detail, dramatic "
    "stormy sky with heavy clouds and lightning, warm torchlight against cold "
    "famine-stricken landscape, rich saturated color grading, epic movie "
    "still, painterly realism, no text, no watermark"
)
FALLBACK_IMAGE_PROMPT = (
    f"starving villagers gathered on a barren, cracked Joseon-era farmland, "
    f"{CINEMATIC_STYLE}"
)

# 재난 소재(3D 엔지니어링 단면도) 전용 고정 스타일. LLM 이 없을 때만 쓰인다.
ENGINEERING_DIAGRAM_STYLE = (
    "hyper-realistic cinematic photograph, epic sense of scale, dramatic overcast stormy "
    "sky, moody natural color grading, volumetric mist, ultra-detailed, 8k, photorealistic, "
    "no text, no watermark"
)
FALLBACK_DISASTER_IMAGE_PROMPT = (
    f"a large structure with visible structural damage and cracks, "
    f"{ENGINEERING_DIAGRAM_STYLE}"
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

    part2_lines = [line(0, "1부에서 남긴 질문부터 보겠습니다.")]
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


# candidate.facts 는 위키백과 문단을 "\n\n" 로 쪼갠 것이라 문단 하나가 수백 자를 넘기도
# 한다. 이걸 그대로 한 줄(=TTS 한 문장)로 읽히면 컷 하나가 몇 분씩 늘어난다 — 실제로
# 이 사고로 6분 넘는 영상이 나온 적이 있다(run #32). 문장 하나가 화면 한 컷과 맞물려야
# 하는 이 파이프라인 구조상, 폴백 문장도 최대 길이를 지켜야 한다.
# 사고 이력(2026-10-02): 이 폴백이 LLM 완전 실패뿐 아니라 "LLM 응답은 받았지만
# 구조 검증에서 거절됐을 때"도 탄다(generate() 가 재시도 한 번 뒤에도 실패하면
# 여기로 떨어진다) — 번역이 안 되는 한국어 원문을 그대로 읽는 경로이므로 상한을
# 120자에서 더 낮춰 최악의 경우에도 영상이 과도하게 길어지지 않게 한다.
_FALLBACK_FACT_MAX_CHARS = 70


def _cap_fact_for_speech(fact: str, max_chars: int = _FALLBACK_FACT_MAX_CHARS) -> str:
    """긴 위키백과 문단을 한 문장 분량으로 자른다. 문장 경계(마침표)에서 자르되,
    그 경계가 없으면 글자 수에서 그냥 끊는다 — 사실을 지어내지 않되, 한 줄이
    통째로 몇 분짜리 낭독이 되는 것만 막는다."""
    if len(fact) <= max_chars:
        return fact
    head = fact[:max_chars]
    boundary = head.rfind(". ")
    if boundary == -1:
        boundary = head.rfind("다.")
    if boundary > max_chars * 0.4:
        return head[: boundary + 1].strip()
    return head.strip()


def _disaster_template_script(candidate: StoryCandidate) -> Script:
    """LLM 없이 위키백과 본문만으로 짜는 대본.

    본문 문단을 그대로 나눠 쓴다 — 사실 재료를 임의로 바꾸지 않는다는 원칙은
    실록 폴백과 같다. 다만 문단이 너무 길면 한 줄(한 컷)이 지나치게 길어지므로
    `_cap_fact_for_speech` 로 상한을 둔다.
    """
    queries_ko = candidate.image_queries_ko or [candidate.subject or "재난"]
    queries_en = candidate.image_queries_en or ["disaster"]
    facts = [_cap_fact_for_speech(fact) for fact in candidate.facts if fact]

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
            prompt=FALLBACK_DISASTER_IMAGE_PROMPT if scene_type in ("VEO", "AI_IMAGE") else "",
            sfx=sfx,
        )

    # 이 폴백(LLM 이 끝내 구조 요건을 못 맞췄을 때 탄다, generate() 참고)의 문구
    # 자체는 스페인어로 옮겼지만, facts(candidate.facts)는 한국어 위키백과 원문
    # 그대로다 — 번역 엔진 없이 오프라인으로 도는 경로라 번역할 방법이 없다.
    # 정상 경로(LLM 동작 중)는 GLOBAL_DISASTER_PROMPT 가 전체를 스페인어로 새로
    # 쓰므로 이 문제가 없다. 사실 줄 수도 4개로 줄였다(기존 6개는 길이 상한을
    # 낮춰도 217초짜리 영상이 나온 사고의 원인 중 하나였다, 2026-10-02).
    part1_lines = [
        line(
            0,
            f"{candidate.headline}, esto ocurrió de verdad.",
            emphasis=[candidate.headline],
            scene_type="VEO",
        )
    ]
    part1_lines += [line(i, fact) for i, fact in enumerate(facts[:4], start=1)]
    part1_lines.append(line(5, "¿Cuál fue realmente la causa?", emphasis=["causa"], sfx="braam"))

    part2_lines = [line(0, "Retomamos la pregunta que quedó pendiente.")]
    part2_lines += [line(i, fact) for i, fact in enumerate(facts[4:8], start=1)]
    part2_lines.append(
        line(5, "Hasta aquí llega lo que quedó registrado.", emphasis=["registrado"], sfx="sub_boom")
    )
    part2_lines.append(line(8, "¿Por qué creen ustedes que ocurrió este desastre?"))

    return Script(
        topic_key=candidate.topic_key,
        title_seed=candidate.headline,
        part1=ScriptPart(part=1, hook_number=candidate.headline, lines=part1_lines),
        part2=ScriptPart(part=2, hook_number=candidate.headline, lines=part2_lines),
        source_note=f"Fuente: {candidate.source_name}",
        is_sample=candidate.is_sample,
    )
