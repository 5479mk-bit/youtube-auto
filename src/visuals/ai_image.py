"""장면을 생성 이미지로 만든다 (Gemini 이미지 모델).

조선 사건에는 사진이 존재하지 않는다. "하늘에 호리병 같은 게 떠올랐다"를 찍은 사진은
없고, 그래서 지금까지 텍스트 검색으로 현대 사진(우체국·카페)이 끌려 들어왔다. 이 모듈이
그 자리를 대신한다.

같은 프롬프트는 두 번 만들지 않는다 — 프롬프트 해시로 캐시에 남겨 재실행이 공짜가 되게
한다. 생성이 막히면(무료 티어는 이미지 할당량이 0 이다) None 을 돌려주고, 부르는 쪽이
대체 화면으로 내려간다. 절대 웹에서 실사 사진을 끌어오지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import requests
from loguru import logger

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash-image"
TIMEOUT = 180


class AiImageClient:
    def __init__(self, api_keys: list[str], cache_dir: Path, model: str = DEFAULT_MODEL):
        self.api_keys = [key for key in api_keys if key]
        self.model = model or DEFAULT_MODEL
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # 403(프로젝트 접근 거부)·429(할당량 0)가 뜬 키는 이번 실행 동안 다시 쓰지 않는다.
        # 컷마다 죽은 키를 또 찔러보면 렌더가 몇 분씩 길어진다.
        self._dead: set[str] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.api_keys)

    def generate(self, prompt: str, aspect_ratio: str = "9:16") -> Path | None:
        """프롬프트 하나로 이미지 한 장. 캐시에 있으면 호출하지 않는다."""
        if not prompt.strip():
            return None

        cached = self._cache_path(prompt, aspect_ratio)
        if cached.exists():
            return cached
        if not self.enabled:
            return None

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": aspect_ratio},
            },
        }

        for key in self.api_keys:
            if key in self._dead:
                continue
            try:
                response = requests.post(
                    f"{BASE_URL}/{self.model}:generateContent",
                    headers={"x-goog-api-key": key},
                    json=payload,
                    timeout=TIMEOUT,
                )
            except Exception as exc:
                logger.warning(f"[ai_image] 호출 실패: {exc}")
                continue

            if response.status_code in (403, 429):
                self._dead.add(key)
                continue
            if response.status_code != 200:
                logger.warning(f"[ai_image] {response.status_code}: {response.text[:160]}")
                continue

            data = _first_image_bytes(response.json())
            if data is None:
                logger.warning("[ai_image] 응답에 이미지가 없습니다")
                continue

            cached.write_bytes(data)
            logger.info(f"[ai_image] 생성: {cached.name}")
            return cached

        logger.warning("[ai_image] 쓸 수 있는 키가 없습니다 (무료 티어는 이미지 할당량이 0)")
        return None

    def _cache_path(self, prompt: str, aspect_ratio: str) -> Path:
        key = hashlib.sha1(f"{self.model}|{aspect_ratio}|{prompt}".encode()).hexdigest()[:16]
        return self.cache_dir / f"{key}.png"


def _first_image_bytes(payload: dict) -> bytes | None:
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    return None
