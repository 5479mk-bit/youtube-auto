"""인스타그램 Graph API — 릴스(Reels) 업로드.

바이트를 직접 안 받고 "공개 HTTPS URL"을 서버가 가져가는 2단계(container→publish)
방식이다. `src/cloud/gcs_publish.py` 로 렌더된 mp4를 잠깐 공개 버킷에 올려 그
URL을 넘기고, 인스타그램이 가져간 뒤 바로 지운다.

장기 액세스 토큰은 보통 60일 만료다 — 만료 10일 전부터 자동 갱신하고,
`platform_tokens` 캐시 파일에 최신 값을 저장한다(Secrets 는 최초 시드로만 쓴다).
"""

from __future__ import annotations

import time
from pathlib import Path

import requests
from loguru import logger

from ..cloud.gcs_publish import delete_temp, publish_temp
from ..cloud.vertex import VertexAuth
from ..settings import Settings
from . import platform_tokens

_GRAPH_BASE = "https://graph.facebook.com/v21.0"
_ACCESS_TOKEN_CACHE_KEY = "instagram_access_token"
_POLL_INTERVAL_SECONDS = 5
_POLL_MAX_ATTEMPTS = 24  # 최대 2분 대기


class InstagramUploadError(RuntimeError):
    pass


class InstagramClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        access_token: str,
        business_account_id: str,
        gcs_bucket: str,
        gcs_auth: VertexAuth,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.access_token = access_token
        self.business_account_id = business_account_id
        self.gcs_bucket = gcs_bucket
        self.gcs_auth = gcs_auth

    def _refresh_token_if_needed(self) -> None:
        # 장기 토큰 만료 시각은 별도 조회가 필요해 매번 확인하기보단, 그냥 매 실행마다
        # 한 번 갱신을 시도한다 — 갱신은 멱등이고(아직 유효해도 새 토큰을 또 내준다),
        # 실패해도(예: 이미 90일 전에 만료) 기존 토큰으로 계속 시도해본다.
        response = requests.get(
            f"{_GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "fb_exchange_token": self.access_token,
            },
            timeout=30,
        )
        if response.status_code >= 300:
            logger.warning(f"[instagram] 토큰 갱신 실패(기존 토큰으로 계속 시도): {response.text[:200]}")
            return
        new_token = response.json().get("access_token", "")
        if new_token:
            self.access_token = new_token
            platform_tokens.set(_ACCESS_TOKEN_CACHE_KEY, new_token)


def get_client(settings: Settings) -> InstagramClient | None:
    app_id = settings.secrets.meta_app_id
    app_secret = settings.secrets.meta_app_secret
    business_account_id = settings.secrets.instagram_business_account_id
    access_token = platform_tokens.get(
        _ACCESS_TOKEN_CACHE_KEY, settings.secrets.instagram_access_token
    )
    gcs_bucket = settings.secrets.gcs_public_bucket
    if not (app_id and app_secret and business_account_id and access_token and gcs_bucket):
        return None
    gcs_auth = VertexAuth(settings.secrets.vertex_service_account_file)
    return InstagramClient(app_id, app_secret, access_token, business_account_id, gcs_bucket, gcs_auth)


def upload(client: InstagramClient, video_path: Path, caption: str) -> str:
    client._refresh_token_if_needed()
    public_url, object_name = publish_temp(video_path, client.gcs_bucket, client.gcs_auth)
    try:
        media_id = _create_and_publish(client, public_url, caption)
    finally:
        delete_temp(client.gcs_bucket, object_name, client.gcs_auth)
    return media_id


def _create_and_publish(client: InstagramClient, video_url: str, caption: str) -> str:
    create_response = requests.post(
        f"{_GRAPH_BASE}/{client.business_account_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": client.access_token,
        },
        timeout=30,
    )
    if create_response.status_code >= 300:
        raise InstagramUploadError(
            f"컨테이너 생성 실패: {create_response.status_code} {create_response.text[:300]}"
        )
    container_id = create_response.json()["id"]

    for _ in range(_POLL_MAX_ATTEMPTS):
        status_response = requests.get(
            f"{_GRAPH_BASE}/{container_id}",
            params={"fields": "status_code", "access_token": client.access_token},
            timeout=30,
        )
        status_code = status_response.json().get("status_code", "")
        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            raise InstagramUploadError(f"컨테이너 처리 실패: {status_response.text[:300]}")
        time.sleep(_POLL_INTERVAL_SECONDS)
    else:
        raise InstagramUploadError("컨테이너가 시간 내 FINISHED 상태가 되지 않았습니다")

    publish_response = requests.post(
        f"{_GRAPH_BASE}/{client.business_account_id}/media_publish",
        data={"creation_id": container_id, "access_token": client.access_token},
        timeout=30,
    )
    if publish_response.status_code >= 300:
        raise InstagramUploadError(
            f"게시 실패: {publish_response.status_code} {publish_response.text[:300]}"
        )
    media_id = publish_response.json()["id"]
    logger.info(f"[instagram] 릴스 게시 완료: {media_id}")
    return media_id
