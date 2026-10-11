"""설정 로딩. config.yaml(파이프라인 값)과 .env(비밀값)를 분리해서 읽는다."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"
RESOURCE_DIR = PROJECT_ROOT / "resource"
CACHE_DIR = PROJECT_ROOT / ".cache"
STATE_DB = PROJECT_ROOT / "state.sqlite3"


@dataclass(frozen=True)
class Secrets:
    data_go_kr_service_key: str
    gemini_api_key: str
    # 백업 키를 포함한 전체 목록. Gemini 무료 한도는 키(프로젝트)마다 따로 계산되므로
    # 하나가 하루 한도를 다 쓰면 다음 키로 넘어간다.
    gemini_api_keys: list[str]
    openai_api_key: str
    openai_base_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    youtube_client_secret_file: str
    youtube_token_file: str
    # Vertex AI(구글 클라우드) 인증용 서비스 계정 키 파일. 없으면 ADC 를 찾는다.
    vertex_service_account_file: str = ""
    # 틱톡 Content Posting API. 리프레시 토큰은 쓸 때마다 새로 발급되므로 이 값은
    # "최초 시드"일 뿐이고, 실제 최신 값은 캐시 파일(.cache/platform_tokens.json)
    # 에서 우선 읽는다(tiktok_uploader._load_refresh_token 참고).
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_refresh_token_seed: str = ""
    # 인스타그램(릴스) Graph API. 장기 토큰도 같은 이유로 캐시 파일을 우선 읽는다.
    meta_app_id: str = ""
    meta_app_secret: str = ""
    instagram_access_token: str = ""
    instagram_business_account_id: str = ""
    gcs_public_bucket: str = ""


class Settings:
    def __init__(self, config: dict[str, Any], secrets: Secrets):
        self.raw = config
        self.secrets = secrets

    def __getitem__(self, section: str) -> dict[str, Any]:
        return self.raw[section]

    def get(self, dotted_key: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def load_settings(config_path: Path | None = None) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")

    path = config_path or CONFIG_DIR / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))

    # config.local.yaml 이 있으면 최상위 섹션 단위로 덮어쓴다.
    local_path = CONFIG_DIR / "config.local.yaml"
    if local_path.exists():
        local = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
        for section, value in local.items():
            if isinstance(value, dict) and isinstance(config.get(section), dict):
                config[section].update(value)
            else:
                config[section] = value

    primary_key = os.getenv("GEMINI_API_KEY", "")
    backup_keys = [
        key.strip()
        for key in os.getenv("GEMINI_API_KEY_BACKUP", "").split(",")
        if key.strip()
    ]

    secrets = Secrets(
        data_go_kr_service_key=os.getenv("DATA_GO_KR_SERVICE_KEY", ""),
        gemini_api_key=primary_key,
        gemini_api_keys=[key for key in [primary_key, *backup_keys] if key],
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_base_url=os.getenv("OPENAI_BASE_URL", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        youtube_client_secret_file=os.getenv(
            "YOUTUBE_CLIENT_SECRET_FILE", "credentials/youtube_client_secret.json"
        ),
        youtube_token_file=os.getenv(
            "YOUTUBE_TOKEN_FILE", "credentials/youtube_token.json"
        ),
        vertex_service_account_file=os.getenv("VERTEX_SERVICE_ACCOUNT_FILE", ""),
        tiktok_client_key=os.getenv("TIKTOK_CLIENT_KEY", ""),
        tiktok_client_secret=os.getenv("TIKTOK_CLIENT_SECRET", ""),
        tiktok_refresh_token_seed=os.getenv("TIKTOK_REFRESH_TOKEN_SEED", ""),
        meta_app_id=os.getenv("META_APP_ID", ""),
        meta_app_secret=os.getenv("META_APP_SECRET", ""),
        instagram_access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN", ""),
        instagram_business_account_id=os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", ""),
        gcs_public_bucket=os.getenv("GCS_PUBLIC_BUCKET", ""),
    )
    return Settings(config, secrets)
