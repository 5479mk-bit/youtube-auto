"""LLM 공급자 추상화.

무료 티어(Gemini)를 기본으로 두되, OpenAI 호환 엔드포인트로 바로 갈아끼울 수 있게
같은 인터페이스를 유지한다. 키가 없으면 ``None`` 을 돌려주고, 호출자가 템플릿
경로로 폴백한다.
"""

from __future__ import annotations

import json
from typing import Protocol

import requests
from loguru import logger

TIMEOUT = 60


class LLMProvider(Protocol):
    def complete_json(self, prompt: str, temperature: float) -> dict | None: ...


class GeminiProvider:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

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
        try:
            response = requests.post(
                url,
                params={"key": self.api_key},
                json=payload,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except Exception as exc:
            logger.warning(f"Gemini 호출 실패: {exc}")
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
    if provider_name == "gemini" and secrets.gemini_api_key:
        return GeminiProvider(secrets.gemini_api_key, model)
    if provider_name in {"openai", "openai_compatible"} and secrets.openai_api_key:
        return OpenAICompatibleProvider(secrets.openai_api_key, model, secrets.openai_base_url)

    logger.warning(f"LLM 키가 없어 템플릿 대본으로 진행합니다 (provider={provider_name})")
    return None
