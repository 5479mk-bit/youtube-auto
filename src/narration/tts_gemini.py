"""Gemini TTS 내레이션 합성.

edge-tts 와 결정적으로 다른 점은 **스타일을 말로 지시할 수 있다**는 것이다.
"에너지 있게, 숏폼 내레이터처럼" 같은 프롬프트가 실제로 먹힌다.

무료 티어의 한도는 분당이 아니라 **하루 10회(모델·프로젝트당)** 다. 문장마다 부르면
한 편도 못 만든다. 그래서 부 전체를 한 번에 읽히고, 받은 음원을 문장 경계에서 자른다.
한 편에 두 번이면 끝난다.

키는 여러 개를 돌린다. 한도에 걸린 키는 그 날 다시 쓰지 않는다.
"""

from __future__ import annotations

import base64
import subprocess
import wave
from pathlib import Path

import requests
from loguru import logger

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
# 응답은 24kHz 16bit 모노 PCM 으로 온다.
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1
# 문장을 자를 무음 기준. 문장 끝의 자연스러운 쉼을 잡되 말 중간의 숨은 넘긴다.
SPLIT_SILENCE_DB = "-38dB"
SPLIT_MIN_SILENCE = 0.14
# 부 하나(40~50초)를 읽히는 데 드는 추정 비용. 실제 청구액은 GCP 청구서가 정답이다.
TTS_COST_KRW = 50


class GeminiTTS:
    name = "gemini"

    def __init__(
        self,
        api_keys: list[str],
        voice: str = "Fenrir",
        model: str = DEFAULT_MODEL,
        style_prompt: str = "",
        backend: str = "aistudio",
        vertex=None,
        auth=None,
        spend_guard=None,
        cost_krw: int = TTS_COST_KRW,
    ):
        self.api_keys = [key for key in api_keys if key]
        self.voice = voice
        self.model = model or DEFAULT_MODEL
        self.style_prompt = style_prompt or "자연스러운 속도의 내레이터처럼"
        self.session = requests.Session()
        # 한도에 걸린 키는 이 실행 동안 건너뛴다. 어차피 내일까지 안 풀린다.
        self._exhausted: set[str] = set()
        # 어느 지갑으로 결제할지. vertex = GCP 크레딧, aistudio = 선불 잔액.
        self.backend = backend
        self.vertex = vertex
        self.auth = auth
        self.spend_guard = spend_guard
        self.cost_krw = int(cost_krw)

    @property
    def enabled(self) -> bool:
        if self.backend == "vertex":
            return bool(self.vertex and self.auth)
        return bool(self.api_keys)

    @property
    def supports_batch(self) -> bool:
        return True

    def synthesize(self, text: str, rate: str, out_path: Path) -> bool:
        """문장 하나. voice-test 처럼 한 문장만 필요할 때 쓴다."""
        audio = self._request(self._build_prompt([text]))
        if audio is None:
            return False
        _pcm_to_mp3(audio, out_path)
        return True

    def synthesize_batch(self, texts: list[str], out_dir: Path, prefix: str) -> list[Path] | None:
        """부 전체를 한 번에 읽히고 문장 경계에서 자른다. 실패하면 None."""
        if not texts:
            return []

        audio = self._request(self._build_prompt(texts))
        if audio is None:
            return None

        out_dir.mkdir(parents=True, exist_ok=True)
        whole = out_dir / f"{prefix}_whole.mp3"
        _pcm_to_mp3(audio, whole)

        paths = _split_into_sentences(whole, texts, out_dir, prefix)
        whole.unlink(missing_ok=True)
        return paths

    def _build_prompt(self, texts: list[str]) -> str:
        if len(texts) == 1:
            return f"{self.style_prompt} 읽어줘:\n{texts[0]}"
        # 줄 사이를 쉬어 달라고 해야 나중에 문장 경계를 찾을 수 있다.
        body = "\n".join(texts)
        return (
            f"{self.style_prompt} 읽어줘. 각 줄은 한 문장이고, 줄과 줄 사이에는 "
            f"반 박자 쉬어줘. 줄 번호나 기호는 읽지 마:\n{body}"
        )

    def _request(self, prompt: str) -> bytes | None:
        if self.backend == "vertex":
            return self._request_vertex(prompt)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}}
                },
            },
        }

        for key in self.api_keys:
            if key in self._exhausted:
                continue
            try:
                response = self.session.post(
                    f"{BASE_URL}/models/{self.model}:generateContent",
                    headers={"x-goog-api-key": key},
                    json=payload,
                    timeout=180,
                )
            except Exception as exc:
                logger.warning(f"Gemini TTS 호출 실패: {exc}")
                continue

            if response.status_code == 429:
                self._exhausted.add(key)
                logger.warning(
                    f"키 하나가 하루 한도(10회)를 다 썼습니다. "
                    f"남은 키 {len(self.api_keys) - len(self._exhausted)}개로 계속합니다"
                )
                continue
            if not response.ok:
                logger.warning(f"Gemini TTS 오류 {response.status_code}: {response.text[:200]}")
                continue

            try:
                data = response.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                return base64.b64decode(data["data"])
            except (KeyError, IndexError, ValueError) as exc:
                logger.warning(f"Gemini TTS 응답을 읽지 못했습니다: {exc}")
                continue

        logger.warning("쓸 수 있는 Gemini 키가 없습니다")
        return None

    def _request_vertex(self, prompt: str) -> bytes | None:
        """같은 모델을 GCP 크레딧으로 부른다. 페이로드는 AI Studio 쪽과 같다."""
        if self.spend_guard is not None:
            try:
                self.spend_guard.ensure_can_spend(self.cost_krw, "내레이션 1부")
            except Exception as exc:
                logger.warning(f"[tts] {exc}")
                return None

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}}
                },
            },
        }
        try:
            response = self.session.post(
                self.vertex.model_url(self.model, "generateContent"),
                headers=self.auth.headers(),
                json=payload,
                timeout=180,
            )
        except Exception as exc:
            logger.warning(f"Vertex TTS 호출 실패: {exc}")
            return None

        if not response.ok:
            # 404 면 이 프로젝트에 TTS 모델이 안 열린 것이다. edge-tts 로 내려간다.
            logger.warning(f"Vertex TTS 오류 {response.status_code}: {response.text[:200]}")
            return None

        try:
            part = response.json()["candidates"][0]["content"]["parts"][0]
            data = part.get("inlineData") or part["inline_data"]
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning(f"Vertex TTS 응답을 읽지 못했습니다: {exc}")
            return None

        if self.spend_guard is not None:
            self.spend_guard.record(self.cost_krw, "tts", self.model)
        return base64.b64decode(data["data"])


