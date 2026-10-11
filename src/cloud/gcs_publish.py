"""인스타그램 Graph API용 임시 공개 호스팅.

인스타그램은 영상을 바이트로 안 받고 "공개 HTTPS URL"을 서버가 가져간다
(container 생성 시 video_url). 이미 쓰는 GCP 프로젝트의 Cloud Storage 공개
버킷에 렌더된 mp4를 잠깐 올려 그 URL을 넘기고, 인스타그램이 가져간 뒤 바로
지운다 — 버킷에는 보조로 24시간 Lifecycle 삭제 규칙을 걸어두는 걸 권장한다
(README 참고, 코드로는 강제하지 않는다).

인증은 `VertexAuth`(cloud-platform 스코프)를 그대로 재사용한다 — GCS JSON API도
같은 스코프로 커버되므로 별도 서비스 계정이 필요 없다.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import requests
from loguru import logger

from .vertex import VertexAuth

_UPLOAD_URL = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
_PUBLIC_URL = "https://storage.googleapis.com/{bucket}/{name}"
_DELETE_URL = "https://storage.googleapis.com/storage/v1/b/{bucket}/o/{name}"


class GcsPublishError(RuntimeError):
    pass


def publish_temp(
    video_path: Path, bucket: str, auth: VertexAuth, object_name: str = ""
) -> tuple[str, str]:
    """mp4 를 공개 버킷에 올리고 (공개 URL, 객체 이름) 을 돌려준다.

    객체 이름은 업로드가 끝난 뒤 `delete_temp()` 로 지울 때 필요하다.
    """
    if not bucket:
        raise GcsPublishError("GCS_PUBLIC_BUCKET 이 설정되지 않았습니다")

    name = object_name or f"ig-temp/{video_path.stem}.mp4"
    headers = auth.headers()
    headers["Content-Type"] = "video/mp4"
    response = requests.post(
        _UPLOAD_URL.format(bucket=bucket),
        params={"uploadType": "media", "name": name, "predefinedAcl": "publicRead"},
        headers=headers,
        data=video_path.read_bytes(),
        timeout=120,
    )
    if response.status_code >= 300:
        raise GcsPublishError(f"GCS 업로드 실패: {response.status_code} {response.text[:300]}")

    url = _PUBLIC_URL.format(bucket=bucket, name=name)
    logger.info(f"[gcs] 인스타그램용 임시 공개 업로드: {url}")
    return url, name


def delete_temp(bucket: str, object_name: str, auth: VertexAuth) -> None:
    """인스타그램이 가져간 뒤 바로 지운다 — 실패해도 무시한다(Lifecycle 규칙이 안전망)."""
    try:
        requests.delete(
            _DELETE_URL.format(bucket=bucket, name=quote(object_name, safe="")),
            headers=auth.headers(),
            timeout=30,
        )
    except Exception:
        logger.warning(f"[gcs] 임시 업로드({object_name}) 삭제 실패 — Lifecycle 규칙이 대신 지웁니다")
