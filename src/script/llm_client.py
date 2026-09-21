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
# 설정한 모델이 Vertex 에 없을 때 내려갈 자리. 오래 있던 모델이라 어느 프로젝트에나 있다.
VERTEX_FALLBACK_MODEL = "gemini-2.5-flash"


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


class VertexGeminiProvider:
    """같은 Gemini 모델을 Vertex 로 부른다.

    AI Studio 키는 **선불 잔액**에서 빠지고 그게 0 이면 402 가 온다(실제로 그렇게 됐다).
    GCP 크레딧을 쓰려면 주소와 인증을 바꿔야 한다 — 페이로드는 한 글자도 다르지 않다.

    키를 여러 개 돌릴 일이 없다. 자격증명이 하나뿐이라 순환할 대상이 없다.
    """

    def __init__(self, vertex, auth, model: str):
        self.vertex = vertex
        self.auth = auth
        self.model = model or VERTEX_FALLBACK_MODEL

    def complete_json(self, prompt: str, temperature: float) -> dict | None:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            },
        }

        # 설정한 모델 이름이 Vertex 에 없을 수 있다(AI Studio 에만 있는 이름이거나,
        # 프로젝트에 아직 안 열린 모델이거나). 그때 한 번만 무난한 모델로 내려간다.
        for model in _model_candidates(self.model):
            for attempt in range(ROTATION_ATTEMPTS):
                if attempt:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                try:
                    response = requests.post(
                        self.vertex.model_url(model, "generateContent"),
                        headers=self.auth.headers(),
                        json=payload,
                        timeout=TIMEOUT,
                    )
                except Exception as exc:
                    logger.warning(f"Vertex 대본 호출 실패 ({model}): {_safe(exc)}")
                    continue

                if response.status_code == 404:
                    logger.warning(f"Vertex 에 {model} 모델이 없습니다 — 다음 후보로 넘어갑니다")
                    break
                if not response.ok:
                    logger.warning(
                        f"Vertex 대본 {response.status_code} ({model}): {response.text[:200]}"
                    )
                    continue

                try:
                    text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                    return json.loads(text)
                except (KeyError, IndexError, ValueError) as exc:
                    logger.warning(f"Vertex 대본 응답을 읽지 못했습니다: {_safe(exc)}")

        return None


def _model_candidates(model: str) -> list[str]:
    if model and model != VERTEX_FALLBACK_MODEL:
        return [model, VERTEX_FALLBACK_MODEL]
    return [VERTEX_FALLBACK_MODEL]


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


def build_provider(
    provider_name: str, model: str, secrets, vertex=None, auth=None
) -> LLMProvider | None:
    # Vertex 가 준비돼 있으면 같은 모델을 그쪽으로 부른다. AI Studio 선불 잔액이 아니라
    # GCP 크레딧에서 빠진다.
    if provider_name in {"gemini", "vertex_gemini"} and vertex is not None and auth is not None:
        return VertexGeminiProvider(vertex, auth, model)
    if provider_name == "vertex_gemini":
        logger.warning("Vertex 준비가 안 돼 AI Studio 키로 대본을 씁니다")
    if provider_name in {"gemini", "vertex_gemini"} and secrets.gemini_api_keys:
        return GeminiProvider(secrets.gemini_api_keys, model)
    if provider_name in {"openai", "openai_compatible"} and secrets.openai_api_key:
        return OpenAICompatibleProvider(secrets.openai_api_key, model, secrets.openai_base_url)

    logger.warning(f"LLM 키가 없어 템플릿 대본으로 진행합니다 (provider={provider_name})")
    return None
