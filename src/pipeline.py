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
from .heritage import heritage_media
from . import assets
from . import disasters
from .models import PhotoCredit, RenderedPart, Script, ScriptPart, StoryCandidate, write_json
from .motion.veo_budget import VeoBudget
from .motion.veo_client import VeoClient
from .narration import tts_engine
from .narration.tts_cloud import GoogleCloudTTS
from .narration.tts_edge import EdgeTTS
from .narration.tts_gemini import GeminiTTS
from .narration.tts_supertonic import SupertonicEngine
from .narration.tts_voxcpm import VoxCPMEngine
from .publish import seo_metadata
from .render.thumbnail import build_thumbnail
from .render.video_builder import build_part
from .script import script_writer
from .script.llm_client import build_provider
from .cloud.spend_guard import SpendGuard
from .cloud.vertex import VertexAuth, VertexConfig, project_from_env
from .settings import CACHE_DIR, OUTPUT_DIR, STATE_DB, Settings
from .subtitles import subtitle_builder
from . import heritage, sillok
from .topics import history, topic_scorer
from .render.motion_sources import PhotoMissing
from .render.video_builder import photo_credit_line
from .visuals.image_collector import ImageCollector
from .visuals.photo_sources import build_chain
from .visuals.ai_image import AiImageClient
from .visuals.scene_planner import (
    engine_summary,
    interleave_veo,
    plan_scenes,
    plan_typed_scenes,
)


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


def select_topic(settings: Settings, ignore_history: bool = False) -> StoryCandidate | None:
    """이번 회차 주제 하나. 소스는 `topics.source` 로 고른다.

    문화재·실록 경로는 지우지 않았다 — 다른 소재가 필요할 때 그대로 다시 쓴다.
    """
    source = settings.get("topics.source", "heritage")
    if source == "sillok":
        candidates = sillok.collect_candidates(settings.get("topics.sillok.article_ids", []))
        weights = settings.get("topics.sillok.weights", {})
    elif source == "disaster":
        candidates = disasters.collect_candidates(settings.get("topics.disaster.titles", []))
        weights = settings.get("topics.disaster.weights", {})
    else:
        client = DataGoKrClient(settings.secrets.data_go_kr_service_key)
        candidates = heritage.collect_candidates(
            client,
            settings.get("topics.candidates_per_dataset", 8),
        )
        weights = settings.get("topics.weights", {})

    # 같은 소재를 다시 렌더할 때(연출을 고치며 반복할 때)는 중복 방지를 건너뛴다.
    used = set() if ignore_history else history.used_topic_keys(STATE_DB)
    return topic_scorer.pick(candidates, weights, used)


def build_llm_provider(settings: Settings):
    """대본 생성기. Vertex 가 준비돼 있으면 GCP 크레딧 쪽으로 간다."""
    vertex, auth = build_vertex(settings)
    return build_provider(
        settings.get("script.llm.provider", "gemini"),
        settings.get("script.llm.model", ""),
        settings.secrets,
        vertex=vertex,
        auth=auth,
    )


def write_script(settings: Settings, candidate: StoryCandidate) -> Script:
    provider = build_llm_provider(settings)
    return script_writer.generate(
        candidate,
        provider,
        settings.get("script.part1_target_chars", 190),
        settings.get("script.part2_target_chars", 200),
        settings.get("script.llm.temperature", 0.9),
    )


