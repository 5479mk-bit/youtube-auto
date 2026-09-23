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
import time
from pathlib import Path

import requests
from loguru import logger

from ..cloud.vertex import VertexAuth, VertexConfig

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash-image"
# Vertex 쪽 이미지 모델. 이 프로젝트의 Model Garden 카탈로그에 Imagen 계열이 아예
# 없어서(검색해도 안 뜬다) Gemini 이미지 모델을 그대로 Vertex 로 부른다 — AI Studio
# 분기와 같은 generateContent 형식이라 페이로드를 그대로 재사용한다.
VERTEX_MODEL = "gemini-3.1-flash-image"
IMAGE_COST_KRW = 30
TIMEOUT = 180
# 신규 모델이라 프로젝트별 분당 한도가 낮다. 한 번만 짧게 쉬었다 재시도한다.
VERTEX_IMAGE_RETRIES = 2
VERTEX_IMAGE_RETRY_SECONDS = 8


class AiImageClient:
    def __init__(
        self,
        api_keys: list[str],
        cache_dir: Path,
        model: str = DEFAULT_MODEL,
        backend: str = "aistudio",
        vertex: VertexConfig | None = None,
        auth: VertexAuth | None = None,
        spend_guard=None,
        vertex_model: str = VERTEX_MODEL,
        cost_krw: int = IMAGE_COST_KRW,
    ):
        self.api_keys = [key for key in api_keys if key]
        self.model = model or DEFAULT_MODEL
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # 403(프로젝트 접근 거부)·429(할당량 0)가 뜬 키는 이번 실행 동안 다시 쓰지 않는다.
        # 컷마다 죽은 키를 또 찔러보면 렌더가 몇 분씩 길어진다.
        self._dead: set[str] = set()
        # 어느 지갑으로 결제할지. vertex = GCP 크레딧, aistudio = 선불 잔액.
        self.backend = backend
        self.vertex = vertex
        self.auth = auth
        self.spend_guard = spend_guard
        self.vertex_model = vertex_model or VERTEX_MODEL
        self.cost_krw = int(cost_krw)

    @property
    def enabled(self) -> bool:
        if self.backend == "vertex":
            return bool(self.vertex and self.vertex.project_id and self.auth and self.auth.available)
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

        # 크레딧이 남았는지 호출 전에 본다. 바닥나면 여기서 멈춘다(추가 과금 방지).
        if self.spend_guard is not None:
            try:
                self.spend_guard.ensure_can_spend(self.cost_krw, "이미지 1장")
            except Exception as exc:
                logger.warning(f"[ai_image] {exc}")
                return None

        if self.backend == "vertex":
            return self._generate_vertex(prompt, aspect_ratio, cached)

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
            if self.spend_guard is not None:
                self.spend_guard.record(self.cost_krw, "image", self.model)
            return cached

        logger.warning("[ai_image] 쓸 수 있는 키가 없습니다 (무료 티어는 이미지 할당량이 0)")
        return None

    def _generate_vertex(self, prompt: str, aspect_ratio: str, dest: Path) -> Path | None:
        """Vertex 이미지 경로. GCP 크레딧으로 결제된다.

        모델 이름에 imagen 이 들어 있으면 Imagen 전용 predict 형식을, 그 외(Gemini
        이미지 계열)면 AI Studio 분기와 같은 generateContent 형식을 쓴다.
        """
        if "imagen" in self.vertex_model.lower():
            return self._generate_vertex_imagen(prompt, aspect_ratio, dest)
        return self._generate_vertex_gemini(prompt, aspect_ratio, dest)

    def _generate_vertex_imagen(self, prompt: str, aspect_ratio: str, dest: Path) -> Path | None:
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": aspect_ratio,
                # 조선 사건 장면은 사람이 나와도 얼굴을 크게 잡지 않는다. 그래도 인물
                # 필터에 걸려 빈 응답이 오는 일이 있어 허용 범위를 명시한다.
                "personGeneration": "allow_adult",
            },
        }
        try:
            response = requests.post(
                self.vertex.model_url(self.vertex_model, "predict"),
                headers=self.auth.headers(),
                json=payload,
                timeout=TIMEOUT,
            )
        except Exception as exc:
            logger.warning(f"[ai_image] Vertex 호출 실패: {exc}")
            return None

        if response.status_code != 200:
            logger.warning(f"[ai_image] Vertex {response.status_code}: {response.text[:200]}")
            return None

        predictions = response.json().get("predictions", [])
        for prediction in predictions:
            encoded = prediction.get("bytesBase64Encoded")
            if encoded:
                dest.write_bytes(base64.b64decode(encoded))
                logger.info(f"[ai_image] 생성(vertex): {dest.name}")
                if self.spend_guard is not None:
                    self.spend_guard.record(self.cost_krw, "image", self.vertex_model)
                return dest

        logger.warning(f"[ai_image] Vertex 응답에 이미지가 없습니다: {str(response.text)[:200]}")
        return None

    def _generate_vertex_gemini(self, prompt: str, aspect_ratio: str, dest: Path) -> Path | None:
        """AI Studio 분기와 같은 generateContent 형식. 주소·인증만 Vertex 것을 쓴다.

        AI Studio 쪽은 role 없이도 받아주지만, Vertex 는 "Please use a valid role:
        user, model." 로 400 을 낸다 — 명시해야 한다.
        """
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": aspect_ratio},
            },
        }
        url = self.vertex.model_url(self.vertex_model, "generateContent", location="global")

        # 새 모델이라 프로젝트별 분당 요청 한도가 낮다 — 컷을 몰아서 만들면 429 가
        # 섞여 나온다. 한 번만 짧게 쉬었다 다시 부른다(계속 실패하면 대체 화면으로).
        response = None
        for attempt in range(VERTEX_IMAGE_RETRIES):
            if attempt:
                time.sleep(VERTEX_IMAGE_RETRY_SECONDS)
            try:
                response = requests.post(
                    # Gemini 이미지 생성은 리전 엔드포인트(us-central1 등)에는 없고
                    # global 에만 있다 — 콘솔 코드 샘플로 확인됐다.
                    url,
                    headers=self.auth.headers(),
                    json=payload,
                    timeout=TIMEOUT,
                )
            except Exception as exc:
                logger.warning(f"[ai_image] Vertex 호출 실패: {exc}")
                return None
            if response.status_code != 429:
                break
            logger.info(f"[ai_image] Vertex 429(할당량) — {VERTEX_IMAGE_RETRY_SECONDS}초 뒤 재시도")

        if response.status_code != 200:
            logger.warning(f"[ai_image] Vertex {response.status_code}: {response.text[:200]}")
            return None

        data = _first_image_bytes(response.json())
        if data is None:
            logger.warning(f"[ai_image] Vertex 응답에 이미지가 없습니다: {str(response.text)[:200]}")
            return None

        dest.write_bytes(data)
        logger.info(f"[ai_image] 생성(vertex): {dest.name}")
        if self.spend_guard is not None:
            self.spend_guard.record(self.cost_krw, "image", self.vertex_model)
        return dest

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
