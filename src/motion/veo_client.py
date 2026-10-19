"""Google Veo image-to-video 호출.

공공 API 로 받은 사진 한 장을 넣으면 실제로 움직이는 8초 세로 영상이 나온다.
비동기 작업이라 요청 → 오퍼레이션 폴링 → 다운로드 순서로 진행한다.

주의할 점 둘:
- Veo 는 오디오를 항상 만들고 끌 수 없다. 우리 내레이션과 겹치므로 받아서 바로 버린다.
- 생성물은 서버에 2일만 남는다. 받자마자 캐시에 영구 보관한다.
"""

from __future__ import annotations

import base64
import mimetypes
import subprocess
import time
from pathlib import Path

import requests
from loguru import logger

from ..cloud.vertex import VertexAuth, VertexConfig
from .veo_budget import ClipCost, VeoBudget

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MODELS = {
    "lite": "veo-3.1-lite-generate-preview",
    "fast": "veo-3.1-fast-generate-preview",
    "standard": "veo-3.1-generate-preview",
}
# Vertex 쪽 모델 이름. lite 는 Vertex 에 없어서 fast 로 올라간다(단가가 3배라 로그로 알린다).
# "-preview" 로 불렀더니 404 였다 — 콘솔 코드 샘플로 확인해 보니 이 프로젝트에는 정식
# GA 버전(-001)으로 올라와 있었다. Imagen/Gemini 이미지 때와 같은 부류의 문제였다.
VERTEX_MODELS = {
    "lite": "veo-3.1-fast-generate-001",
    "fast": "veo-3.1-fast-generate-001",
    "standard": "veo-3.1-generate-001",
}
POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 600
# 실패도 과금될 수 있어 재시도는 한 번만 한다.
MAX_ATTEMPTS = 2


class VeoError(RuntimeError):
    pass