def build_collector(settings: Settings, candidate: StoryCandidate | None = None) -> ImageCollector:
    """사진 소스 체인. 실록·재난 소재에서는 **웹 검색을 아예 끈다.**

    "하늘에 호리병 같은 게 떠올랐다"를 텍스트로 검색하면 현대 우체국·카페 사진이 끌려
    들어온다. 재난 소재도 마찬가지로 3D 단면도 인포그래픽을 생성으로만 만든다 — 실제
    사고 사진은 유가족·저작권 문제가 있어 쓰지 않는다. 실사 검색은 문화재 소재에서만 쓴다.
    """
    if candidate is not None and candidate.dataset in ("sillok", "disaster"):
        logger.info(f"[visuals] {candidate.dataset} 소재 — 웹 이미지 검색을 사용하지 않습니다")
        return ImageCollector([], CACHE_DIR / "images")

    sources = build_chain(
        settings.secrets.data_go_kr_service_key,
        settings.get("visuals.allowed_licenses", []),
        settings.get("video.width", 1080),
    )
    priority_images = []
    fallback_queries: list[tuple[str, str]] = []
    if candidate is not None:
        if candidate.heritage_ccba_kdcd:
            priority_images = heritage_media.fetch_images(
                candidate.heritage_ccba_kdcd,
                candidate.heritage_ccba_asno,
                candidate.heritage_ccba_ctcd,
            )
        # 컷 전용 검색어(LLM 이 지어낸 것)가 빗나갔을 때 쓸 주제 대표 검색어.
        fallback_queries = list(
            zip(
                candidate.image_queries_ko,
                candidate.image_queries_en + [""] * len(candidate.image_queries_ko),
            )
        )
    return ImageCollector(
        sources,
        CACHE_DIR / "images",
        priority_images=priority_images,
        fallback_queries=fallback_queries,
    )


def fetch_official_video(
    settings: Settings, candidate: StoryCandidate, work_dir: Path
) -> tuple[Path, float] | None:
    """도입부에 쓸 국가유산청 공식 영상을 미리 내려받는다.

    있으면 Veo 생성을 그만큼 건너뛴다. 식별자가 없거나(픽스처 등) 영상이 없으면 None을
    돌려주고, 호출하는 쪽은 지금까지처럼 Veo → 파랄랙스 순으로 진행한다.
    """
    if not candidate.heritage_ccba_kdcd:
        return None

    videos = heritage_media.fetch_videos(
        candidate.heritage_ccba_kdcd,
        candidate.heritage_ccba_asno,
        candidate.heritage_ccba_ctcd,
    )
    chosen = heritage_media.select_video(videos)
    if chosen is None:
        return None

    dest = heritage_media.download_video(chosen, work_dir / "official_intro.mp4")
    if dest is None:
        return None

    # 실제 응답엔 재생시간 정보가 없다(url 뿐) — 받은 파일을 직접 열어 길이를 잰다.
    max_seconds = float(settings.get("veo.duration_seconds", 8))
    try:
        from moviepy import VideoFileClip

        with VideoFileClip(str(dest)) as clip:
            duration = min(clip.duration, max_seconds)
    except Exception as exc:
        logger.warning(f"공식 동영상을 열지 못해 이번엔 건너뜁니다: {exc}")
        return None

    if duration < 1.0:
        logger.warning("공식 동영상이 너무 짧아(1초 미만) 건너뜁니다")
        return None

    return dest, duration


