"""파이프라인 오케스트레이션.

주제 선정 → 대본 → 내레이션 → 자막 → 컷 배치 → 렌더 → 검수 → 업로드.
각 단계의 산출물은 output/<task_id>/ 아래에 남겨서, 중간에 멈춰도 무엇이 어디까지
됐는지 눈으로 확인할 수 있게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

from .datasource.datagokr import DataGoKrClient
from .models import RenderedPart, Script, ScriptPart, StoryCandidate, write_json
from .narration import tts_edge
from .publish import seo_metadata
from .render.thumbnail import build_thumbnail
from .render.video_builder import build_part
from .script import script_writer
from .script.llm_client import build_provider
from .settings import CACHE_DIR, OUTPUT_DIR, STATE_DB, Settings
from .subtitles import subtitle_builder
from .topics import history, story_finder, topic_scorer
from .visuals.image_collector import ImageCollector
from .visuals.scene_planner import plan_scenes


@dataclass
class PipelineResult:
    task_id: str
    candidate: StoryCandidate
    script: Script
    parts: list[RenderedPart]
    work_dir: Path


def select_topic(settings: Settings) -> StoryCandidate | None:
    client = DataGoKrClient(settings.secrets.data_go_kr_service_key)
    candidates = story_finder.collect_candidates(
        client,
        settings.get("topics.datasets", []),
        settings.get("topics.candidates_per_dataset", 10),
    )
    return topic_scorer.pick(
        candidates,
        settings.get("topics.weights", {}),
        history.used_topic_keys(STATE_DB),
    )


def write_script(settings: Settings, candidate: StoryCandidate) -> Script:
    provider = build_provider(
        settings.get("script.llm.provider", "gemini"),
        settings.get("script.llm.model", ""),
        settings.secrets,
    )
    return script_writer.generate(
        candidate,
        provider,
        settings.get("script.part1_target_chars", 190),
        settings.get("script.part2_target_chars", 200),
        settings.get("script.llm.temperature", 0.9),
    )


def run(settings: Settings, render: bool = True) -> PipelineResult | None:
    candidate = select_topic(settings)
    if candidate is None:
        logger.error("선정할 주제 후보가 없습니다")
        return None

    logger.info(f"주제 선정: {candidate.headline} ({candidate.headline_number}) score={candidate.score:.3f}")

    task_id = f"{datetime.now():%Y%m%d-%H%M%S}"
    work_dir = OUTPUT_DIR / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    write_json(work_dir / "topic.json", candidate)

    script = write_script(settings, candidate)
    write_json(work_dir / "script.json", script)

    if not render:
        logger.info(f"dry-run: 텍스트 산출물만 생성했습니다 → {work_dir}")
        return PipelineResult(task_id, candidate, script, [], work_dir)

    collector = ImageCollector(
        endpoint=settings.get("visuals.wikimedia_endpoint"),
        allowed_licenses=settings.get("visuals.allowed_licenses", []),
        cache_dir=CACHE_DIR / "images",
        width=settings.get("video.width"),
        height=settings.get("video.height"),
    )
    provider = build_provider(
        settings.get("script.llm.provider", "gemini"),
        settings.get("script.llm.model", ""),
        settings.secrets,
    )

    parts: list[RenderedPart] = []
    for part in (script.part1, script.part2):
        parts.append(
            _render_part(settings, script, part, candidate, collector, provider, work_dir)
        )

    # 내레이션 길이가 채워진 뒤의 대본을 다시 남긴다. 디스크의 기록이 실제 영상과
    # 어긋나 있으면 나중에 그 대본으로 재렌더할 수 없다.
    write_json(work_dir / "script.json", script)

    if collector.placeholder_count:
        logger.warning(
            f"컷 {collector.placeholder_count}개가 사진 대신 단색 배경으로 채워졌습니다. "
            "검색어를 손보거나 네트워크 상태를 확인하세요."
        )

    history.mark_used(STATE_DB, candidate.topic_key, candidate.headline)
    write_json(
        work_dir / "result.json",
        {
            "task_id": task_id,
            "topic_key": candidate.topic_key,
            "is_sample": script.is_sample,
            "parts": [
                {
                    "part": part.part,
                    "video": part.video_path,
                    "thumbnail": part.thumbnail_path,
                    "duration": part.duration,
                    "title": part.metadata.title,
                    "title_alternatives": part.metadata.title_alternatives,
                    "description": part.metadata.description,
                    "tags": part.metadata.tags,
                }
                for part in parts
            ],
        },
    )
    return PipelineResult(task_id, candidate, script, parts, work_dir)


def _render_part(
    settings: Settings,
    script: Script,
    part: ScriptPart,
    candidate: StoryCandidate,
    collector: ImageCollector,
    provider,
    work_dir: Path,
) -> RenderedPart:
    logger.info(f"{part.part}부 내레이션 합성")
    tts_edge.synthesize_part(
        part,
        settings.get("audio.voice"),
        settings.get("audio.rate"),
        work_dir / "audio",
    )

    cues = subtitle_builder.build_cues(
        part,
        settings.get("subtitle.max_chars_per_line", 16),
        # 훅 숫자는 화면 한가운데 크게 떠 있으므로 자막으로 한 번 더 쓰지 않는다.
        suppress_texts=[part.hook_number],
    )
    subtitle_builder.write_srt(cues, work_dir / f"part{part.part}.srt")

    scenes = plan_scenes(
        part,
        cut_duration_max=settings.get("video.cut_duration_max", 3.0),
        freeze_last=part.part == 1,
        series_labels=[point.label for point in candidate.series],
    )

    _warn_if_off_target(settings, part)

    logger.info(f"{part.part}부 렌더 ({part.duration:.1f}초, 컷 {len(scenes)}개)")
    video_path = work_dir / f"part{part.part}.mp4"
    duration = build_part(
        script, part, candidate, scenes, cues, collector, settings, work_dir, video_path
    )

    thumbnail_path = build_thumbnail(
        video_path,
        part,
        work_dir / f"part{part.part}_thumb.jpg",
        (settings.get("video.width"), settings.get("video.height")),
    )

    metadata = seo_metadata.generate(
        script, part, candidate, provider, settings.get("publish.default_tags", [])
    )

    return RenderedPart(
        part=part.part,
        video_path=str(video_path),
        thumbnail_path=str(thumbnail_path),
        duration=duration,
        metadata=metadata,
    )


def _warn_if_off_target(settings: Settings, part: ScriptPart) -> None:
    """쇼츠는 길이가 곧 노출이다. 목표 구간을 벗어나면 렌더 전에 알린다."""
    low = settings.get("video.part_duration_min", 30)
    high = settings.get("video.part_duration_max", 40)
    if part.duration < low:
        logger.warning(
            f"{part.part}부가 {part.duration:.1f}초로 목표({low}~{high}초)보다 짧습니다. "
            "대본 문장을 늘리거나 LLM 목표 글자 수를 올리세요."
        )
    elif part.duration > high:
        logger.warning(
            f"{part.part}부가 {part.duration:.1f}초로 목표({low}~{high}초)보다 깁니다."
        )


def review_and_publish(
    settings: Settings, result: PipelineResult, wait_seconds: int
) -> list[str]:
    from .publish.youtube_uploader import SampleDataUploadBlocked, get_service, upload_series
    from .review.telegram_bot import TelegramReviewer, build_summary

    reviewer = TelegramReviewer(
        settings.secrets.telegram_bot_token, settings.secrets.telegram_chat_id
    )
    if not reviewer.enabled:
        logger.warning("텔레그램 설정이 없어 검수를 건너뜁니다. 업로드하지 않습니다.")
        return []

    first = result.parts[0].metadata
    titles = [first.title, *first.title_alternatives]
    summary = build_summary(
        result.script.title_seed, titles, first.description, result.script.is_sample
    )

    history.save_pending(
        STATE_DB,
        result.task_id,
        {"topic_key": result.candidate.topic_key, "titles": titles},
    )
    reviewer.send_review(
        result.task_id, summary, titles, [Path(part.video_path) for part in result.parts]
    )

    decision = reviewer.wait_for_decision(result.task_id, wait_seconds)
    if decision is None or not decision.approved:
        history.set_review_status(STATE_DB, result.task_id, "rejected")
        logger.info("승인되지 않아 업로드하지 않습니다")
        return []

    if result.script.is_sample:
        history.set_review_status(STATE_DB, result.task_id, "blocked_sample")
        reviewer.notify("샘플 데이터로 만든 영상이라 업로드를 차단했습니다.")
        raise SampleDataUploadBlocked(result.task_id)

    # 사람이 고른 제목을 1부에 반영하고, 2부는 같은 어투로 맞춘다.
    chosen = titles[min(decision.title_index, len(titles) - 1)]
    result.parts[0].metadata.title = chosen

    service = get_service(
        settings.secrets.youtube_client_secret_file, settings.secrets.youtube_token_file
    )
    video_ids = upload_series(
        service,
        [
            (Path(part.video_path), part.metadata, Path(part.thumbnail_path))
            for part in result.parts
        ],
        settings.get("publish.privacy_status", "private"),
        settings.get("publish.category_id", "25"),
    )

    history.set_review_status(STATE_DB, result.task_id, "uploaded")
    reviewer.notify(
        "업로드 완료\n"
        + "\n".join(
            f"{index + 1}부: https://www.youtube.com/watch?v={video_id}"
            for index, video_id in enumerate(video_ids)
        )
    )
    return video_ids