class VeoClient:
    def __init__(
        self,
        api_key: str,
        budget: VeoBudget,
        tier: str = "lite",
        resolution: str = "720p",
        duration_seconds: int = 8,
        aspect_ratio: str = "9:16",
        backend: str = "aistudio",
        vertex: "VertexConfig | None" = None,
        auth: "VertexAuth | None" = None,
        spend_guard=None,
        text_to_video: bool = False,
    ):
        self.api_key = api_key
        # True 면 시작 이미지를 보내지 않는다(text-to-video). 이미지에서 출발하면 Veo 가
        # 원본 그림을 거의 그대로 두어 "그림 자체가 크게 움직이지 않는" 문제가 있었다
        # (2026-10-19 사용자 피드백). 이미지는 여전히 만들어 두어 실패 시 파랄랙스로 쓴다.
        self.text_to_video = text_to_video
        self.budget = budget
        self.tier = tier
        self.resolution = resolution
        self.duration_seconds = duration_seconds
        self.aspect_ratio = aspect_ratio
        self.session = requests.Session()
        # 어느 지갑으로 결제할지. vertex 는 GCP 크레딧, aistudio 는 선불 잔액이다.
        self.backend = backend
        self.vertex = vertex
        self.auth = auth
        self.spend_guard = spend_guard
        self.model = (
            VERTEX_MODELS.get(tier, VERTEX_MODELS["fast"])
            if backend == "vertex"
            else MODELS.get(tier, MODELS["lite"])
        )
        if backend == "vertex" and tier == "lite":
            logger.info("[veo] Vertex 에는 lite 등급이 없어 fast 로 올라갑니다 (단가 3배)")

    @property
    def enabled(self) -> bool:
        if self.backend == "vertex":
            return bool(self.vertex and self.vertex.project_id and self.auth and self.auth.available)
        return bool(self.api_key)

    @property
    def billed_tier(self) -> str:
        """실제로 과금되는 등급. Vertex 는 lite 요청도 fast 로 나간다."""
        return "fast" if (self.backend == "vertex" and self.tier == "lite") else self.tier

    def animate(self, image_path: Path, prompt: str) -> Path:
        """사진을 움직이는 영상으로. 캐시에 있으면 호출하지 않는다."""
        params = {
            "aspectRatio": self.aspect_ratio,
            "resolution": self.resolution,
            "durationSeconds": str(self.duration_seconds),
            "model": self.model,
        }
        if self.text_to_video:
            params["mode"] = "text_to_video"  # 캐시 키를 image-to-video 결과와 분리
        key = self.budget.cache_key(image_path, prompt, params)

        cached = self.budget.cached_clip(key)
        if cached:
            logger.info(f"Veo 캐시 적중: {image_path.name} (과금 없음)")
            return cached

        if not self.enabled:
            raise VeoError(
                "Vertex 인증 정보가 없어 Veo 를 호출할 수 없습니다"
                if self.backend == "vertex"
                else "GEMINI_API_KEY 가 없어 Veo 를 호출할 수 없습니다"
            )

        self.budget.ensure_can_generate()
        # 크레딧이 남았는지 **호출 전에** 본다. 바닥난 뒤에 부르면 그때부터 카드로 나간다.
        cost = ClipCost(clips=1, seconds_each=self.duration_seconds, tier=self.billed_tier)
        if self.spend_guard is not None:
            self.spend_guard.ensure_can_spend(cost.krw(), f"Veo {self.billed_tier} 1클립")

        logger.info(
            f"Veo 생성 요청: {image_path.name} "
            f"({self.backend}/{self.billed_tier}, {self.duration_seconds}초, 약 {cost.krw():,}원)"
        )
        raw_path = self.budget.clip_path(key).with_suffix(".raw.mp4")
        if self.backend == "vertex":
            self._generate_vertex(image_path, prompt, params, raw_path)
        else:
            operation = self._start(image_path, prompt, params)
            self._download(self._wait(operation), raw_path)

        self.budget.record(1)
        if self.spend_guard is not None:
            self.spend_guard.record(cost.krw(), "veo", f"{self.billed_tier} {self.duration_seconds}초")

        final_path = self.budget.clip_path(key)
        _strip_audio(raw_path, final_path)
        raw_path.unlink(missing_ok=True)
        return final_path

    def _instance(self, image_path: Path, prompt: str, mime_type: str) -> dict:
        instance: dict = {"prompt": prompt}
        if not self.text_to_video:
            # predictLongRunning 은 Gemini 생성 API 의 inlineData 형식을 안 받는다
            # ("`inlineData` isn't supported by this model"). 이 엔드포인트는
            # bytesBase64Encoded 를 쓴다.
            instance["image"] = {
                "bytesBase64Encoded": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                "mimeType": mime_type,
            }
        return instance

    def _generate_vertex(self, image_path: Path, prompt: str, params: dict, dest: Path) -> None:
        """Vertex 경로. 요청 → fetchPredictOperation 폴링 → base64 영상 저장.

        AI Studio 와 두 가지가 다르다.
        - 오퍼레이션을 GET 이 아니라 `:fetchPredictOperation` POST 로 조회한다.
        - 결과가 URI 가 아니라 base64 로 바로 온다(스토리지 버킷을 안 쓸 때).
        """
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        payload = {
            "instances": [
                self._instance(image_path, prompt, mime_type)
            ],
            "parameters": {
                "aspectRatio": params["aspectRatio"],
                "resolution": params["resolution"],
                "durationSeconds": int(params["durationSeconds"]),
                "sampleCount": 1,
            },
        }

        response = self.session.post(
            self.vertex.model_url(self.model, "predictLongRunning"),
            headers=self.auth.headers(),
            json=payload,
            timeout=120,
        )
        if response.status_code != 200:
            raise VeoError(f"Vertex 요청 실패({response.status_code}): {response.text[:300]}")
        operation = response.json().get("name")
        if not operation:
            raise VeoError(f"오퍼레이션 이름이 없습니다: {response.text[:200]}")

        deadline = time.time() + POLL_TIMEOUT_SECONDS
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            poll = self.session.post(
                self.vertex.model_url(self.model, "fetchPredictOperation"),
                headers=self.auth.headers(),
                json={"operationName": operation},
                timeout=60,
            )
            if poll.status_code != 200:
                raise VeoError(f"Vertex 폴링 실패({poll.status_code}): {poll.text[:300]}")
            body = poll.json()
            if body.get("error"):
                raise VeoError(f"Vertex 생성 실패: {body['error']}")
            if not body.get("done"):
                continue

            data = _find_video_bytes(body)
            if data:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(base64.b64decode(data))
                return
            uri = _find_video_uri(body)
            if uri:
                self._download_vertex(uri, dest)
                return
            raise VeoError(f"생성 결과에 영상이 없습니다: {str(body)[:300]}")

        raise VeoError(f"Veo 생성이 {POLL_TIMEOUT_SECONDS}초 안에 끝나지 않았습니다")

    def _download_vertex(self, uri: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # storageUri 를 안 줘도 일부 응답은 gs:// 링크로 돌아온다. gs:// 는 HTTP GET 이
        # 안 되니 Cloud Storage JSON API(다운로드) 주소로 바꿔 같은 Bearer 토큰으로 받는다.
        if uri.startswith("gs://"):
            bucket, _, obj = uri.removeprefix("gs://").partition("/")
            uri = (
                f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
                f"{requests.utils.quote(obj, safe='')}?alt=media"
            )
        response = self.session.get(uri, headers=self.auth.headers(), timeout=300)
        response.raise_for_status()
        dest.write_bytes(response.content)

    def _start(self, image_path: Path, prompt: str, params: dict) -> str:
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        payload = {
            "instances": [
                self._instance(image_path, prompt, mime_type)
            ],
            "parameters": {
                "aspectRatio": params["aspectRatio"],
                "resolution": params["resolution"],
                # 문자열로 보내면 400 이다. 캐시 키는 문자열이라도 요청은 숫자로 나간다.
                "durationSeconds": int(params["durationSeconds"]),
            },
        }

        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self.session.post(
                    f"{BASE_URL}/models/{self.model}:predictLongRunning",
                    headers={"x-goog-api-key": self.api_key},
                    json=payload,
                    timeout=120,
                )
                response.raise_for_status()
                name = response.json().get("name")
                if not name:
                    raise VeoError(f"오퍼레이션 이름이 없습니다: {response.text[:200]}")
                return name
            except Exception as exc:
                last_error = exc
                if attempt < MAX_ATTEMPTS - 1:
                    logger.warning(f"Veo 요청 실패, 한 번만 재시도합니다: {exc}")
                    time.sleep(5)
        raise VeoError(f"Veo 요청 실패: {last_error}")

    def _wait(self, operation_name: str) -> str:
        deadline = time.time() + POLL_TIMEOUT_SECONDS
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            response = self.session.get(
                f"{BASE_URL}/{operation_name}",
                headers={"x-goog-api-key": self.api_key},
                timeout=60,
            )
            response.raise_for_status()
            body = response.json()

            if body.get("error"):
                raise VeoError(f"Veo 생성 실패: {body['error']}")
            if body.get("done"):
                uri = _find_video_uri(body)
                if not uri:
                    raise VeoError(f"생성 결과에 영상이 없습니다: {str(body)[:300]}")
                return uri

        raise VeoError(f"Veo 생성이 {POLL_TIMEOUT_SECONDS}초 안에 끝나지 않았습니다")

    def _download(self, uri: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        response = self.session.get(
            uri, headers={"x-goog-api-key": self.api_key}, timeout=300
        )
        response.raise_for_status()
        dest.write_bytes(response.content)


def _find_video_bytes(body: dict) -> str | None:
    """Vertex 는 영상을 base64 로 바로 준다. 응답 어디에 있든 첫 값을 찾는다."""

    def walk(node) -> str | None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "bytesBase64Encoded" and isinstance(value, str):
                    return value
                found = walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        return None

    return walk(body.get("response") or body)


def _find_video_uri(body: dict) -> str | None:
    """응답 형태가 버전마다 조금씩 달라 uri 로 끝나는 첫 값을 찾는다."""
    def walk(node) -> str | None:
        if isinstance(node, dict):
            for key, value in node.items():
                # gcsUri: 콘솔 코드 샘플의 실제 응답 형태(response.videos[].gcsUri).
                if key in {"uri", "videoUri", "fileUri", "gcsUri"} and isinstance(value, str):
                    return value
                found = walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        return None

    return walk(body.get("response") or body)


def _strip_audio(source: Path, dest: Path) -> None:
    """Veo 오디오는 내레이션과 겹치므로 영상 트랙만 남긴다."""
    import imageio_ffmpeg

    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-i",
            str(source),
            "-an",
            "-c:v",
            "copy",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