def build_tts_engines(settings: Settings) -> list:
    """설정한 엔진을 앞에 두고, 실패했을 때 내려갈 자리를 뒤에 붙인다.

    VoxCPM(로컬 GPU 모델)이 항상 1순위, Supertonic(로컬 HTTP 서버)이 2순위다 —
    설정과 무관하게 둘 다 항상 시도한다. GPU 가 없는 환경(GitHub Actions 러너 등)에서는
    VoxCPM 로딩이 조용히 실패해 Supertonic 으로 자연스럽게 넘어간다.
    """
    vertex, auth = build_vertex(settings)
    voxcpm = VoxCPMEngine()
    supertonic = SupertonicEngine(
        base_url=settings.get("audio.supertonic_url", "http://127.0.0.1:7788/v1/tts"),
        voice=settings.get("audio.supertonic_voice", "M1"),
        lang=settings.get("audio.supertonic_lang", "ko"),
        steps=settings.get("audio.supertonic_steps", 64),
    )
    gemini = GeminiTTS(
        api_keys=settings.secrets.gemini_api_keys,
        voice=settings.get("audio.gemini_voice", "Fenrir"),
        model=settings.get("audio.gemini_model", ""),
        style_prompt=settings.get("audio.gemini_style_prompt", ""),
        backend="vertex" if vertex is not None else "aistudio",
        vertex=vertex,
        auth=auth,
        spend_guard=build_spend_guard(settings),
        cost_krw=settings.get("cloud.tts_cost_krw", 50),
    )
    edge = EdgeTTS(
        settings.get("audio.edge_voice", "hyunsu"),
        pitch=settings.get("audio.edge_pitch", "+0Hz"),
    )
    cloud_tts = GoogleCloudTTS(
        auth=auth,
        voice=settings.get("audio.cloud_tts_voice", "ko-KR-Neural2-C"),
        pitch_semitones=settings.get("audio.cloud_tts_pitch", -1.5),
        spend_guard=build_spend_guard(settings),
        cost_krw=settings.get("cloud.tts_cost_krw", 50),
    )

    # voxcpm(GPU 전용)과 supertonic(로컬 서버 전용)은 GitHub Actions 러너에서는
    # 구조적으로 절대 못 뜬다 — 폴백 자체는 정상 동작이다.
    #
    # cloud_tts(Google Cloud Text-to-Speech, Neural2 음성)를 gemini 보다 앞에 둔다 —
    # Gemini 의 멀티모달 오디오 출력(generateContent 의 AUDIO 응답)은 이 채널이
    # 원하는 "다큐 성우" 톤을 내기엔 부족했다(사용자 청취 확인). Cloud TTS 는 이
    # 목적으로 만들어진 전용 합성 엔진이고, SSML 로 피치를 직접 지시할 수 있어
    # `audio.cloud_tts_pitch` 로 더 낮고 무게감 있는 톤을 낼 수 있다.
    engine = settings.get("audio.engine", "cloud_tts")
    if engine == "edge":
        quality_chain = [edge]
    elif engine == "gemini":
        quality_chain = [gemini, cloud_tts]
    else:
        quality_chain = [cloud_tts, gemini]

    # audio.require_quality_voice(기본 true): 품질 엔진이 전부 실패했을 때 edge-tts
    # (기계음)로 조용히 떨어지는 대신, 아예 edge 를 체인에서 빼서 synthesize_part 가
    # TTSFailure 를 던지고 렌더를 멈추게 한다 — "저품질 목소리로 끝까지 만들어놓고
    # 나서야 실패를 안다"를 없애기 위함. edge 를 의도적으로 쓰려면(비용 0원이 최우선일
    # 때) 이 값을 false 로 내리거나 audio.engine 을 "edge" 로 지정한다.
    if engine != "edge" and not settings.get("audio.require_quality_voice", True):
        quality_chain = [*quality_chain, edge]

    return [voxcpm, supertonic, *quality_chain]


def build_spend_guard(settings: Settings) -> SpendGuard:
    """크레딧 원장. 남은 금액이 모자라면 생성 호출 자체를 막는다."""
    return SpendGuard(
        ledger_path=CACHE_DIR / "spend_ledger.json",
        credit_krw=settings.get("cloud.credit_krw", 0),
        reserve_krw=settings.get("cloud.reserve_krw", 20000),
        enabled=bool(settings.get("cloud.stop_when_exhausted", True)),
    )


def build_vertex(settings: Settings) -> tuple[VertexConfig | None, VertexAuth | None]:
    """Vertex 설정과 인증. 프로젝트나 인증 정보가 없으면 (None, None)."""
    if settings.get("cloud.backend", "aistudio") != "vertex":
        return None, None

    project_id = settings.get("cloud.project_id", "") or project_from_env()
    if not project_id:
        logger.warning(
            "[cloud] Vertex 를 쓰려면 프로젝트 ID 가 필요합니다 "
            "(config cloud.project_id 또는 VERTEX_PROJECT_ID)"
        )
        return None, None

    auth = VertexAuth(settings.secrets.vertex_service_account_file)
    if not auth.available:
        logger.warning(
            "[cloud] Vertex 인증 정보를 찾지 못했습니다 "
            "(VERTEX_SERVICE_ACCOUNT_FILE 또는 gcloud ADC)"
        )
        return None, None

    return VertexConfig(project_id=project_id, location=settings.get("cloud.location", "us-central1")), auth


