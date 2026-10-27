"""후킹 제목 후보와 설명·태그를 만든다.

제목은 세 개를 만든다. 텔레그램 검수에서 사람이 고를 수 있어야 하기 때문이다.
설명에는 반드시 출처를 적는다. 데이터 채널의 신뢰는 거기서 나온다.

제목·설명 어디에도 채널명/브랜드명("신비한 건축사전" 등)을 쓰지 않는다 — 영상 자체가
소재마다 독립적으로 소비되는 쇼츠라 브랜드 워딩은 군더더기다(사용자 지시, 2026-09-27).
"""

from __future__ import annotations

from ..models import Script, ScriptPart, StoryCandidate, VideoMetadata
from ..script.llm_client import LLMProvider
from ..text_ko import particle

# 재난(disaster) 콘텐츠에 고정으로 붙이는 해시태그. LLM 이 고르게 두지 않고
# 항상 이 목록만 쓴다(사용자 지시) — 설명 하단 해시태그 줄과 API tags 필드
# 양쪽에 그대로 들어간다(_parse/_template_metadata 가 둘 다 이 리스트를 그대로
# 쓴다 — 한 곳만 고치면 자동으로 둘 다 반영된다).
#
# 조회수 부진에 대한 특단의 조치(사용자 지시, 2026-09-30)로 채널 언어를 스페인어로
# 전환했고, 이후 "더 많이 해도 되잖아? 스페인어도 포함하고" 요청으로 5개 → 13개로
# 늘렸다(2026-09-30). 세 갈래로 구성:
# - 알고리즘 노출용(언어 무관, 검색량 최상위): #shorts #viral #fyp
# - 스페인어권 콘텐츠 카테고리 대표 태그(검색·추천 유입이 큰 상위 태그들):
#   #datoscuriosos #curiosidades #documental
# - 소재 직결 핵심 검색어(전부 스페인어): #historia #desastres #catastrofes
#   #misterio #ciencia #ingenieria #terror
# 2026-10-27: "#viral #fyp"(틱톡식, 유튜브에선 의미 없음)·"#terror"(공포물 시청층으로 잘못
# 분류될 위험)를 뺐다. 설명란에는 주제 맞춤 해시태그(#Pompeya #Italia 등)가 이 앞에 붙는다.
DISASTER_HASHTAGS = [
    "#shorts", "#historia", "#desastres", "#catastrofes", "#misterio",
    "#datoscuriosos", "#curiosidades", "#documental", "#ciencia", "#ingenieria",
]

# 텔레그램 복붙용 캡션 전용 — DISASTER_HASHTAGS(유튜브 메타데이터, 12개)와는
# 별개로, 틱톡/인스타그램에 손으로 붙여넣을 때는 이 5개만 쓴다(사용자 지시,
# 2026-10-11).
TELEGRAM_CAPTION_HASHTAGS = [
    "#historia", "#desastres", "#catastrofes", "#misterio", "#datoscuriosos",
]
# 2026-10-20: 캡션 해시태그 5개 = 주제 맞춤 3개(LLM) + 넓은 고정 2개. 주제 태그가 검색·추천에
# 걸리고, 고정 태그가 채널 카테고리를 묶는다. LLM 이 못 주면 위 5개로 돌아간다.
TELEGRAM_CAPTION_FIXED_HASHTAGS = ["#desastres", "#misterio"]


def caption_hashtags(metadata) -> list[str]:
    topic = [tag for tag in getattr(metadata, "hashtags", []) or [] if tag not in TELEGRAM_CAPTION_FIXED_HASHTAGS]
    if len(topic) < 3:
        return list(TELEGRAM_CAPTION_HASHTAGS)
    return topic[:3] + TELEGRAM_CAPTION_FIXED_HASHTAGS

_COMMON_RULES = """규칙:
- 제목은 후킹용 한 줄이다. 28자 이내, 숫자를 앞쪽에 배치하고, 낚시가 아니라 실제
  내용과 일치해야 한다. 과장 금지, 데이터에 없는 주장 금지.
- 채널명·브랜드명(예: '신비한 건축사전')을 제목·설명 어디에도 쓰지 않는다.
- description 필드는 2~3줄로 쓴다. 첫 줄은 궁금증을 남기는 후킹 문장("이 사고, 아직도
  원인이 다 안 밝혀졌다" 같은), 나머지 줄에는 미스터리·의문점·아직 안 풀린 부분처럼
  시청자의 흥미를 끄는 요소를 한두 개 더 구체적으로 덧붙인다(예: "전문가들도 그날
  왜 그렇게 됐는지 의견이 갈린다", "생존자 증언과 공식 기록이 서로 다르다"). 본문에
  없는 사실을 지어내지 않는다 — 있는 사실을 궁금하게 포장하는 것까지만 한다. 출처·
  해시태그는 이 함수가 뒤에 자동으로 붙이므로 description 에는 넣지 않는다."""