def _split_into_sentences(
    whole: Path, texts: list[str], out_dir: Path, prefix: str
) -> list[Path]:
    """긴 음원을 문장 수만큼 자른다.

    무음 위치를 먼저 찾고, 문장 수와 맞아떨어지면 거기서 자른다. 맞지 않으면 글자 수
    비례로 나눈다. 자막이 조금 밀릴지언정 파이프라인이 멈추지는 않게 한다.
    """
    total = _duration(whole)
    needed = len(texts) - 1
    silences = _silence_boundaries(whole)

    if len(silences) >= needed:
        # 쉼이 넉넉히 잡히면 그중 가장 긴 것들이 문장 경계다. 말 중간의 짧은 숨은
        # 자연히 걸러진다.
        chosen = sorted(silences, key=lambda item: item[1], reverse=True)[:needed]
        boundaries = sorted(point for point, _ in chosen)
    else:
        logger.info(
            f"무음 경계 {len(silences)}개로는 문장 {len(texts)}개를 나눌 수 없어 "
            "글자 수 비례로 나눕니다"
        )
        boundaries = _proportional_boundaries(texts, total)

    cuts = [0.0, *boundaries, total]
    paths = []
    for index in range(len(texts)):
        path = out_dir / f"{prefix}_line{index:02d}.mp3"
        _extract(whole, cuts[index], cuts[index + 1], path)
        paths.append(path)
    return paths


def _silence_boundaries(audio_path: Path) -> list[tuple[float, float]]:
    """무음 구간의 (한가운데 시각, 길이). 길이가 길수록 문장 경계일 가능성이 높다."""
    import imageio_ffmpeg

    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-i",
            str(audio_path),
            "-af",
            f"silencedetect=n={SPLIT_SILENCE_DB}:d={SPLIT_MIN_SILENCE}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )

    starts: list[float] = []
    boundaries: list[tuple[float, float]] = []
    for raw in result.stderr.splitlines():
        if "silence_start:" in raw:
            starts.append(float(raw.split("silence_start:")[1].strip()))
        elif "silence_end:" in raw and starts:
            end = float(raw.split("silence_end:")[1].split("|")[0].strip())
            start = starts.pop()
            if start > 0.05:  # 맨 앞의 무음은 경계가 아니다
                boundaries.append(((start + end) / 2, end - start))
    return boundaries


def _proportional_boundaries(texts: list[str], total: float) -> list[float]:
    lengths = [max(len(text), 1) for text in texts]
    whole_length = sum(lengths)
    boundaries = []
    cursor = 0
    for length in lengths[:-1]:
        cursor += length
        boundaries.append(total * cursor / whole_length)
    return boundaries


def _extract(source: Path, start: float, end: float, dest: Path) -> None:
    import imageio_ffmpeg

    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-i",
            str(source),
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{max(end, start + 0.1):.3f}",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )


def _duration(path: Path) -> float:
    from moviepy import AudioFileClip

    with AudioFileClip(str(path)) as clip:
        return float(clip.duration)


def _pcm_to_mp3(pcm: bytes, out_path: Path) -> None:
    """헤더 없는 PCM 이라 wav 로 감싼 뒤 mp3 로 옮긴다."""
    import imageio_ffmpeg

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path = out_path.with_suffix(".wav")
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)

    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(wav_path), str(out_path)],
        check=True,
        capture_output=True,
    )
    wav_path.unlink(missing_ok=True)
