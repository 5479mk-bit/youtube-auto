"""「숫자가 말해준다」 공공데이터 다큐 쇼츠 자동화 CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from src import pipeline
from src.settings import OUTPUT_DIR, PROJECT_ROOT, load_settings


def _add_file_log() -> None:
    """작업 스케줄러 같은 무인 실행에서 콘솔이 없어도 나중에 원인을 볼 수 있게 한다."""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "run_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
        encoding="utf-8",
        level="INFO",
    )


def main() -> int:
    _add_file_log()
    parser = argparse.ArgumentParser(description="공공데이터 쇼츠 자동 생성")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="한 회차(1부/2부)를 생성한다")
    run_parser.add_argument(
        "--dry-run", action="store_true", help="영상 렌더 없이 주제·대본만 만든다"
    )
    run_parser.add_argument(
        "--no-veo",
        action="store_true",
        help="Veo 를 쓰지 않고 전부 무료 파랄랙스로 만든다 (과금 없음)",
    )
    run_parser.add_argument(
        "--reuse-topic",
        action="store_true",
        help="이미 다룬 주제도 다시 고른다 (연출을 고치며 같은 소재를 재렌더할 때)",
    )
    run_parser.add_argument(
        "--part1-only",
        action="store_true",
        help="1부만 렌더/전송한다 (검증 사이클용 임시 옵션, 과금·시간을 아낀다)",
    )
    run_parser.add_argument(
        "--review",
        action="store_true",
        help="렌더 후 텔레그램으로 검수 요청을 보내고 승인 시 업로드한다",
    )
    run_parser.add_argument(
        "--wait", type=int, default=1800, help="검수 응답을 기다릴 최대 시간(초)"
    )

    subparsers.add_parser("topics", help="주제 후보 순위만 확인한다")

    subparsers.add_parser(
        "credits", help="생성에 쓴 추정 금액과 남은 크레딧을 본다 (호출 없음)"
    )

    cloud_parser = subparsers.add_parser(
        "cloud-check",
        help="Vertex 인증·권한·연결을 점검한다 (기본은 과금 없음)",
    )
    cloud_parser.add_argument(
        "--generate",
        action="store_true",
        help="이미지 1장을 실제로 만들어 끝까지 확인한다 (약 30원 과금)",
    )

    review_pending_parser = subparsers.add_parser(
        "review-pending",
        help="PC가 꺼지는 등으로 끊긴 검수 대기를 다시 확인하고, 승인됐으면 업로드한다",
    )
    review_pending_parser.add_argument(
        "--wait", type=int, default=300, help="응답을 기다릴 최대 시간(초)"
    )

    photos_parser = subparsers.add_parser(
        "photos", help="검색어로 어떤 사진이 오는지 미리 본다 (Veo 호출 없음)"
    )
    photos_parser.add_argument("query", help="한국어 검색어")
    photos_parser.add_argument("--en", default="", help="영어 보조 검색어")

    voice_parser = subparsers.add_parser(
        "voice-test", help="엔진별로 내레이션 샘플을 만들어 들어본다"
    )
    voice_parser.add_argument("text", help="읽게 할 문장")

    veo_parser = subparsers.add_parser(
        "veo-test", help="Veo 클립을 딱 1개만 만들어 본다 (과금 발생)"
    )
    veo_parser.add_argument("image", help="움직이게 만들 사진 경로")
    veo_parser.add_argument(
        "--prompt", default="", help="생성 프롬프트 (비우면 기본 다큐 프롬프트)"
    )

    args = parser.parse_args()
    settings = load_settings()

    if args.command == "topics":
        return _topics(settings)
    if args.command == "credits":
        return _credits(settings)
    if args.command == "cloud-check":
        return _cloud_check(settings, args.generate)
    if args.command == "photos":
        return _photos(settings, args.query, args.en)
    if args.command == "voice-test":
        return _voice_test(settings, args.text)
    if args.command == "veo-test":
        return _veo_test(settings, Path(args.image), args.prompt)
    if args.command == "review-pending":
        return _review_pending(settings, args.wait)

    try:
        result = pipeline.run(
            settings,
            render=not args.dry_run,
            use_veo=not args.no_veo,
            reuse_topic=args.reuse_topic,
            part1_only=args.part1_only,
        )
    except Exception as exc:
        # 작업 스케줄러로 무인 실행될 때는 아무도 콘솔을 안 본다. 조용히 죽으면 며칠 뒤에야
        # "왜 영상이 없지" 하고 알게 되므로, 실패 자체를 텔레그램으로 알린다.
        logger.exception("회차 생성 중 예외로 중단됐습니다")
        _notify_failure(settings, f"예외로 중단: {type(exc).__name__}: {exc}")
        return 1

    if result is None:
        _notify_failure(settings, "주제를 못 고르거나 사진을 못 구해 중단됐습니다 (logs/ 확인)")
        return 1

    logger.info(f"산출물: {result.work_dir}")
    for part in result.parts:
        logger.info(f"{part.part}부 {part.duration:.1f}초 · {part.metadata.title}")
    if result.parts:
        logger.info(result.cost_note)

    if args.review and result.parts:
        # 영상은 다 나왔는데 텔레그램 전송 하나로 회차 전체를 실패로 만들 이유가 없다.
        # 실패 사유는 telegram_bot 이 응답의 description 으로 이미 찍는다.
        try:
            pipeline.review_and_publish(settings, result, args.wait)
        except Exception as exc:
            logger.warning(f"검수 전송에 실패했습니다 (영상은 정상 생성됨): {exc}")

    return 0


def _credits(settings) -> int:
    """지금까지 쓴 추정 금액과 남은 크레딧. 실제 청구액은 GCP 콘솔이 정답이다."""
    guard = pipeline.build_spend_guard(settings)
    logger.info(guard.summary())

    vertex, auth = pipeline.build_vertex(settings)
    if vertex is None:
        logger.warning(
            "Vertex 가 준비되지 않아 지금은 생성이 막힙니다 "
            "(cloud.project_id / VERTEX_SERVICE_ACCOUNT_FILE 확인)"
        )
    else:
        logger.info(f"Vertex: {vertex.project_id} / {vertex.location}")

    ledger = guard.ledger_path
    if ledger.exists():
        logger.info(f"원장: {ledger}")
    return 0


def _cloud_check(settings, generate: bool) -> int:
    """Vertex 를 쓸 준비가 됐는지 단계별로 점검한다.

    자격증명은 이 PC 밖으로 나갈 이유가 없다 — 그래서 점검도 여기서 끝나게 만들었다.
    막히는 지점이 있으면 무엇을 고쳐야 하는지까지 같이 찍는다.
    """
    project_id = settings.get("cloud.project_id", "") or pipeline.project_from_env()
    if not project_id:
        logger.error(
            "1) 프로젝트 ID 없음 → config/config.yaml 의 cloud.project_id 또는 "
            "환경변수 VERTEX_PROJECT_ID 를 채우세요"
        )
        return 1
    logger.info(f"1) 프로젝트: {project_id}")

    vertex, auth = pipeline.build_vertex(settings)
    if vertex is None or auth is None:
        logger.error(
            "2) 인증 실패 → 서비스 계정 키 파일 경로(VERTEX_SERVICE_ACCOUNT_FILE)를 "
            "확인하거나 `gcloud auth application-default login` 을 하세요"
        )
        return 1

    try:
        token = auth.token()
    except Exception as exc:
        logger.error(f"2) 토큰 발급 실패: {exc}")
        return 1
    logger.info(f"2) 토큰 발급 성공 (앞 8자리 {token[:8]}…, 이 값은 저장하지 않습니다)")

    guard = pipeline.build_spend_guard(settings)
    logger.info(f"3) {guard.summary()}")

    if not generate:
        logger.info(
            "4) 실제 생성은 건너뜁니다. 끝까지 확인하려면 "
            "`python main.py cloud-check --generate` (약 30원)"
        )
        return 0

    # 대본이 먼저다. 이게 막히면 22분짜리 렌더가 템플릿 대본으로 나간다 — 지난 회차가
    # 정확히 그렇게 됐다. 토큰 몇 개짜리 호출이라 비용은 사실상 없다.
    logger.info("4) 대본 모델을 한 번 찔러봅니다…")
    provider = pipeline.build_llm_provider(settings)
    if provider is None:
        logger.error("4) 대본 공급자를 못 만들었습니다 → config script.llm.provider 를 보세요")
        return 1
    probe = provider.complete_json(
        '{"ok": true} 라는 JSON 만 그대로 돌려줘.', 0.0
    )
    if probe is None:
        logger.error("4) 대본 모델 호출 실패 → 위 경고의 상태 코드를 보세요")
        return 1
    logger.info(f"4) 대본 모델 응답: {probe}")

    client = pipeline.build_ai_image_client(settings, guard)
    if client.backend != "vertex":
        logger.error("5) Vertex 경로가 아닙니다 → config cloud.backend 를 vertex 로 두세요")
        return 1

    logger.info("5) 이미지 1장을 실제로 만들어 봅니다…")
    path = client.generate(
        "dark historical Joseon dynasty night sky over a mountain village, "
        "chiaroscuro lighting, heavy volumetric fog, eerie atmosphere, 35mm film grain, 8k"
    )
    if path is None:
        logger.error(
            "5) 생성 실패 → 위 경고를 보세요. 403 이면 서비스 계정에 "
            "roles/aiplatform.user 가 없고, 404 면 프로젝트에 Vertex AI API 가 꺼져 있습니다"
        )
        return 1

    logger.info(f"5) 성공: {path}")
    logger.info(f"6) {guard.summary()}")
    return 0


def _notify_failure(settings, reason: str) -> None:
    """실패를 텔레그램으로 알린다. 알림 자체가 실패해도 원래 오류를 덮지 않는다."""
    from src.review.telegram_bot import TelegramReviewer

    reviewer = TelegramReviewer(
        settings.secrets.telegram_bot_token, settings.secrets.telegram_chat_id
    )
    if not reviewer.enabled:
        return
    try:
        reviewer.notify(f"⚠️ 오늘 회차를 만들지 못했습니다\n{reason}")
    except Exception as exc:
        logger.warning(f"실패 알림 전송도 실패했습니다: {exc}")


def _topics(settings) -> int:
    candidate = pipeline.select_topic(settings)
    if candidate is None:
        logger.error("후보가 없습니다")
        return 1
    logger.info(
        f"{candidate.headline} | {candidate.headline_number} | score={candidate.score:.3f}"
    )
    logger.info(candidate.context)
    return 0


def _photos(settings, query_ko: str, query_en: str) -> int:
    """렌더를 4분 돌리기 전에, 어떤 사진이 실제로 오는지 먼저 눈으로 본다."""
    collector = pipeline.build_collector(settings)
    found = collector.collect(query_ko, query_en)
    if found is None:
        logger.error(
            f"'{query_ko}' 사진을 어떤 소스에서도 찾지 못했습니다. "
            "검색어를 더 구체적인 지명·시설명으로 바꿔보세요."
        )
        return 1

    path, credit = found
    out_path = OUTPUT_DIR / "photo_preview.jpg"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(path.read_bytes())

    logger.info(f"출처: {credit.source_name} ({credit.license_name or '라이선스 미표기'})")
    if credit.page_url:
        logger.info(f"원본: {credit.page_url}")
    logger.info(f"미리보기: {out_path}")
    return 0


def _voice_test(settings, text: str) -> int:
    """목소리는 코드로 판단할 수 없다. 엔진별로 뽑아 귀로 비교한다."""
    from src.text_ko import read_aloud

    spoken = read_aloud(text)
    logger.info(f"자막용: {text}")
    logger.info(f"낭독용: {spoken}")

    out_dir = OUTPUT_DIR / "voice_test"
    out_dir.mkdir(parents=True, exist_ok=True)

    made = 0
    for engine in pipeline.build_tts_engines(settings):
        if not engine.enabled:
            logger.warning(f"{engine.name}: 키가 없어 건너뜁니다")
            continue
        out_path = out_dir / f"{engine.name}.mp3"
        if engine.synthesize(spoken, "-4%", out_path):
            logger.info(f"{engine.name}: {out_path}")
            made += 1
        else:
            logger.warning(f"{engine.name}: 합성 실패")

    return 0 if made else 1


def _veo_test(settings, image_path: Path, prompt: str) -> int:
    """클립 하나만 만들어 결과와 실제 청구액을 확인한다."""
    from src.render.motion_sources import VEO_PROMPT

    if not image_path.exists():
        logger.error(f"사진이 없습니다: {image_path}")
        return 1

    client = pipeline.build_veo_client(settings)
    if not client.enabled:
        logger.error("GEMINI_API_KEY 가 없습니다")
        return 1

    text = prompt or VEO_PROMPT.format(subject=image_path.stem)
    logger.info(f"Veo {client.tier} 로 {client.duration_seconds}초 클립 1개를 만듭니다")
    clip_path = client.animate(image_path, text)

    cost = client.budget.episode_cost(client.duration_seconds)
    logger.info(f"생성 완료: {clip_path}")
    logger.info(cost.summary() if cost.clips else "캐시 적중 — 과금 없음")
    return 0


def _review_pending(settings, wait_seconds: int) -> int:
    """`run --review` 가 응답을 못 받고 끊긴 회차들을 다시 확인한다(재렌더 없음)."""
    from src.publish.youtube_uploader import SampleDataUploadBlocked

    try:
        results = pipeline.resume_pending_reviews(settings, wait_seconds)
    except SampleDataUploadBlocked as exc:
        logger.warning(f"샘플 데이터라 업로드가 차단됐습니다: {exc}")
        return 1

    if not results:
        return 0

    failed = 0
    for task_id, video_ids in results.items():
        if video_ids:
            logger.info(f"{task_id}: 업로드 완료 ({len(video_ids)}편)")
        else:
            logger.info(f"{task_id}: 업로드되지 않았습니다(거절 또는 무응답)")
            failed += 1
    return 1 if failed == len(results) else 0


if __name__ == "__main__":
    sys.exit(main())
