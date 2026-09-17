"""LLM 공급자 추상화.

무료 티어(Gemini)를 기본으로 두되, OpenAI 호환 엔드포인트로 바로 갈아끼울 수 있게
같은 인터페이스를 유지한다. 키가 없으면 ``None`` 을 돌려주고, 호출자가 템플릿
경로로 폴백한다.
"""

from __future__ import annotations

import json
import time
from typing import Protocol

import requests
from loguru import logger

TIMEOUT = 60
# 키를 한 바퀴 도는 것을 몇 번 반복할지, 그 사이에 얼마나 쉴지.
ROTATION_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 4


def _safe(error: Exception) -> str:
    """오류 메시지에 키가 섞여 나오지 않게 거른다."""
    text = str(error)
    if "key=" in text:
        text = text.split("key=")[0] + "key=***"
    return text[:200]


class LLMProvider(Protocol):
    def complete_json(self, prompt: str, temperature: float) -> dict | None: ...


class GeminiProvider:
    def __init__(self, api_keys: list[str], model: str):
        self.api_keys = [key for key in api_keys if key]
        self.model = model
        # 403(프로젝트 접근 거부)이 뜬 키. 이번 실행 동안 다시 쓰지 않는다.
        self._denied: set[str] = set()

    def complete_json(self, prompt: str, temperature: float) -> dict | None:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            },
        }

        # 503(과부하)은 잠깐 뒤에 다시 하면 되는 실패다. 키를 한 바퀴 돌고 끝내면
        # 대본이 통째로 템플릿으로 떨어지므로, 한 바퀴를 몇 번 더 돈다.
        for attempt in range(ROTATION_ATTEMPTS):
            if attempt:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            for key in self.api_keys:
                if key in self._denied:
                    continue
                try:
                    # 키는 헤더로만 보낸다. 쿼리로 붙이면 실패 로그에 URL 이 찍히면서
                    # 키가 그대로 남는다.
                    response = requests.post(
                        url,
                        headers={"x-goog-api-key": key},
                        json=payload,
                        timeout=TIMEOUT,
                    )
                    if response.status_code == 429:
                        logger.warning("LLM 키 하나가 한도에 걸려 다음 키로 넘어갑니다")
                        continue
                    if response.status_code == 403:
                        # 프로젝트 자체가 막힌 키다. 다시 시도해도 늘 403 이라 빼둔다.
                        logger.warning("LLM 키 하나가 권한 거부(403)라 이번 실행에서 제외합니다")
                        self._denied.add(key)
                        continue
                    response.raise_for_status()
                    text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                    return json.loads(text)
                except Exception as exc:
                    logger.warning(f"Gemini 호출 실패 ({self.model}): {_safe(exc)}")

        return None


class OpenAICompatibleProvider:
    def __init__(self, api_key: str, model: str, base_url: str = ""):
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def complete_json(self, prompt: str, temperature: float) -> dict | None:
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": temperature,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            return json.loads(text)
        except Exception as exc:
            logger.warning(f"OpenAI 호환 엔드포인트 호출 실패: {exc}")
            return None


def build_provider(provider_name: str, model: str, secrets) -> LLMProvider | None:
    if provider_name == "gemini" and secrets.gemini_api_keys:
        return GeminiProvider(secrets.gemini_api_keys, model)
    if provider_name in {"openai", "openai_compatible"} and secrets.openai_api_key:
        return OpenAICompatibleProvider(secrets.openai_api_key, model, secrets.openai_base_url)

    logger.warning(f"LLM 키가 없어 템플릿 대본으로 진행합니다 (provider={provider_name})")
    return None