def build_veo_client(settings: Settings, spend_guard: SpendGuard | None = None) -> VeoClient:
    tier = settings.get("veo.tier", "lite")
    budget = VeoBudget(
        cache_dir=CACHE_DIR / "veo",
        tier=tier,
        max_clips_per_episode=settings.get("veo.max_clips_per_episode", 4),
        max_clips_per_day=settings.get("veo.max_clips_per_day", 12),
    )
    vertex, auth = build_vertex(settings)
    backend = "vertex" if vertex is not None else "aistudio"
    return VeoClient(
        api_key=settings.secrets.gemini_api_key,
        budget=budget,
        tier=tier,
        resolution=settings.get("veo.resolution", "720p"),
        duration_seconds=settings.get("veo.duration_seconds", 8),
        backend=backend,
        vertex=vertex,
        auth=auth,
        spend_guard=spend_guard or build_spend_guard(settings),
    )


def build_ai_image_client(
    settings: Settings, spend_guard: SpendGuard | None = None
) -> AiImageClient:
    """장면 생성용 이미지 클라이언트.

    Vertex 가 준비돼 있으면 GCP 크레딧으로, 아니면 AI Studio 키로 간다. 둘 다 막히면
    호출하지 않고 대체 화면으로 내려간다.
    """
    vertex, auth = build_vertex(settings)
    return AiImageClient(
        settings.secrets.gemini_api_keys,
        CACHE_DIR / "ai_images",
        settings.get("visuals.ai_image_model", ""),
        backend="vertex" if vertex is not None else "aistudio",
        vertex=vertex,
        auth=auth,
        spend_guard=spend_guard or build_spend_guard(settings),
        vertex_model=settings.get("cloud.image_model", ""),
        cost_krw=settings.get("cloud.image_cost_krw", 30),
    )


def _print_script_preview(script: Script) -> None:
    """비싼 렌더(Veo/전체 TTS) 전에 대본과 영문 AI 프롬프트를 콘솔에 요약 출력한다.

    사용자가 이 요약만 보고 Y/N 으로 전체 렌더 진행 여부를 정할 수 있게 한다 —
    외부 API 를 다시 부르지 않고 이미 만들어진 `script` 객체만 순회한다.
    """
    print(f"\n===== 대본 미리보기: {script.title_seed} =====")
    for part in (script.part1, script.part2):
        print(f"\n--- {part.part}부 · {part.hook_number} ---")
        for line in part.lines:
            print(f"  [{line.scene_type}] {line.text}")
            if line.prompt:
                print(f"      prompt: {line.prompt}")
    print("=====================================\n")


def _truncate_script_for_preview(script: Script, target_seconds: float) -> None:
    """콘셉트만 싸게 확인할 때 쓴다 — 1부만 남기고, 목표 초 안쪽으로 문장을 줄인다.

    아직 TTS 를 돌리기 전이라 정확한 길이는 모른다. `tts_engine` 이 무음 폴백 길이를
    추정할 때 쓰는 것과 같은 대략치(글자수 ÷ CHARS_PER_SECOND)로 충분하다 — 정밀한
    초가 아니라 "이쯤에서 끊으면 몇 문장"만 알면 된다. 씬(컷) 수가 줄어드는 만큼
    AI 이미지/Veo 호출 수도 그대로 줄어 비용이 실제로 낮아진다.
    """
    kept = []
    total = 0.0
    for line in script.part1.lines:
        estimate = max(len(line.narration) / tts_engine.CHARS_PER_SECOND, tts_engine.MIN_DURATION)
        if kept and total + estimate > target_seconds:
            break
        kept.append(line)
        total += estimate
    script.part1.lines = kept or script.part1.lines[:1]
    script.part2.lines = []