PROMPT = """다음 쇼츠 영상의 유튜브 제목 후보 3개와 설명을 쓴다.

주제: {title_seed}
화면 문구: {hook_number}
대상: {subject}
부: {part}부 (전체 2부작)
내레이션: {narration}

""" + _COMMON_RULES + """

JSON 만 출력한다:
{{"titles": ["", "", ""], "description": "", "tags": ["", ""]}}
"""

# 합본(--combine-parts) 렌더는 1·2부로 나뉘지 않은 영상 하나다 — "부"·"2부작" 언급을
# 프롬프트에서부터 빼서 LLM 이 틀린 부 표기를 만들지 않게 한다.
COMBINED_PROMPT = """다음 쇼츠 영상의 유튜브 제목 후보 3개와 설명을 쓴다.

주제: {title_seed}
화면 문구: {hook_number}
대상: {subject}
내레이션: {narration}

""" + _COMMON_RULES + """

JSON 만 출력한다:
{{"titles": ["", "", ""], "description": "", "tags": ["", ""]}}
"""

# disaster 데이터셋 전용 스페인어판. 위 PROMPT/COMBINED_PROMPT(한국어, sillok/heritage
# 용)는 지우지 않는다 — 다른 소재로 되돌릴 때를 위해 남겨둔다.
_COMMON_RULES_ES = """Reglas:
- **Título (2026-10-27)**: de 40 a 65 caracteres, y en los primeros ~35 caracteres tiene que
  aparecer **el nombre reconocible** del suceso, lugar u objeto (Titanic, Pompeya, Chernóbil,
  Armero, los Andes...). Quien lo ve pasar debe entender en un vistazo de qué trata **y** sentir
  que le falta algo. Los 3 candidatos usan fórmulas distintas — **el primero siempre es la
  fórmula de cifra**, la que mejor ha funcionado en este canal ("3,276 muertos: Rutina fatal",
  "8.6 TERREMOTO: ¿Por qué no arrasó?", "36 toneladas de veneno: Bhopal"):
  · "[CIFRA + qué]: [nombre] + [pregunta o giro]" — "69.000 muertos: el terremoto que nadie vio venir"
  · "[Nombre]: [hecho concreto que impacta]" — "Pompeya: 2.000 personas que nunca intentaron huir"
  · "Por qué / El día que [...]" — "El día que el Titanic se partió en dos"
  Para la audiencia mexicana, en sismos usa también "temblor" o "sismo" (así lo buscan).
  **Prohibido**: una cifra sin decir de qué suceso o qué mide ("61.2 m/s: El RÉCORD EFÍMERO",
  "494 KM: El RUGIDO"), más de una palabra en MAYÚSCULAS aparte de la cifra, hashtags o emojis.
  Todo lo que diga el título debe ser cierto según los datos — impactante sí, falso nunca.
  Si en Pistas hay un aniversario, **uno** de los 3 títulos lo usa con la cifra exacta de la
  pista ("Hace 41 años, Armero desapareció en una sola noche") — la gente busca estos sucesos
  en su aniversario. Nunca "hoy" ni "un día como hoy".
- Nunca uses el nombre de un canal ni ninguna marca en el título ni en la descripción.
- Todo en **español latinoamericano neutro** (público de México a Argentina): "ustedes", nunca
  "vosotros", ni expresiones propias de España ni de un solo país.
- El campo description tiene 2-3 líneas cortas y **su único objetivo es que el espectador
  vea el video completo** — nunca debe poder "entender la historia" leyendo solo la
  descripción:
  · **Prohibido el spoiler**: no digas el desenlace, la causa, el número de víctimas final ni
    la respuesta a la pregunta del video. La descripción abre la pregunta; solo el video la
    cierra.
  · Línea 1: un detalle concreto e inquietante del inicio de la historia + una pregunta
    directa ("Tenía 16 compartimentos estancos. ¿Por qué no sirvieron de nada?").
  · Línea 2: anuncia que la clave está al final del video, apuntando a algo concreto que el
    video sí muestra al final ("El detalle que lo cambia todo aparece en los últimos
    segundos 👀", "Fíjate en lo que pasa al final"). Solo si es verdad según la narración.
  · Opcional línea 3: una frase corta que invite a comentar ("¿Tú lo habrías visto venir?").
  No inventes hechos que no estén en el material. La fuente y los hashtags se agregan
  automáticamente después — no los incluyas en description.
- En el campo hashtags escribe exactamente 3 hashtags en español **específicos de este tema**
  (el nombre del suceso, el lugar o el tipo de desastre — ej. "#Titanic", "#naufragio",
  "#AtlanticoNorte"), sin espacios, cada uno empezando con #. Nada genérico. **Uno de los 3
  debe ser siempre el país donde ocurrió** (ej. "#Francia", "#Japón", "#EstadosUnidos").
- Además, en el campo description_ko escribe una traducción fiel al coreano del campo
  description (sin agregar ni quitar información, misma cantidad de líneas). Es solo para
  que el equipo (que no lee español) revise el contenido antes de publicar — nunca se sube
  a YouTube.
- En el campo titles_ko escribe una traducción al coreano de cada título de titles, en el
  mismo orden (3 elementos). También es solo para la revisión del equipo, nunca se sube a
  YouTube.
- En el campo pinned_comment escribe un comentario corto (1 línea, máximo 20 palabras) para
  fijar como comentario destacado — algo que invite a responder o debatir, relacionado con el
  hecho concreto del video (ej. "¿Tú habrías notado la falla antes de que fuera tarde?",
  "¿Negligencia o mala suerte? Dime qué piensas 👇"). Nada genérico tipo "like y suscríbete".
  No inventes hechos nuevos — solo invita a opinar sobre lo ya narrado."""

