"""틱톡 Content Posting API — 초안함(Inbox) 업로드.

심사(audit) 안 받은 앱의 "다이렉트 포스트"(바로 게시, `video/init/`)는 대상
계정이 비공개 계정일 때만 허용된다(`unaudited_client_can_only_post_to_private_accounts`,
실제로 겪은 오류) — 사용자가 계정을 공개로 유지하길 원해서 이 경로는 못 쓴다.
대신 **초안함 업로드**(`inbox/video/init/`)를 쓴다 — 이건 "게시"가 아니라 영상을
그 계정의 틱톡 앱 인박스에 넣어주기만 하고, 실제 게시는 사람이 앱에서 직접 눌러야
한다 — 그래서 계정 공개/비공개와 무관하게 심사 전 앱도 쓸 수 있다.

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
_INBOX_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
# 2026-10-30: 파일 전송은 성공(200)인데 앱 초안함에 이틀 연속 안 보였다 — 전송 뒤 틱톡이 영상을
# 실제로 처리해 인박스로 보냈는지(SEND_TO_USER_INBOX) 아니면 실패했는지(FAILED + 사유)를 확인한다.
_STATUS_POLL_SECONDS = 10
_STATUS_POLL_TRIES = 30
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
    """영상을 그 계정의 틱톡 앱 인박스(초안함)에 올리고 publish_id 를 돌려준다.

    `caption`/`privacy_level` 은 인박스 업로드 API에는 안 쓰인다(게시 메타데이터는
    사람이 앱에서 직접 입력한다) — 기존 호출부와 시그니처를 맞추려고만 남겨둔다.
    """
    client._refresh()
    size = video_path.stat().st_size
    response = requests.post(
        _INBOX_INIT_URL,
        headers={
            "Authorization": f"Bearer {client.access_token}",
            "Content-Type": "application/json",
        },
        json={
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

    status = wait_for_inbox(client, publish_id)
    logger.info(f"[tiktok] 초안함 전달 확인({status}) — 틱톡 앱 알림에서 게시: {publish_id}")
    return publish_id


def wait_for_inbox(client: TikTokClient, publish_id: str, sleep=None) -> str:
    """틱톡이 처리를 끝내고 인박스로 보냈는지 확인한다. 실패면 사유와 함께 예외."""
    import time

    sleep = sleep or time.sleep
    last = ""
    for _ in range(_STATUS_POLL_TRIES):
        response = requests.post(
            _STATUS_URL,
            headers={
                "Authorization": f"Bearer {client.access_token}",
                "Content-Type": "application/json",
            },
            json={"publish_id": publish_id},
            timeout=30,
        )
        if response.status_code >= 300:
            raise TikTokUploadError(f"상태 조회 실패: {response.status_code} {response.text[:300]}")
        body = response.json()
        data = body.get("data") or {}
        last = str(data.get("status", ""))
        if last in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            return last
        if last == "FAILED":
            raise TikTokUploadError(
                f"틱톡 처리 실패: {data.get('fail_reason') or body.get('error')}"
            )
        sleep(_STATUS_POLL_SECONDS)
    raise TikTokUploadError(f"틱톡 처리 확인 시간 초과(마지막 상태 {last or '없음'})")
