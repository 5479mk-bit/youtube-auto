"""국가유산청 공식 이미지·동영상 — 항목 식별자(ccbaKdcd/ccbaAsno/ccbaCtcd)로 조회한다.

목록/상세 API(`heritage_source.py`)와 마찬가지로 khs.go.kr 는 인증키가 필요 없다(직접
호출로 확인). 이미지·동영상·나레이션 API는 이름이 아니라 (종목코드, 지정번호, 시도코드)
조합으로만 조회된다 — 이 조합은 `heritage_source.py`가 목록 API 응답에서 받아
`StoryCandidate.heritage_ccba_*` 에 채워둔다.

나레이션(SearchVoiceOpenapi.do)은 이번 단계에서 다루지 않는다 — 정보 전달용 해설 낭독이라
지금 대본 말투와 안 맞을 수 있어 따로 검토하기로 했다.

실제 호출로 확인한 item 필드:
- 이미지: `sn`(순번), `imageNuri`(라이선스 구분으로 보이는 A/D 플래그 — 정확한 의미는
  아직 미확인, 실제 공공누리 유형표와 대조 필요), `imageUrl`, `ccimDesc`(이미지 설명).
  `ccbaMnm1` 같은 이름 필드는 item 안에 없다(응답 최상위에만 있음).
- 동영상: `sn`, `videoUrl` 뿐이다. 해상도·재생시간 필드는 응답에 아예 없다 — 화면비·길이는
  실제로 내려받아 확인하는 수밖에 없다(`render/motion_sources.py`가 다운로드 후 크롭한다).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests
from loguru import logger

from . import khs_client
from ..visuals.photo_sources.base import MIN_WIDTH, PhotoResult

IMAGE_ENDPOINT = "http://www.khs.go.kr/cha/SearchImageOpenapi.do"
VIDEO_ENDPOINT = "http://www.khs.go.kr/cha/SearchVideoOpenapi.do"
SOURCE_NAME = "국가유산청 국가문화유산포털"
SOURCE_URL = "https://www.heritage.go.kr"
LICENSE_NAME = "공공누리 제1유형"
DOWNLOAD_TIMEOUT = 60


@dataclass
class HeritageVideo:
    # 실제 응답엔 url 외엔 아무 메타데이터도 없다(해상도·재생시간 필드 자체가 없음).
    url: str


def _identifiers_ok(kdcd: str, asno: str, ctcd: str) -> bool:
    return bool(kdcd and asno and ctcd)


def fetch_images(kdcd: str, asno: str, ctcd: str, limit: int = 10) -> list[PhotoResult]:
    """항목에 공식 매핑된 이미지 목록. 식별자가 없으면 아예 호출하지 않는다."""
    if not _identifiers_ok(kdcd, asno, ctcd):
        return []

    try:
        rows = khs_client.get(
            IMAGE_ENDPOINT, {"ccbaKdcd": kdcd, "ccbaAsno": asno, "ccbaCtcd": ctcd}
        )
    except khs_client.KhsApiError as exc:
        logger.warning(f"[heritage_media] 이미지 조회 실패({kdcd}/{asno}/{ctcd}): {exc}")
        return []

    results: list[PhotoResult] = []
    for row in rows:
        url = str(row.get("imageUrl") or "").strip()
        if not url.startswith("http"):
            continue
        results.append(
            PhotoResult(
                url=url,
                source_name=SOURCE_NAME,
                license_name=LICENSE_NAME,
                page_url=SOURCE_URL,
                author=str(row.get("ccimDesc") or "").strip(),
                width=MIN_WIDTH,
            )
        )
        if len(results) >= limit:
            break
    return results


def fetch_videos(kdcd: str, asno: str, ctcd: str) -> list[HeritageVideo]:
    """항목에 공식 매핑된 동영상 목록. 화면비·해상도는 응답을 실제로 봐야 확정된다."""
    if not _identifiers_ok(kdcd, asno, ctcd):
        return []

    try:
        rows = khs_client.get(
            VIDEO_ENDPOINT, {"ccbaKdcd": kdcd, "ccbaAsno": asno, "ccbaCtcd": ctcd}
        )
    except khs_client.KhsApiError as exc:
        logger.warning(f"[heritage_media] 동영상 조회 실패({kdcd}/{asno}/{ctcd}): {exc}")
        return []

    videos: list[HeritageVideo] = []
    for row in rows:
        url = str(row.get("videoUrl") or "").strip()
        if not url.startswith("http"):
            continue
        # 실제 응답엔 sn/videoUrl 뿐이라 해상도·재생시간은 항상 0(정보 없음)이다 —
        # 적합성은 다운로드 후 render/motion_sources.py 가 실제 파일을 열어서 판단한다.
        videos.append(HeritageVideo(url=url))
    return videos


def select_video(videos: list[HeritageVideo]) -> HeritageVideo | None:
    """도입부에 쓸 영상 하나를 고른다.

    실제 응답엔 해상도·재생시간 메타데이터가 없어(url 뿐) 미리 걸러낼 수 없다 — 첫 번째
    항목을 그대로 쓰고, 부적합하면(너무 짧거나 다운로드 실패) 호출하는 쪽이 Veo/파랄랙스로
    내려간다.
    """
    return videos[0] if videos else None


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