DISASTER_PROMPT_ES = """Escribe 3 títulos candidatos para YouTube y una descripción para
este Short.

Tema: {title_seed}
Texto en pantalla: {hook_number}
Sujeto: {subject}
Pistas: {hints}
Parte: {part} (de una serie de 2 partes)
Narración: {narration}

""" + _COMMON_RULES_ES + """

Responde solo con JSON. titles/description/pinned_comment en español, description_ko y
titles_ko en coreano:
{{"titles": ["", "", ""], "titles_ko": ["", "", ""], "description": "", "description_ko": "",
  "pinned_comment": "", "hashtags": ["#", "#", "#"], "tags": ["", ""]}}
"""

# 합본(--combine-parts) 렌더용 — "parte"/"serie de 2 partes" 언급을 아예 뺀다.
DISASTER_COMBINED_PROMPT_ES = """Escribe 3 títulos candidatos para YouTube y una descripción
para este Short.

Tema: {title_seed}
Texto en pantalla: {hook_number}
Sujeto: {subject}
Pistas: {hints}
Narración: {narration}

""" + _COMMON_RULES_ES + """

Responde solo con JSON. titles/description/pinned_comment en español, description_ko y
titles_ko en coreano:
{{"titles": ["", "", ""], "titles_ko": ["", "", ""], "description": "", "description_ko": "",
  "pinned_comment": "", "hashtags": ["#", "#", "#"], "tags": ["", ""]}}
"""


def generate(
    script: Script,
    part: ScriptPart,
    candidate: StoryCandidate,
    provider: LLMProvider | None,
    default_tags: list[str],
    is_combined: bool = False,
) -> VideoMetadata:
    """`is_combined` 는 1·2부를 나누지 않고 영상 하나로 합본 렌더했을 때(--combine-parts)
    True 다 — 제목에 "(1부)" 를 붙이거나 "전체 2부작"이라고 쓰면 틀린 설명이 된다."""
    narration = " ".join(line.text for line in part.lines)

    if candidate.dataset == "disaster":
        prompt_template = DISASTER_COMBINED_PROMPT_ES if is_combined else DISASTER_PROMPT_ES
    else:
        prompt_template = COMBINED_PROMPT if is_combined else PROMPT

    if provider is not None:
        raw = provider.complete_json(
            prompt_template.format(
                title_seed=script.title_seed,
                hook_number=part.hook_number,
                subject=candidate.subject,
                part=part.part,
                narration=narration,
                hints="; ".join(candidate.topic_tags) or "ninguna",
            ),
            temperature=0.8,
        )
        metadata = _parse(raw, script, part, candidate, default_tags, is_combined)
        if metadata is not None:
            return metadata

    return _template_metadata(script, part, candidate, default_tags, is_combined)


