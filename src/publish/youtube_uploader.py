"""YouTube Data API v3 업로드.

1부를 먼저 올리고, 2부 설명에 1부 링크를 넣어 두 편을 잇는다. 기본 공개 범위는
비공개이고, 공개 전환은 사람이 확인한 뒤에 한다.

업로드마다 세 가지를 추가로 한다(사용자 지시, 2026-09-27):
- AI 생성 콘텐츠 공개 표시(`containsSyntheticMedia`) 켜기.
- 지정한 재생목록(기본 "재난아카이브")에 자동으로 넣기 — 없으면 채널에 새로 만든다.
- 설명에 채널의 기존 영상 하나를 "관련 영상"으로 링크한다. 엔드스크린/카드는 YouTube
  Data API 로 건드릴 수 없는 영역이라(공개 API 에 없다), 가장 가까운 대안으로 설명에
  링크를 남긴다.

이 세 가지는 업로드(`youtube.upload`) 권한만으로는 안 된다 — 재생목록 조회/생성과
채널의 업로드 목록 조회는 더 넓은 `youtube` 스코프가 필요하다. 기존에 `youtube.upload`
스코프로만 발급된 YOUTUBE_TOKEN_JSON 이 있다면, 그 토큰으로는 이 세 기능이 403 으로
실패한다 — 사람이 다시 로그인해서(`get_service` 를 로컬에서 한 번 더 돌려서) 토큰을
새로 발급받아 시크릿을 갱신해야 한다. 실패해도 영상 업로드 자체는 그대로 끝나게
만들어 뒀다(각자 실패해도 업로드를 막지 않는다).
"""

from __future__ import annotations

import random
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from loguru import logger

from ..models import VideoMetadata

# playlists.list/insert, search 등은 upload 스코프만으론 안 된다.
SCOPES = ["https://www.googleapis.com/auth/youtube"]
WATCH_URL = "https://www.youtube.com/watch?v="

DEFAULT_PLAYLIST_TITLE = "재난아카이브"


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
    playlist_title: str = DEFAULT_PLAYLIST_TITLE,
) -> str:
    description = _with_related_video(service, metadata.description)

    body = {
        "snippet": {
            "title": metadata.title,
            "description": description,
            "tags": metadata.tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
            # AI/합성 콘텐츠 공개 표시. 이 영상은 실제로 생성 이미지·Veo·합성 음성으로
            # 만들어지므로 항상 켠다(사용자 지시).
            "containsSyntheticMedia": True,
        },
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

    if playlist_title:
        try:
            playlist_id = _ensure_playlist(service, playlist_title)
            _add_to_playlist(service, playlist_id, video_id)
            logger.info(f"재생목록 '{playlist_title}'에 추가했습니다")
        except Exception as exc:
            # 재생목록 권한이 없는 토큰(구 스코프)일 수 있다 — 업로드 자체는 막지 않는다.
            logger.warning(f"재생목록 추가 실패({playlist_title}): {exc}")

    return video_id


def upload_series(
    service,
    parts: list[tuple[Path, VideoMetadata, Path | None]],
    privacy_status: str,
    category_id: str,
    playlist_title: str = DEFAULT_PLAYLIST_TITLE,
) -> list[str]:
    video_ids: list[str] = []
    for index, (video_path, metadata, thumbnail) in enumerate(parts):
        if index > 0 and video_ids:
            metadata.description = (
                f"1부 먼저 보기: {WATCH_URL}{video_ids[0]}\n\n{metadata.description}"
            )
        video_ids.append(
            upload(
                service, video_path, metadata, privacy_status, category_id, thumbnail,
                playlist_title=playlist_title,
            )
        )
    return video_ids


def _with_related_video(service, description: str) -> str:
    """채널에 이미 올라온 영상 중 하나를 무작위로 골라 설명에 링크한다.

    YouTube Data API 에는 엔드스크린/카드("관련 동영상")를 설정하는 공개 엔드포인트가
    없다 — 채널 소유자가 YouTube 스튜디오에서 직접 붙이는 것만 가능하다. 가장 가까운
    자동화는 설명 안에 이전 영상 링크를 남기는 것이라 그렇게 한다. 채널에 영상이
    아직 하나도 없으면(첫 업로드) 조용히 건너뛴다.
    """
    try:
        video_id = _pick_existing_video(service)
    except Exception as exc:
        logger.warning(f"관련 영상 추천 실패(건너뜀): {exc}")
        return description
    if video_id is None:
        return description
    return f"{description}\n\n▶ 같이 보면 좋은 영상: {WATCH_URL}{video_id}"


def _pick_existing_video(service) -> str | None:
    channels = service.channels().list(part="contentDetails", mine=True).execute()
    items = channels.get("items", [])
    if not items:
        return None
    uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    response = (
        service.playlistItems()
        .list(part="contentDetails", playlistId=uploads_playlist_id, maxResults=50)
        .execute()
    )
    video_ids = [item["contentDetails"]["videoId"] for item in response.get("items", [])]
    if not video_ids:
        return None
    return random.choice(video_ids)


def _ensure_playlist(service, title: str) -> str:
    """`title` 이라는 재생목록이 채널에 있으면 그 ID 를, 없으면 새로 만들어 그 ID 를
    돌려준다."""
    response = service.playlists().list(part="snippet", mine=True, maxResults=50).execute()
    for item in response.get("items", []):
        if item["snippet"]["title"] == title:
            return item["id"]

    created = (
        service.playlists()
        .insert(
            part="snippet,status",
            body={
                "snippet": {"title": title},
                "status": {"privacyStatus": "private"},
            },
        )
        .execute()
    )
    logger.info(f"재생목록 '{title}'을(를) 새로 만들었습니다")
    return created["id"]


def _add_to_playlist(service, playlist_id: str, video_id: str) -> None:
    service.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()
