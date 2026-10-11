"""틱톡/인스타그램 자동 업로드 모듈의 네트워크 없는 단위 테스트."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.publish import instagram_uploader, platform_tokens, tiktok_uploader
from src.settings import Secrets, Settings


def _settings(**overrides) -> Settings:
    base = dict(
        data_go_kr_service_key="",
        gemini_api_key="",
        gemini_api_keys=[],
        openai_api_key="",
        openai_base_url="",
        telegram_bot_token="x",
        telegram_chat_id="y",
        youtube_client_secret_file="",
        youtube_token_file="",
    )
    base.update(overrides)
    return Settings({}, Secrets(**base))


class TestTikTokGetClient:
    def test_missing_secrets_returns_none(self):
        settings = _settings()
        assert tiktok_uploader.get_client(settings) is None

    def test_missing_refresh_token_returns_none(self, tmp_path):
        settings = _settings(tiktok_client_key="k", tiktok_client_secret="s")
        with patch.object(platform_tokens, "_TOKENS_PATH", tmp_path / "platform_tokens.json"):
            assert tiktok_uploader.get_client(settings) is None

    def test_uses_cached_refresh_token_over_seed(self, tmp_path):
        settings = _settings(
            tiktok_client_key="k",
            tiktok_client_secret="s",
            tiktok_refresh_token_seed="seed-token",
        )
        with patch.object(platform_tokens, "_TOKENS_PATH", tmp_path / "platform_tokens.json"):
            platform_tokens.set("tiktok_refresh_token", "cached-token")
            client = tiktok_uploader.get_client(settings)
        assert client is not None
        assert client.refresh_token == "cached-token"

    def test_falls_back_to_seed_when_no_cache(self, tmp_path):
        settings = _settings(
            tiktok_client_key="k",
            tiktok_client_secret="s",
            tiktok_refresh_token_seed="seed-token",
        )
        with patch.object(platform_tokens, "_TOKENS_PATH", tmp_path / "platform_tokens.json"):
            client = tiktok_uploader.get_client(settings)
        assert client is not None
        assert client.refresh_token == "seed-token"


class TestInstagramGetClient:
    def test_missing_any_required_secret_returns_none(self, tmp_path):
        settings = _settings(
            meta_app_id="id",
            meta_app_secret="secret",
            instagram_business_account_id="",  # 비어있음
            instagram_access_token="token",
            gcs_public_bucket="bucket",
        )
        with patch.object(platform_tokens, "_TOKENS_PATH", tmp_path / "platform_tokens.json"):
            assert instagram_uploader.get_client(settings) is None

    def test_all_secrets_present_returns_client(self, tmp_path):
        settings = _settings(
            meta_app_id="id",
            meta_app_secret="secret",
            instagram_business_account_id="biz",
            instagram_access_token="token",
            gcs_public_bucket="bucket",
        )
        with patch.object(platform_tokens, "_TOKENS_PATH", tmp_path / "platform_tokens.json"):
            client = instagram_uploader.get_client(settings)
        assert client is not None
        assert client.business_account_id == "biz"
        assert client.gcs_bucket == "bucket"


class TestPlatformTokensCache:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "platform_tokens.json"
        with patch.object(platform_tokens, "_TOKENS_PATH", path):
            assert platform_tokens.get("missing_key") == ""
            platform_tokens.set("a", "1")
            platform_tokens.set("b", "2")
            assert platform_tokens.get("a") == "1"
            assert platform_tokens.get("b") == "2"

    def test_corrupt_cache_file_is_ignored(self, tmp_path):
        path = tmp_path / "platform_tokens.json"
        path.write_text("not json", encoding="utf-8")
        with patch.object(platform_tokens, "_TOKENS_PATH", path):
            assert platform_tokens.get("a", "default") == "default"


class TestInstagramPublishFlow:
    def test_create_and_publish_polls_until_finished(self):
        client = MagicMock()
        client.business_account_id = "biz"
        client.access_token = "tok"

        create_response = MagicMock(status_code=200)
        create_response.json.return_value = {"id": "container-1"}
        status_response = MagicMock(status_code=200)
        status_response.json.return_value = {"status_code": "FINISHED"}
        publish_response = MagicMock(status_code=200)
        publish_response.json.return_value = {"id": "media-1"}

        with patch("src.publish.instagram_uploader.requests.post") as mock_post, patch(
            "src.publish.instagram_uploader.requests.get", return_value=status_response
        ), patch("src.publish.instagram_uploader.time.sleep"):
            mock_post.side_effect = [create_response, publish_response]
            media_id = instagram_uploader._create_and_publish(client, "https://example.com/v.mp4", "caption")

        assert media_id == "media-1"

    def test_error_status_raises(self):
        client = MagicMock()
        client.business_account_id = "biz"
        client.access_token = "tok"

        create_response = MagicMock(status_code=200)
        create_response.json.return_value = {"id": "container-1"}
        status_response = MagicMock(status_code=200)
        status_response.json.return_value = {"status_code": "ERROR"}

        with patch("src.publish.instagram_uploader.requests.post", return_value=create_response), patch(
            "src.publish.instagram_uploader.requests.get", return_value=status_response
        ), patch("src.publish.instagram_uploader.time.sleep"):
            try:
                instagram_uploader._create_and_publish(client, "https://example.com/v.mp4", "caption")
            except instagram_uploader.InstagramUploadError:
                pass
            else:
                raise AssertionError("InstagramUploadError 가 발생해야 합니다")