def _parse(
    raw: dict | None,
    script: Script,
    part: ScriptPart,
    candidate: StoryCandidate,
    default_tags: list[str],
    is_combined: bool = False,
) -> VideoMetadata | None:
    if not raw:
        return None
    titles = [str(title).strip() for title in raw.get("titles", []) if str(title).strip()]
    titles_ko = [str(title).strip() for title in raw.get("titles_ko", []) if str(title).strip()]
    description = str(raw.get("description", "")).strip()
    description_ko = str(raw.get("description_ko", "")).strip()
    pinned_comment = str(raw.get("pinned_comment", "")).strip()
    hashtags = [
        "#" + str(tag).strip().lstrip("#").replace(" ", "")
        for tag in raw.get("hashtags", []) or []
        if str(tag).strip().lstrip("#")
    ]
    if not titles or not description:
        return None

    if not is_combined:
        titles = [_with_part_suffix(title, part.part) for title in titles]

    if candidate.dataset == "disaster":
        # 사용자 지시: LLM 이 고르지 않는다 — 이 다섯 개로 고정한다.
        # 주제 맞춤 해시태그(나라·사건명)를 유튜브 태그 앞에도 붙인다(2026-10-20).
        tags = list(dict.fromkeys(hashtags + list(DISASTER_HASHTAGS)))
    else:
        tags = [str(tag).strip() for tag in raw.get("tags", []) if str(tag).strip()]
        tags = list(dict.fromkeys(tags + default_tags))[:15]

    return VideoMetadata(
        title=titles[0],
        title_alternatives=titles[1:],
        description=_with_source(description, script, candidate, hashtags),
        tags=tags,
        title_ko=titles_ko[0] if titles_ko else "",
        title_alternatives_ko=titles_ko[1:],
        hook_description=description,
        hashtags=hashtags,
        source_line=_source_line(script, candidate),
        description_ko=description_ko,
        pinned_comment=pinned_comment,
    )


def _template_metadata(
    script: Script,
    part: ScriptPart,
    candidate: StoryCandidate,
    default_tags: list[str],
    is_combined: bool = False,
) -> VideoMetadata:
    def suffixed(title: str) -> str:
        return title if is_combined else _with_part_suffix(title, part.part)

    if candidate.dataset == "disaster":
        titles = [
            suffixed(f"{part.hook_number}, {script.title_seed}"),
            suffixed(f"{script.title_seed} ({part.hook_number})"),
            suffixed(f"La historia detrás de {candidate.subject}"),
        ]
    else:
        titles = [
            suffixed(f"{part.hook_number}, {script.title_seed}"),
            suffixed(f"{script.title_seed} ({part.hook_number})"),
            suffixed(f"{candidate.subject}에 얽힌 이야기"),
        ]
    if candidate.dataset == "sillok":
        description = (
            f"{candidate.headline_number} 실록에 적힌 기록. "
            "조선왕조실록 국역(국사편찬위원회)을 그대로 따라 정리했습니다."
        )
    elif candidate.dataset == "disaster":
        description = f"Repasamos los hechos de {candidate.subject} según los registros de Wikipedia."
    else:
        description = (
            f"{particle(candidate.subject, '을', '를')} 국가유산청 해설과 위키백과로 들여다봤습니다."
        )

    tags = list(DISASTER_HASHTAGS) if candidate.dataset == "disaster" else list(
        dict.fromkeys(default_tags)
    )[:15]

    return VideoMetadata(
        title=titles[0],
        title_alternatives=titles[1:],
        description=_with_source(description, script, candidate),
        tags=tags,
        hook_description=description,
        source_line=_source_line(script, candidate),
    )


def _with_part_suffix(title: str, part: int) -> str:
    suffix = f" ({part}부)"
    trimmed = title[: 100 - len(suffix)]
    return f"{trimmed}{suffix}"


def _source_line(script: Script, candidate: StoryCandidate) -> str:
    if script.source_note:
        return script.source_note
    if candidate.dataset == "disaster":
        return f"Fuente: {candidate.source_name}"
    return f"출처: {candidate.source_name}"


def _with_source(
    description: str, script: Script, candidate: StoryCandidate, hashtags: list[str] | None = None
) -> str:
    lines = [description, "", _source_line(script, candidate)]
    if candidate.source_url:
        lines.append(candidate.source_url)
    if candidate.dataset == "disaster":
        # 화면에 보이는 해시태그 줄(유튜브가 설명 상단 최대 3개를 클릭 가능한
        # 태그로 띄워준다) — DISASTER_HASHTAGS 와 순서·내용을 그대로 맞춘다.
        lines.append("")
        # 주제 맞춤 해시태그를 앞에 — 유튜브는 설명의 첫 해시태그 3개를 제목 위에 띄운다.
        lines.append(" ".join(dict.fromkeys([*(hashtags or [])[:3], *DISASTER_HASHTAGS])))
    if script.is_sample:
        lines.insert(0, "[샘플 데이터로 만든 테스트 영상입니다. 실제 해설문이 아닙니다.]")
    return "\n".join(lines)
