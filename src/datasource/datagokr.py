"""공공데이터포털(data.go.kr) 공통 클라이언트.

포털의 오픈 API 는 서비스마다 응답 래핑이 조금씩 다르지만, 대부분
``response.body.items.item`` 형태의 JSON 을 돌려준다. 이 클라이언트는 그 껍데기를
벗겨 레코드 리스트만 넘겨준다.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import requests
from loguru import logger

BASE_URL = "https://apis.data.go.kr"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2


class DataGoKrError(RuntimeError):
    pass


class QuotaExceededError(DataGoKrError):
    """일일 트래픽 초과. 호출자는 폴백 경로로 넘어가야 한다."""


class DataGoKrClient:
    def __init__(self, service_key: str, timeout: int = 20):
        self.service_key = service_key
        self.timeout = timeout
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.service_key)

    def get(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.enabled:
            raise DataGoKrError("DATA_GO_KR_SERVICE_KEY 가 설정되지 않았습니다")

        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        query = {
            "serviceKey": self.service_key,
            "type": "json",
            "_type": "json",
            **params,
        }

        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.session.get(url, params=query, timeout=self.timeout)
                response.raise_for_status()
                return _extract_items(response.text)
            except QuotaExceededError:
                raise
            except Exception as exc:  # 네트워크/파싱 오류 모두 재시도 대상
                last_error = exc
                if attempt < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_SECONDS * attempt)
                    logger.warning(f"data.go.kr 재시도 {attempt}/{MAX_ATTEMPTS}: {exc}")

        raise DataGoKrError(f"{url} 호출 실패: {last_error}")

    def paginate(
        self, path: str, params: dict[str, Any], page_size: int = 100, max_pages: int = 5
    ) -> Iterator[dict[str, Any]]:
        for page in range(1, max_pages + 1):
            items = self.get(path, {**params, "pageNo": page, "numOfRows": page_size})
            if not items:
                return
            yield from items
            if len(items) < page_size:
                return


def _extract_items(payload: str) -> list[dict[str, Any]]:
    import json

    # 트래픽 초과 등의 오류는 JSON 이 아니라 XML 로 내려오는 경우가 많다.
    if payload.lstrip().startswith("<"):
        if "LIMITED_NUMBER_OF_SERVICE_REQUESTS" in payload or "22" in _result_code(payload):
            raise QuotaExceededError("일일 호출 한도를 초과했습니다")
        raise DataGoKrError(f"예상치 못한 XML 응답: {payload[:200]}")

    data = json.loads(payload)
    body = data.get("response", {}).get("body", data.get("body", data))
    items = body.get("items", body.get("item", []))
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return items or []


def _result_code(xml_payload: str) -> str:
    start = xml_payload.find("<resultCode>")
    if start == -1:
        return ""
    end = xml_payload.find("</resultCode>", start)
    return xml_payload[start + len("<resultCode>") : end].strip()
