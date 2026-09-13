"""파이프라인 단계 사이를 오가는 데이터 구조."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

CameraMove = Literal["push_in", "pull_out", "pan_lr", "pan_rl", "pan_tb", "freeze"]


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
    series: list[DataPoint] = field(default_factory=list)
    series_unit: str = ""
    source_name: str = ""
    source_url: str = ""
    image_queries: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    # 픽스처(샘플) 데이터로 만들어진 후보. 실제 통계가 아니므로 업로드가 차단된다.
    is_sample: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScriptLine:
    """내레이션 한 문장 + 그 문장이 화면에서 어떻게 보일지."""

    text: str
    visual_query: str  # 이미지 검색어
    show_chart: bool = False
    emphasis: list[str] = field(default_factory=list)  # 자막에서 강조할 조각
    audio_path: str = ""
    duration: float = 0.0
    camera: CameraMove = "push_in"


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
class Scene:
    """화면에 얹히는 한 컷. 문장 하나가 길면 여러 컷으로 쪼개진다."""

    start: float
    duration: float
    image_query: str
    camera: CameraMove
    show_chart: bool = False
    chart_step: int = 0  # 차트를 몇 항목까지 드러낼지
    is_hook: bool = False


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
