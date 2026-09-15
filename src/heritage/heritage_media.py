"""국가유산청 공식 이미지·동영상 — 항목 식별자(ccbaKdcd/ccbaAsno/ccbaCtcd)로 조회한다.

목록/상세 API(`heritage_source.py`)는 이름으로 검색하지만, 이미지·동영상·나레이션 API는
그렇지 않다. 반드시 (종목코드, 지정번호, 시도코드) 조합이 있어야 조회된다. 이 조합은
`heritage_source.py`가 목록 API 응답에서 받아 `StoryCandidate.heritage_ccba_*` 에 채워둔다.

나레이션(SearchVoiceOpenapi.do)은 이번 단계에서 다루지 않는다 — 정보 전달용 해설 낭독이라
지금 대본 말투와 안 맞을 수 있어 따로 검토하기로 했다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests
from loguru import logger

from ..datasource.datagokr import DataGoKrClient, DataGoKrError
from ..visuals.photo_sources.base import MIN_WIDTH, PhotoResult

IMAGE_ENDPOINT = "http://www.khs.go.kr/cha/SearchImageOpenapi.do"
VIDEO_ENDPOINT = "http://www.khs.go.kr/cha/SearchVideoOpenapi.do"
SOURCE_NAME = "국가유산청 국가문화유산포털"
SOURCE_URL = "https://www.heritage.go.kr"
LICENSE_NAME = "공공누리 제1유형"
DOWNLOAD_TIMEOUT = 60
# 너무 짧은 영상은 도입부(보통 3~8초)를 못 채운다. 해상도는 0(정보 없음)이면 걸러내지 않는다
# — 실제 응답을 봐야 알 수 있어 일단 통과시키고 크롭 단계에서 가운데를 잘라 쓴다.
MIN_DURATION_SECONDS = 3.0


@dataclass
class HeritageVideo:
    url: str
    width: int = 0
    height: int = 0
    duration_seconds: float = 0.0


def _identifiers_ok(kdcd: str, asno: str, ctcd: str) -> bool:
    return bool(kdcd and asno and ctcd)


def fetch_images(
    client: DataGoKrClient, kdcd: str, asno: str, ctcd: str, limit: int = 10
) -> list[PhotoResult]:
    """항목에 공식 매핑된 이미지 목록. 식별자가 없으면 아예 호출하지 않는다."""
    if not client.enabled or not _identifiers_ok(kdcd, asno, ctcd):
        return []

    try:
        rows = client.get(
            IMAGE_ENDPOINT, {"ccbaKdcd": kdcd, "ccbaAsno": asno, "ccbaCtcd": ctcd}
        )
    except DataGoKrError as exc:
        logger.warning(f"[heritage_media] 이미지 조회 실패({kdcd}/{asno}/{ctcd}): {exc}")
        return []

    results: list[PhotoResult] = []
    for row in rows:
        url = str(row.get("imageUrl") or row.get("ccimgUrl") or "").strip()
        if not url.startswith("http"):
            continue
        results.append(
            PhotoResult(
                url=url,
                source_name=SOURCE_NAME,
                license_name=LICENSE_NAME,
                page_url=SOURCE_URL,
                author=str(row.get("ccbaMnm1") or "").strip(),
                width=MIN_WIDTH,
            )
        )
        if len(results) >= limit:
            break
    return results


def fetch_videos(client: DataGoKrClient, kdcd: str, asno: str, ctcd: str) -> list[HeritageVideo]:
    """항목에 공식 매핑된 동영상 목록. 화면비·해상도는 응답을 실제로 봐야 확정된다."""
    if not client.enabled or not _identifiers_ok(kdcd, asno, ctcd):
        return []

    try:
        rows = client.get(
            VIDEO_ENDPOINT, {"ccbaKdcd": kdcd, "ccbaAsno": asno, "ccbaCtcd": ctcd}
        )
    except DataGoKrError as exc:
        logger.warning(f"[heritage_media] 동영상 조회 실패({kdcd}/{asno}/{ctcd}): {exc}")
        return []

    videos: list[HeritageVideo] = []
    for row in rows:
        url = str(row.get("videoUrl") or row.get("ccbaVdUrl") or "").strip()
        if not url.startswith("http"):
            continue
        videos.append(
            HeritageVideo(
                url=url,
                width=_to_int(row.get("width")),
                height=_to_int(row.get("height")),
                duration_seconds=_to_float(row.get("playTime") or row.get("duration")),
            )
        )
    return videos


def select_video(videos: list[HeritageVideo]) -> HeritageVideo | None:
    """도입부에 쓸 영상 하나를 고른다. 길이 정보가 있으면 너무 짧은 건 거른다."""
    usable = [v for v in videos if v.duration_seconds == 0 or v.duration_seconds >= MIN_DURATION_SECONDS]
    if not usable:
        return None
    # 해상도가 큰 걸 우선한다 — 세로로 크롭해도 화질이 덜 깨진다.
    return max(usable, key=lambda v: v.width * v.height)


def download_video(video: HeritageVideo, dest_path: Path) -> Path | None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(video.url, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        dest_path.write_bytes(response.content)
        return dest_path
    except Exception as exc:
        logger.warning(f"[heritage_media] 동영상 다운로드 실패({video.url[:60]}): {exc}")
        dest_path.unlink(missing_ok=True)
        return None


def _to_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _to_float(value: object) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0
