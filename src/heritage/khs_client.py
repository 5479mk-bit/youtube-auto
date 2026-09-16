"""국가유산청(khs.go.kr) Open API 전용 클라이언트.

`datasource/datagokr.py`의 `DataGoKrClient`는 apis.data.go.kr 이 공유하는 JSON 래핑
규약(`response.body.items.item`)을 전제한다. khs.go.kr 는 그 포털이 아니라 국가유산청이
직접 운영하는 별도 호스트라 규약이 다르다 — 실제로 호출해 확인한 결과:

- 인증키(서비스키)가 필요 없다. `serviceKey` 파라미터 없이도 정상 응답한다.
- 응답은 항상 XML이다(`_type=json` 을 줘도 무시된다). `DataGoKrClient`로 이 호스트를
  부르면 모든 정상 응답이 "예상치 못한 XML 응답" 오류로 잘못 처리된다 — 이 클라이언트를
  만들게 된 이유다.
- 항목은 `<item>...</item>` 반복 구조로 온다(중첩 위치는 API마다 다를 수 있어, 트리 전체를
  훑어 `item` 태그를 찾는다).

실제 응답으로 확인된 예(목록 API, ccbaKdcd=11 국보 · ccbaCtcd=11 서울):
ccbaMnm1(이름) · ccbaKdcd(종목코드) · ccbaAsno(지정번호) · ccbaCtcd(시도코드) ·
ccbaCtcdNm(시도명) · ccsiName(시군구명) · ccbaAdmin(관리자) · longitude/latitude.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import requests
from loguru import logger

TIMEOUT = 20
MAX_ATTEMPTS = 2


class KhsApiError(RuntimeError):
    pass


def get(url: str, params: dict[str, Any]) -> list[dict[str, str]]:
    """항목(item) 리스트만 뽑아 돌려준다. 실패하면 KhsApiError."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return _extract_items(response.content)
        except Exception as exc:  # 네트워크/파싱 오류 모두 재시도 대상
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                logger.warning(f"khs.go.kr 재시도 {attempt}/{MAX_ATTEMPTS}: {exc}")

    raise KhsApiError(f"{url} 호출 실패: {last_error}")


def _extract_items(payload: bytes) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise KhsApiError(f"XML 파싱 실패: {exc}") from exc

    return [
        {child.tag: (child.text or "").strip() for child in item}
        for item in root.iter("item")
    ]
