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
        "--review",
        action="store_true",
        help="렌더 후 텔레그램으로 검수 요청을 보내고 승인 시 업로드한다",
    )
    run_parser.add_argument(
        "--wait", type=int, default=1800, help="검수 응답을 기다릴 최대 시간(초)"
    )

    subparsers.add_parser("topics", help="주제 후보 순위만 확인한다")

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
            settings, render=not args.dry_run, use_veo=not args.no_veo
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
        pipeline.review_and_publish(settings, result, args.wait)

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