def run(
    settings: Settings,
    render: bool = True,
    use_veo: bool = True,
    reuse_topic: bool = False,
    part1_only: bool = False,
    confirm_before_render: bool = False,
    preview_seconds: float | None = None,
) -> PipelineResult | None:
    candidate = select_topic(settings, ignore_history=reuse_topic)
    if candidate is None:
        logger.error("선정할 주제 후보가 없습니다")
        return None

    logger.info(f"주제 선정: {candidate.headline} ({candidate.headline_number}) score={candidate.score:.3f}")

    task_id = f"{datetime.now():%Y%m%d-%H%M%S}"
    work_dir = OUTPUT_DIR / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    write_json(work_dir / "topic.json", candidate)

    script = write_script(settings, candidate)
    if preview_seconds is not None:
        _truncate_script_for_preview(script, preview_seconds)
        part1_only = True
        logger.warning(
            f"프리뷰 모드: 1부를 약 {preview_seconds:.0f}초 분량({len(script.part1.lines)}문장)으로 "
            "줄여 렌더합니다 — 이 결과는 [0~5초 오프닝 훅]만 보여줍니다. [중반 반전 훅]· "
            "[클라이맥스 후킹](2부)은 이 컷에 아예 포함되지 않으니, 대본 구조나 톤을 "
            "평가할 때는 --preview-seconds 없이 전체 렌더로 다시 확인하세요."
        )
    write_json(work_dir / "script.json", script)

    if not render:
        logger.info(f"dry-run: 텍스트 산출물만 생성했습니다 → {work_dir}")
        return PipelineResult(task_id, candidate, script, [], work_dir, [])

    if confirm_before_render:
        _print_script_preview(script)
        answer = input("이대로 전체 렌더링을 진행할까요? (Y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            logger.info("사용자가 렌더링을 취소했습니다 (--confirm 에서 N 응답)")
            return PipelineResult(task_id, candidate, script, [], work_dir, [])

    assets.warn_if_missing()
    spend_guard = build_spend_guard(settings)
    logger.info(f"[지출] {spend_guard.summary()}")
    collector = build_collector(settings, candidate)
    veo_client = build_veo_client(settings, spend_guard) if use_veo else None
    ai_client = build_ai_image_client(settings, spend_guard)
    provider = build_llm_provider(settings)
    official_video = fetch_official_video(settings, candidate, work_dir)
    if official_video:
        logger.info(f"공식 동영상을 도입부에 씁니다 ({official_video[1]:.1f}초) — Veo 생략")

    parts: list[RenderedPart] = []
    credits: list[PhotoCredit] = []
    parts_to_render = (script.part1,) if part1_only else (script.part1, script.part2)
    if part1_only:
        logger.info("검증 모드: 1부만 렌더/전송합니다 (--part1-only)")
    try:
        for part in parts_to_render:
            rendered, part_credits = _render_part(
                settings,
                script,
                part,
                candidate,
                collector,
                provider,
                work_dir,
                veo_client,
                official_video,
                ai_client,
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
    official_video: tuple[Path, float] | None = None,
    ai_client=None,
) -> tuple[RenderedPart, list[PhotoCredit]]:
    logger.info(f"{part.part}부 내레이션 합성")
    tts_engine.synthesize_part(
        part,
        build_tts_engines(settings),
        work_dir / "audio",
        require_quality=settings.get("audio.require_quality_voice", True),
    )

    cues = subtitle_builder.build_cues(
        part,
        settings.get("subtitle.max_chars_per_line", 16),
        # 훅 숫자는 화면 한가운데 크게 떠 있으므로 자막으로 한 번 더 쓰지 않는다.
        suppress_texts=[part.hook_number],
    )
    subtitle_builder.write_srt(cues, work_dir / f"part{part.part}.srt")

    # 대본이 문장마다 화면 종류를 정해줬으면(실록 경로) 그대로 따른다. 사진 검색을 아예
    # 하지 않는 경로라 아래 Veo 도입부·교차 배치 로직을 타지 않는다.
    if _has_scene_types(part):
        scenes = plan_typed_scenes(part, freeze_last=part.part == 1)
        return _finish_part(
            settings, script, part, candidate, collector, provider, work_dir,
            veo_client, ai_client, scenes, cues,
        )

    # 공식 동영상이 있으면 그걸 도입부로 쓰고 Veo 호출 자체를 건너뛴다 — 없으면 지금까지처럼
    # Veo(있을 때)로 도입부를 채운다.
    veo_seconds = 0.0
    intro_engine = "veo"
    intro_video_path = ""
    if official_video is not None:
        intro_engine = "official_video"
        intro_video_path = str(official_video[0])
        veo_seconds = official_video[1]
    elif veo_client is not None and veo_client.enabled and settings.get("veo.clips_per_part", 1):
        veo_seconds = float(veo_client.duration_seconds)

    scenes = plan_scenes(
        part,
        cut_duration_max=settings.get("video.cut_duration_max", 3.0),
        freeze_last=part.part == 1,
        series_labels=[point.label for point in candidate.series],
        veo_seconds=veo_seconds,
        intro_engine=intro_engine,
        intro_video_path=intro_video_path,
    )

    # 사진 → 그 사진이 움직이는 Veo 컷 → 다음 사진 … 으로 리듬을 만든다.
    if veo_client is not None and veo_client.enabled:
        scenes = interleave_veo(
            scenes,
            clip_seconds=float(veo_client.duration_seconds),
            still_seconds=settings.get("veo.still_seconds", 6.0),
            max_clips=settings.get("veo.clips_per_part", 2),
        )

    return _finish_part(
        settings, script, part, candidate, collector, provider, work_dir,
        veo_client, ai_client, scenes, cues,
    )


def _has_scene_types(part: ScriptPart) -> bool:
    """문장에 화면 종류가 실려 있나. 문화재 경로는 전부 기본값(AI_IMAGE)이 아니다."""
    return any(line.scene_type in ("HISTORICAL_DOC", "MAP_MOTION", "VEO") for line in part.lines)


def _finish_part(
    settings: Settings,
    script: Script,
    part: ScriptPart,
    candidate: StoryCandidate,
    collector: ImageCollector,
    provider,
    work_dir: Path,
    veo_client,
    ai_client,
    scenes,
    cues,
) -> tuple[RenderedPart, list[PhotoCredit]]:
    """씬 배정이 끝난 뒤는 두 경로가 같다 — 렌더하고 썸네일·메타데이터를 붙인다."""
    _warn_if_off_target(settings, part)

    logger.info(
        f"{part.part}부 렌더 ({part.duration:.1f}초, 컷 {len(scenes)}개 · {engine_summary(scenes)})"
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
        ai_client=ai_client,
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
    return _finish_review(
        settings, reviewer, result.task_id, result.script.is_sample, result.parts, titles, decision
    )


def resume_pending_reviews(settings: Settings, wait_seconds: int) -> dict[str, list[str]]:
    """PC 가 꺼지는 등으로 검수 대기가 끊긴 회차들을 다시 확인한다.

    렌더된 영상과 텔레그램 메시지는 이미 나가 있는 상태이므로(review_and_publish 가
    `history.save_pending` 으로 남겨둠), 여기서는 다시 렌더하지 않고 그때 저장해둔
    output/<task_id>/result.json 을 읽어 업로드에 필요한 정보만 재구성한다. 사용자가
    PC를 끄기 전에 이미 승인 버튼을 눌렀어도, 텔레그램은 getUpdates 로 가져가기 전까지
    그 콜백을 들고 있으므로 다음에 실행했을 때 그대로 받아진다.
    """
    import json

    from .models import RenderedPart, VideoMetadata
    from .review.telegram_bot import TelegramReviewer

    reviewer = TelegramReviewer(
        settings.secrets.telegram_bot_token, settings.secrets.telegram_chat_id
    )
    if not reviewer.enabled:
        logger.warning("텔레그램 설정이 없어 대기 중인 승인을 확인할 수 없습니다")
        return {}

    pending = history.list_pending_reviews(STATE_DB)
    if not pending:
        logger.info("대기 중인 승인이 없습니다")
        return {}

    tasks: dict[str, dict] = {}
    for row in pending:
        task_id = row["task_id"]
        result_path = OUTPUT_DIR / task_id / "result.json"
        if not result_path.exists():
            logger.warning(f"{task_id}: 산출물(result.json)이 없어 건너뜁니다")
            history.set_review_status(STATE_DB, task_id, "missing_output")
            continue
        data = json.loads(result_path.read_text(encoding="utf-8"))
        data["titles"] = row["payload"].get("titles") or [data["parts"][0]["title"]]
        tasks[task_id] = data

    if not tasks:
        return {}

    logger.info(f"대기 중인 승인 {len(tasks)}건을 확인합니다: {', '.join(tasks)}")
    decisions = reviewer.wait_for_decisions(list(tasks), wait_seconds)

    results: dict[str, list[str]] = {}
    for task_id, data in tasks.items():
        decision = decisions.get(task_id)
        if decision is None:
            logger.info(f"{task_id}: 아직 응답이 없습니다 — 다음에 다시 확인하세요")
            continue

        parts = [
            RenderedPart(
                part=part["part"],
                video_path=part["video"],
                thumbnail_path=part["thumbnail"],
                duration=part["duration"],
                metadata=VideoMetadata(
                    title=part["title"],
                    title_alternatives=part["title_alternatives"],
                    description=part["description"],
                    tags=part["tags"],
                ),
            )
            for part in data["parts"]
        ]
        results[task_id] = _finish_review(
            settings, reviewer, task_id, data["is_sample"], parts, data["titles"], decision
        )
    return results


def _finish_review(
    settings: Settings,
    reviewer,
    task_id: str,
    is_sample: bool,
    parts: list[RenderedPart],
    titles: list[str],
    decision,
) -> list[str]:
    from .publish.youtube_uploader import SampleDataUploadBlocked, get_service, upload_series

    if decision is None or not decision.approved:
        history.set_review_status(STATE_DB, task_id, "rejected")
        logger.info(f"{task_id}: 승인되지 않아 업로드하지 않습니다")
        return []

    if is_sample:
        history.set_review_status(STATE_DB, task_id, "blocked_sample")
        reviewer.notify("샘플 데이터로 만든 영상이라 업로드를 차단했습니다.")
        raise SampleDataUploadBlocked(task_id)

    # 사람이 고른 제목을 1부에 반영하고, 2부는 같은 어투로 맞춘다.
    chosen = titles[min(decision.title_index, len(titles) - 1)]
    parts[0].metadata.title = chosen

    service = get_service(
        settings.secrets.youtube_client_secret_file, settings.secrets.youtube_token_file
    )
    video_ids = upload_series(
        service,
        [(Path(part.video_path), part.metadata, Path(part.thumbnail_path)) for part in parts],
        settings.get("publish.privacy_status", "private"),
        settings.get("publish.category_id", "25"),
    )

    history.set_review_status(STATE_DB, task_id, "uploaded")
    reviewer.notify(
        "업로드 완료\n"
        + "\n".join(
            f"{index + 1}부: https://www.youtube.com/watch?v={video_id}"
            for index, video_id in enumerate(video_ids)
        )
    )
    return video_ids
