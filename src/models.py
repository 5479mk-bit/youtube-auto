"""파이프라인 단계 사이를 오가는 데이터 구조."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

CameraMove = Literal["push_in", "pull_out", "pan_lr", "pan_rl", "pan_tb", "freeze"]
# 컷을 움직이는 방식. veo 는 생성 영상, parallax 는 깊이 기반 2.5D 카메라 이동,
# official_video 는 국가유산청이 항목별로 제공하는 공식 영상(비용 없이 실사 영상을 쓴다).
MotionEngine = Literal["veo", "parallax", "official_video", "ai_image", "historical_doc", "map_motion"]

# 대본이 문장마다 지정하는 화면 종류. 조선 사건에는 사진이 존재하지 않아서, 실사 검색
# 대신 이 네 갈래로 화면을 만든다.
#   VEO            생성 영상 (영상 전체에서 가장 센 훅 한 컷)
#   AI_IMAGE       생성 정지화 + 시네마틱 무빙 (사건·상황 묘사)
#   HISTORICAL_DOC 한지 위에 실록 원문을 얹고 붉은 먹줄을 긋는다 (사료 인용)
#   MAP_MOTION     대동여지도를 파고들며 고을에 핏빛 잉크가 번진다 (위치 언급)
SceneType = Literal["VEO", "AI_IMAGE", "HISTORICAL_DOC", "MAP_MOTION"]
SCENE_TYPES: tuple[str, ...] = ("VEO", "AI_IMAGE", "HISTORICAL_DOC", "MAP_MOTION")
# 씬 종류를 실제 렌더 엔진으로 옮기는 표.
SCENE_TYPE_ENGINE: dict[str, str] = {
    "VEO": "veo",
    "AI_IMAGE": "ai_image",
    "HISTORICAL_DOC": "historical_doc",
    "MAP_MOTION": "map_motion",
}
# 충격적 사실·전환에 때리는 효과음.
SfxName = Literal["sub_boom", "braam", "paper_rustle", "none"]
ENGINE_NAMES: tuple[str, ...] = (
    "veo",
    "parallax",
    "official_video",
    "ai_image",
    "historical_doc",
    "map_motion",
)


@dataclass
class DataPoint:
    label: str
    value: float


@dataclass
class StoryCandidate:
    """데이터셋 어댑터가 찾아낸 '이야깃거리' 후보."""

    dataset: str
    topic_key: str  # 중복 방지용 고유 키
    headline: str  # "서울 카페 폐업률 1위"
    headline_number: str  # "3.7년"
    subject: str  # 내레이션이 다룰 대상 ("서울시 카페 업종")
    context: str  # LLM 에 넘길 배경 설명
    # 통계 콘텐츠 시절의 유물. 야사 후보는 차트가 없으니 비어 있는 게 정상이고,
    # topic_scorer 도 더는 series 를 요구하지 않는다.
    series: list[DataPoint] = field(default_factory=list)
    series_unit: str = ""
    source_name: str = ""
    source_url: str = ""
    # 사진 검색어. 공공 API 는 한국어로, 위키미디어는 영어로 찾아야 한다.
    image_queries_ko: list[str] = field(default_factory=list)
    image_queries_en: list[str] = field(default_factory=list)
    # 해설문에서 뽑은, 출처가 분명한 문장들. LLM 대본은 이 안에서만 사실을 가져와야 한다.
    facts: list[str] = field(default_factory=list)
    # 위키백과에서 보강한 문단(전설·일화 등). facts 와 구분해 대본에서 "전해지는 말로는"
    # 식으로 표시하게 만든다.
    wiki_excerpt: str = ""
    # 소스가 붙여둔 갈래 표시. 실록은 기사마다 【분류】(예: "과학-천기(天氣)")가 달려 있어
    # 대본 프롬프트에 사건의 성격을 알려주는 단서가 된다.
    topic_tags: list[str] = field(default_factory=list)
    # 국가유산청 이미지/동영상/나레이션 API 는 이름이 아니라 이 세 값의 조합으로 조회한다.
    # 문화재가 아닌 소스(픽스처 등)는 비워둔다.
    heritage_ccba_kdcd: str = ""
    heritage_ccba_asno: str = ""
    heritage_ccba_ctcd: str = ""
    features: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    # 픽스처(샘플) 데이터로 만들어진 후보. 실제 해설문이 아니므로 업로드가 차단된다.
    is_sample: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScriptLine:
    """내레이션 한 문장 + 그 문장이 화면에서 어떻게 보일지."""

    text: str  # 화면 자막에 띄우는 글
    visual_query_ko: str  # 공공 API 용 한국어 검색어
    visual_query_en: str = ""  # 위키미디어 폴백용 영어 검색어
    # 입으로 읽는 글. 비어 있으면 text 를 그대로 읽는다.
    spoken_text: str = ""
    show_chart: bool = False
    emphasis: list[str] = field(default_factory=list)  # 자막에서 강조할 조각
    audio_path: str = ""
    duration: float = 0.0
    camera: CameraMove = "push_in"
    # 이 문장을 어떤 화면으로 만들지. 대본 LLM 이 문장마다 직접 정한다.
    scene_type: str = "AI_IMAGE"
    # VEO/AI_IMAGE 생성에 쓸 영문 프롬프트.
    prompt: str = ""
    # HISTORICAL_DOC 용: 사료 이름(예: 光海君日記)과 밑줄 그을 원문 구절.
    doc_title: str = ""
    doc_highlight: str = ""
    # MAP_MOTION 용: 지역과 표시할 고을들.
    map_region: str = ""
    map_spots: list[str] = field(default_factory=list)
    # 이 문장 시작 지점에 때릴 효과음.
    sfx: str = "none"
    # 이 문장을 읽고 나서 쉬는 시간. 숨 쉴 틈이 없으면 기계가 읽는 것처럼 들린다.
    pause_after: float = 0.0
    speech_rate: str = "+0%"

    @property
    def narration(self) -> str:
        return self.spoken_text or self.text


@dataclass
class ScriptPart:
    part: int
    hook_number: str
    lines: list[ScriptLine]

    @property
    def duration(self) -> float:
        return sum(line.duration for line in self.lines)


@dataclass
class Script:
    topic_key: str
    title_seed: str
    part1: ScriptPart
    part2: ScriptPart
    source_note: str = ""
    is_sample: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Script:
        def part(raw: dict[str, Any]) -> ScriptPart:
            return ScriptPart(
                part=raw["part"],
                hook_number=raw["hook_number"],
                lines=[ScriptLine(**line) for line in raw["lines"]],
            )

        return cls(
            topic_key=data["topic_key"],
            title_seed=data["title_seed"],
            part1=part(data["part1"]),
            part2=part(data["part2"]),
            source_note=data.get("source_note", ""),
            is_sample=data.get("is_sample", False),
        )


@dataclass
class PhotoCredit:
    """사진 한 장의 출처. 공공누리 1유형은 출처 표기가 이용 조건이라 끝까지 들고 다닌다."""

    query: str
    source_name: str
    license_name: str = ""
    page_url: str = ""
    author: str = ""


@dataclass
class Scene:
    """화면에 얹히는 한 컷. 문장 하나가 길면 여러 컷으로 쪼개진다."""

    start: float
    duration: float
    image_query_ko: str
    image_query_en: str
    camera: CameraMove
    engine: MotionEngine = "parallax"
    show_chart: bool = False
    chart_step: int = 0  # 차트를 몇 항목까지 드러낼지
    is_hook: bool = False
    # engine == "official_video" 일 때만 채워진다. 씬 배정 시점에 이미 내려받은 파일 경로다.
    official_video_path: str = ""
    # 아래는 문장(ScriptLine)에서 그대로 물려받는다 — 어떤 화면을 만들지가 전부 여기 있다.
    scene_type: str = "AI_IMAGE"
    prompt: str = ""
    doc_title: str = ""
    doc_highlight: str = ""
    map_region: str = ""
    map_spots: list[str] = field(default_factory=list)
    sfx: str = "none"


@dataclass
class VideoMetadata:
    title: str
    title_alternatives: list[str]
    description: str
    tags: list[str]


@dataclass
class RenderedPart:
    part: int
    video_path: str
    thumbnail_path: str
    duration: float
    metadata: VideoMetadata


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(payload, "to_dict"):
        payload = payload.to_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
