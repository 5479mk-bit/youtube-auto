"""「숫자가 말해준다」 공공데이터 다큐 쇼츠 자동화 CLI."""

from __future__ import annotations

import argparse
import sys

from loguru import logger

from src import pipeline
from src.settings import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="공공데이터 쇼츠 자동 생성")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="한 회차(1부/2부)를 생성한다")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="영상 렌더 없이 주제·대본만 만든다",
    )
    run_parser.add_argument(
        "--review",
        action="store_true",
        help="렌더 후 텔레그램으로 검수 요청을 보내고 승인 시 업로드한다",
    )
    run_parser.add_argument(
        "--wait",
        type=int,
        default=1800,
        help="검수 응답을 기다릴 최대 시간(초)",
    )

    subparsers.add_parser("topics", help="주제 후보 순위만 확인한다")

    args = parser.parse_args()
    settings = load_settings()

    if args.command == "topics":
        candidate = pipeline.select_topic(settings)
        if candidate is None:
            logger.error("후보가 없습니다")
            return 1
        logger.info(f"{candidate.headline} | {candidate.headline_number} | score={candidate.score:.3f}")
        logger.info(candidate.context)
        return 0

    result = pipeline.run(settings, render=not args.dry_run)
    if result is None:
        return 1

    logger.info(f"산출물: {result.work_dir}")
    for part in result.parts:
        logger.info(f"{part.part}부 {part.duration:.1f}초 · {part.metadata.title}")

    if args.review and result.parts:
        pipeline.review_and_publish(settings, result, args.wait)

    return 0


if __name__ == "__main__":
    sys.exit(main())
