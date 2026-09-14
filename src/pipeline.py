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
from .models import PhotoCredit, RenderedPart, Script, ScriptPart, StoryCandidate, write_json
from .motion.veo_budget import VeoBudget
from .motion.veo_client import VeoClient
from .narration import tts_engine
from .narration.tts_edge import EdgeTTS
from .narration.tts_gemini import GeminiTTS
from .publish import seo_metadata
from .render.thumbnail import build_thumbnail
from .render.video_builder import build_part
from .script import script_writer
from .script.llm_client import build_provider
from .settings import CACHE_DIR, OUTPUT_DIR, STATE_DB, Settings
from .subtitles import subtitle_builder
from .topics import history, story_finder, topic_scorer
from .render.motion_sources import PhotoMissing
from .render.video_builder import photo_credit_line
from .visuals.image_collector import ImageCollector
from .visuals.photo_sources import build_chain
from .visuals.scene_planner import engine_counts, plan_scenes


@dataclass
class PipelineResult:
    task_id: str
    candidate: StoryCandidate
    script: Script
    parts: list[RenderedPart]
    work_dir: Path
    credits: list[PhotoCredit]
    veo_clips: int = 0
    cost_note: str = ""


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


def build_collector(settings: Settings) -> ImageCollector:
    sources = build_chain(
        settings.secrets.data_go_kr_service_key,
        settings.get("visuals.allowed_licenses", []),
        settings.get("video.width", 1080),
    )
    return ImageCollector(sources, CACHE_DIR / "images")


def build_tts_engines(settings: Settings) -> list:
    """설정한 엔진을 앞에 두고, 실패했을 때 내려갈 자리를 뒤에 붙인다."""
    gemini = GeminiTTS(
        api_key=settings.secrets.gemini_api_key,
        voice=settings.get("audio.gemini_voice", "Charon"),
        model=settings.get("audio.gemini_model", ""),
        style_prompt=settings.get("audio.gemini_style_prompt", ""),
        min_request_interval=settings.get("audio.gemini_min_request_interval", 1.5),
    )
    edge = EdgeTTS(settings.get("audio.edge_voice", "hyunsu"))

    if settings.get("audio.engine", "gemini") == "edge":
        return [edge]
    return [gemini, edge]


def build_veo_client(settings: Settings) -> VeoClient:
    tier = settings.get("veo.tier", "lite")
    budget = VeoBudget(
        cache_dir=CACHE_DIR / "veo",
        tier=tier,
        max_clips_per_episode=settings.get("veo.max_clips_per_episode", 2),
        max_clips_per_day=settings.get("veo.max_clips_per_day", 6),
    )
    return VeoClient(
        api_key=settings.secrets.gemini_api_key,
        budget=budget,
        tier=tier,
        resolution=settings.get("veo.resolution", "720p"),
        duration_seconds=settings.get("veo.duration_seconds", 8),
    )


def run(
    settings: Settings, render: bool = True, use_veo: bool = True
) -> PipelineResult | None:
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
        return PipelineResult(task_id, candidate, script, [], work_dir, [])

    collector = build_collector(settings)
    veo_client = build_veo_client(settings) if use_veo else None
    provider = build_provider(
        settings.get("script.llm.provider", "gemini"),
        settings.get("script.llm.model", ""),
        settings.secrets,
    )

    parts: list[RenderedPart] = []
    credits: list[PhotoCredit] = []
    try:
        for part in (script.part1, script.part2):
            rendered, part_credits = _render_part(
                settings, script, part, candidate, collector, provider, work_dir, veo_client
            )
            parts.append(rendered)
            credits.extend(
                credit for credit in part_credits if credit not in credits
            )
    except PhotoMissing as exc:
        # 사진이 없으면 단색으로 때우지 않고 멈춘다. 쓸모없는 영상을 몇 분씩 렌더하고
        # 나서야 알게 되는 편이 훨씬 나쁘다.
        logger.error(
            f"사진을 구하지 못해 렌더를 중단했습니다: {', '.join(exc.queries)}\n"
            f"실패한 검색어 전체: {', '.join(collector.failed_queries)}\n"
            "python main.py photos \"검색어\" 로 어떤 사진이 오는지 먼저 확인하세요."
        )
        return None

    # 내레이션 길이가 채워진 뒤의 대본을 다시 남긴다. 디스크의 기록이 실제 영상과
    # 어긋나 있으면 나중에 그 대본으로 재렌더할 수 없다.
    write_json(work_dir / "script.json", script)

    veo_clips = veo_client.budget.episode_clips if veo_client else 0
    cost_note = (
        veo_client.budget.episode_cost(veo_client.duration_seconds).summary()
        if veo_clips
        else "Veo 사용 없음"
    )
    logger.info(cost_note)

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
            "photo_credits": [credit.__dict__ for credit in credits],
            "veo_clips": veo_clips,
            "cost_note": cost_note,
        },
    )
    return PipelineResult(
        task_id, candidate, script, parts, work_dir, credits, veo_clips, cost_note
    )


def _render_part(
    settings: Settings,
    script: Script,
    part: ScriptPart,
    candidate: StoryCandidate,
    collector: ImageCollector,
    provider,
    work_dir: Path,
    veo_client,
) -> tuple[RenderedPart, list[PhotoCredit]]:
    logger.info(f"{part.part}부 내레이션 합성")
    tts_engine.synthesize_part(part, build_tts_engines(settings), work_dir / "audio")

    cues = subtitle_builder.build_cues(
        part,
        settings.get("subtitle.max_chars_per_line", 16),
        # 훅 숫자는 화면 한가운데 크게 떠 있으므로 자막으로 한 번 더 쓰지 않는다.
        suppress_texts=[part.hook_number],
    )
    subtitle_builder.write_srt(cues, work_dir / f"part{part.part}.srt")

    veo_seconds = 0.0
    if veo_client is not None and veo_client.enabled and settings.get("veo.clips_per_part", 1):
        veo_seconds = float(veo_client.duration_seconds)

    scenes = plan_scenes(
        part,
        cut_duration_max=settings.get("video.cut_duration_max", 3.0),
        freeze_last=part.part == 1,
        series_labels=[point.label for point in candidate.series],
        veo_seconds=veo_seconds,
    )

    _warn_if_off_target(settings, part)

    counts = engine_counts(scenes)
    logger.info(
        f"{part.part}부 렌더 ({part.duration:.1f}초, 컷 {len(scenes)}개"
        f" · Veo {counts['veo']} / 파랄랙스 {counts['parallax']})"
    )
    video_path = work_dir / f"part{part.part}.mp4"
    duration, credits = build_part(
        script,
        part,
        candidate,
        scenes,
        cues,
        collector,
        settings,
        work_dir,
        video_path,
        veo_client=veo_client,
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
    photo_note = photo_credit_line(credits)
    if photo_note:
        metadata.description = f"{metadata.description}\n{photo_note}"

    return (
        RenderedPart(
            part=part.part,
            video_path=str(video_path),
            thumbnail_path=str(thumbnail_path),
            duration=duration,
            metadata=metadata,
        ),
        credits,
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
        result.script.title_seed,
        titles,
        first.description,
        result.script.is_sample,
        result.cost_note,
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
