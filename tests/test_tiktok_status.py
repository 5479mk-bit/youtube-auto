from unittest.mock import MagicMock, patch

import pytest

from src.publish import tiktok_uploader as tt


def _resp(status):
    r = MagicMock(status_code=200)
    r.json.return_value = {"data": status}
    return r


def test_waits_until_inbox():
    client = tt.TikTokClient("k", "s", "r")
    with patch.object(tt.requests, "post", side_effect=[_resp({"status": "PROCESSING_UPLOAD"}), _resp({"status": "SEND_TO_USER_INBOX"})]):
        assert tt.wait_for_inbox(client, "p", sleep=lambda s: None) == "SEND_TO_USER_INBOX"


def test_failure_reason_raised():
    client = tt.TikTokClient("k", "s", "r")
    with patch.object(tt.requests, "post", return_value=_resp({"status": "FAILED", "fail_reason": "file_format_check_failed"})):
        with pytest.raises(tt.TikTokUploadError, match="file_format_check_failed"):
            tt.wait_for_inbox(client, "p", sleep=lambda s: None)
