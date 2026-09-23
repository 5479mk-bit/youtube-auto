"""Vertex AI(구글 클라우드) 호출에 필요한 인증·엔드포인트.

AI Studio 키(`generativelanguage.googleapis.com`)는 **선불 잔액**으로 과금되고,
GCP 콘솔에 보이는 크레딧(₩354,417)은 **Cloud Billing 계정**에 붙어 있다. 둘은 다른
지갑이라, 크레딧을 쓰려면 같은 모델을 Vertex(`aiplatform.googleapis.com`)로 불러야 한다.

인증은 서비스 계정 키 파일 또는 ADC(gcloud 로그인)로 한다.
    VERTEX_SERVICE_ACCOUNT_FILE=credentials/vertex-sa.json
    (또는 GOOGLE_APPLICATION_CREDENTIALS, 또는 `gcloud auth application-default login`)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class VertexAuthError(RuntimeError):
    pass


@dataclass
class VertexConfig:
    project_id: str
    location: str = "us-central1"

    @property
    def base_url(self) -> str:
        return self.base_url_at(self.location)

    def base_url_at(self, location: str) -> str:
        # "global" 위치는 지역 접두사 없이 aiplatform.googleapis.com 으로 바로 간다.
        # 새로 나온 멀티모달 모델 일부(예: Gemini 이미지 생성)가 리전 엔드포인트에는
        # 없고 global 에만 있는 경우가 있다 — 콘솔의 코드 샘플로 확인된 사실이다.
        host = (
            "https://aiplatform.googleapis.com/v1"
            if location == "global"
            else f"https://{location}-aiplatform.googleapis.com/v1"
        )
        return f"{host}/projects/{self.project_id}/locations/{location}/publishers/google/models"

    def model_url(self, model: str, method: str, location: str | None = None) -> str:
        base = self.base_url_at(location) if location else self.base_url
        return f"{base}/{model}:{method}"


class VertexAuth:
    """액세스 토큰을 만들어 들고 있는다. 만료되면 알아서 새로 받는다."""

    def __init__(self, credentials_file: str = ""):
        self._explicit_file = credentials_file
        self._credentials = None

    @property
    def available(self) -> bool:
        try:
            self._load()
            return True
        except Exception:
            return False

    def token(self) -> str:
        credentials = self._load()
        if not credentials.valid:
            from google.auth.transport.requests import Request

            credentials.refresh(Request())
        return credentials.token

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"}

    def _load(self):
        if self._credentials is not None:
            return self._credentials

        path = self._explicit_file or os.getenv("VERTEX_SERVICE_ACCOUNT_FILE", "")
        if path and Path(path).exists():
            from google.oauth2 import service_account

            self._credentials = service_account.Credentials.from_service_account_file(
                path, scopes=[SCOPE]
            )
            logger.info(f"[vertex] 서비스 계정으로 인증합니다 ({Path(path).name})")
            return self._credentials

        # 파일이 없으면 ADC(gcloud 로그인, 메타데이터 서버 등)를 찾는다.
        import google.auth

        self._credentials, project = google.auth.default(scopes=[SCOPE])
        logger.info(f"[vertex] 기본 사용자 인증 정보로 인증합니다 (project={project})")
        return self._credentials


def project_from_env() -> str:
    return os.getenv("VERTEX_PROJECT_ID", "") or os.getenv("GOOGLE_CLOUD_PROJECT", "")
