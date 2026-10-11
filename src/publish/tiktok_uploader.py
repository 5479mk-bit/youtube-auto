"""틱톡 Content Posting API — 비공개(SELF_ONLY) 초안 업로드.

앱이 심사(audit) 승인을 받기 전까지는 공개 게시가 거부된다. 그래서
`privacy_level` 은 설정값(`publish.tiktok.privacy_level`)으로 빼두되 기본은
`SELF_ONLY`로 둔다 — 심사가 승인되면 설정값 한 줄만 바꾸면 된다.

액세스 토큰은 24시간, 리프레시 토큰은 쓸 때마다 새로 발급된다. GitHub Secrets 는
워크플로가 못 고쳐 쓰므로, 리프레시 토큰은 `platform_tokens` 캐시 파일에 저장하고
Secrets(`TIKTOK_REFRESH_TOKEN_SEED`)는 최초 1회 시드로만 쓴다.
"""

from __future__ import annotations

from pathlib import Path

import requests
from loguru import logger

from ..settings import Settings
from . import platform_tokens

_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
_REFRESH_TOKEN_CACHE_KEY = "tiktok_refresh_token"


class TikTokUploadError(RuntimeError):
    pass


class TikTokClient:
    def __init__(self, client_key: str, client_secret: str, refresh_token: str):
        self.client_key = client_key
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token = ""

    def _refresh(self) -> None:
        response = requests.post(
            _TOKEN_URL,
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if response.status_code >= 300:
            raise TikTokUploadError(f"토큰 갱신 실패: {response.status_code} {response.text[:300]}")
        data = response.json()
        self.access_token = data["access_token"]
        # 리프레시 토큰은 쓸 때마다 새로 발급된다 — 다음 실행에 넘기려면 바로 캐시에 쓴다.
        new_refresh = data.get("refresh_token", "")
        if new_refresh:
            self.refresh_token = new_refresh
            platform_tokens.set(_REFRESH_TOKEN_CACHE_KEY, new_refresh)


def get_client(settings: Settings) -> TikTokClient | None:
    client_key = settings.secrets.tiktok_client_key
    client_secret = settings.secrets.tiktok_client_secret
    if not client_key or not client_secret:
        return None
    # 캐시된 최신 리프레시 토큰이 있으면 그걸 우선 쓰고, 없으면(최초 실행) 시드 값을 쓴다.
    refresh_token = platform_tokens.get(
        _REFRESH_TOKEN_CACHE_KEY, settings.secrets.tiktok_refresh_token_seed
    )
    if not refresh_token:
        return None
    return TikTokClient(client_key, client_secret, refresh_token)


def upload(client: TikTokClient, video_path: Path, caption: str, privacy_level: str = "SELF_ONLY") -> str:
    """영상을 올리고 publish_id 를 돌려준다. 심사 승인 전엔 `privacy_level` 을
    SELF_ONLY 이외의 값으로 바꾸면 틱톡 쪽에서 거부한다."""
    client._refresh()
    size = video_path.stat().st_size
    response = requests.post(
        _INIT_URL,
        headers={
            "Authorization": f"Bearer {client.access_token}",
            "Content-Type": "application/json",
        },
        json={
            "post_info": {
                "title": caption,
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            },
        },
        timeout=30,
    )
    if response.status_code >= 300:
        raise TikTokUploadError(f"업로드 초기화 실패: {response.status_code} {response.text[:300]}")
    data = response.json()["data"]
    upload_url = data["upload_url"]
    publish_id = data["publish_id"]

    with open(video_path, "rb") as handle:
        put_response = requests.put(
            upload_url,
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes 0-{size - 1}/{size}",
            },
            data=handle.read(),
            timeout=120,
        )
    if put_response.status_code >= 300:
        raise TikTokUploadError(f"영상 업로드 실패: {put_response.status_code}")

    logger.info(f"[tiktok] 업로드 완료(비공개 초안): {publish_id}")
    return publish_id
