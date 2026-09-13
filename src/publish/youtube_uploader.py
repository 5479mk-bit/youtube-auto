"""YouTube Data API v3 업로드.

1부를 먼저 올리고, 2부 설명에 1부 링크를 넣어 두 편을 잇는다. 기본 공개 범위는
비공개이고, 공개 전환은 사람이 확인한 뒤에 한다.
"""

from __future__ import annotations

from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from loguru import logger

from ..models import VideoMetadata

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
WATCH_URL = "https://www.youtube.com/watch?v="


class SampleDataUploadBlocked(RuntimeError):
    """샘플 데이터로 만든 영상은 올리지 않는다."""


def get_service(client_secret_file: str, token_file: str):
    token_path = Path(token_file)
    credentials = None

    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
            credentials = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")

    return build("youtube", "v3", credentials=credentials)


def upload(
    service,
    video_path: Path,
    metadata: VideoMetadata,
    privacy_status: str,
    category_id: str,
    thumbnail_path: Path | None = None,
) -> str:
    body = {
        "snippet": {
            "title": metadata.title,
            "description": metadata.description,
            "tags": metadata.tags,
            "categoryId": category_id,
        },
        "status": {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": False},
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()

    video_id = response["id"]
    logger.info(f"업로드 완료: {WATCH_URL}{video_id}")

    if thumbnail_path and thumbnail_path.exists():
        try:
            service.thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))
            ).execute()
        except Exception as exc:
            # 썸네일 설정에는 채널 인증이 필요하다. 실패해도 업로드 자체는 유효하다.
            logger.warning(f"썸네일 설정 실패: {exc}")

    return video_id


def upload_series(
    service,
    parts: list[tuple[Path, VideoMetadata, Path | None]],
    privacy_status: str,
    category_id: str,
) -> list[str]:
    video_ids: list[str] = []
    for index, (video_path, metadata, thumbnail) in enumerate(parts):
        if index > 0 and video_ids:
            metadata.description = (
                f"1부 먼저 보기: {WATCH_URL}{video_ids[0]}\n\n{metadata.description}"
            )
        video_ids.append(
            upload(service, video_path, metadata, privacy_status, category_id, thumbnail)
        )
    return video_ids
