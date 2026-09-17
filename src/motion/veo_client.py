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

from .veo_budget import VeoBudget

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MODELS = {
    "lite": "veo-3.1-lite-generate-preview",
    "fast": "veo-3.1-fast-generate-preview",
    "standard": "veo-3.1-generate-preview",
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
    ):
        self.api_key = api_key
        self.budget = budget
        self.tier = tier
        self.model = MODELS.get(tier, MODELS["lite"])
        self.resolution = resolution
        self.duration_seconds = duration_seconds
        self.aspect_ratio = aspect_ratio
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def animate(self, image_path: Path, prompt: str) -> Path:
        """사진을 움직이는 영상으로. 캐시에 있으면 호출하지 않는다."""
        params = {
            "aspectRatio": self.aspect_ratio,
            "resolution": self.resolution,
            "durationSeconds": str(self.duration_seconds),
            "model": self.model,
        }
        key = self.budget.cache_key(image_path, prompt, params)

        cached = self.budget.cached_clip(key)
        if cached:
            logger.info(f"Veo 캐시 적중: {image_path.name} (과금 없음)")
            return cached

        if not self.enabled:
            raise VeoError("GEMINI_API_KEY 가 없어 Veo 를 호출할 수 없습니다")

        self.budget.ensure_can_generate()

        logger.info(f"Veo 생성 요청: {image_path.name} ({self.tier}, {self.duration_seconds}초)")
        operation = self._start(image_path, prompt, params)
        video_uri = self._wait(operation)

        raw_path = self.budget.clip_path(key).with_suffix(".raw.mp4")
        self._download(video_uri, raw_path)
        self.budget.record(1)

        final_path = self.budget.clip_path(key)
        _strip_audio(raw_path, final_path)
        raw_path.unlink(missing_ok=True)
        return final_path

    def _start(self, image_path: Path, prompt: str, params: dict) -> str:
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        payload = {
            "instances": [
                {
                    "prompt": prompt,
                    # predictLongRunning 은 Gemini 생성 API 의 inlineData 형식을 안 받는다
                    # ("`inlineData` isn't supported by this model"). 이 엔드포인트는
                    # bytesBase64Encoded 를 쓴다.
                    "image": {
                        "bytesBase64Encoded": base64.b64encode(
                            image_path.read_bytes()
                        ).decode("ascii"),
                        "mimeType": mime_type,
                    },
                }
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


def _find_video_uri(body: dict) -> str | None:
    """응답 형태가 버전마다 조금씩 달라 uri 로 끝나는 첫 값을 찾는다."""
    def walk(node) -> str | None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"uri", "videoUri", "fileUri"} and isinstance(value, str):
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
