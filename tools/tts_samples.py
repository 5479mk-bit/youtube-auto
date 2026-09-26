"""고품질 TTS 후보를 한 번에 여러 개 뽑아 귀로 비교한다(영상 렌더 없음).

Vertex 인증이 필요하므로 GitHub Actions(.github/workflows/tts-samples.yml)에서 돌린다.
- Gemini TTS(Vertex): 모델 × 보이스 × 스타일 지시
- Google Cloud TTS Chirp 3 HD: 최신 고품질 보이스(스타일 지시 없음)

    python tools/tts_samples.py [출력 폴더]
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import requests
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.narration.tts_gemini import _pcm_to_mp3  # noqa: E402
from src.pipeline import build_vertex  # noqa: E402
from src.settings import load_settings  # noqa: E402
from src.text_ko import read_aloud  # noqa: E402

TEXT = (
    "1912년 4월 14일 밤 11시 40분. 세계에서 가장 큰 배가 빙산에 부딪혔어. "
    "절대 가라앉지 않는다던 그 배는, 딱 2시간 40분 만에 바다 밑으로 사라졌어. "
    "그런데 진짜 문제는 따로 있었어. 구명보트가 절반밖에 없었던 거야."
)

STYLES = {
    "doc": (
        "심야 미스터리 다큐멘터리 내레이터처럼 읽어줘. 낮고 차분한 목소리로, "
        "시청자한테 비밀 얘기를 들려주듯 몰입감 있게. 중요한 숫자 앞에서는 살짝 뜸을 들이고, "
        "마지막 문장은 소름 돋게 낮춰서."
    ),
    "story": (
        "친한 형이 무서운 실화를 들려주듯 자연스럽게 읽어줘. 반말 구어체 그대로, "
        "속도는 빠르지 않게, 긴장감이 점점 올라가도록. 과장된 연기 톤은 빼고 진짜 사람처럼."
    ),
}

GEMINI_RUNS = [
    ("gemini-2.5-pro-preview-tts", "Charon", "doc"),
    ("gemini-2.5-pro-preview-tts", "Charon", "story"),
    ("gemini-2.5-pro-preview-tts", "Fenrir", "doc"),
    ("gemini-2.5-pro-preview-tts", "Orus", "doc"),
    ("gemini-2.5-pro-preview-tts", "Algenib", "story"),
    ("gemini-2.5-pro-preview-tts", "Iapetus", "story"),
    ("gemini-2.5-flash-preview-tts", "Charon", "doc"),
    ("gemini-2.5-flash-preview-tts", "Fenrir", "story"),
]

CHIRP_VOICES = ["Charon", "Fenrir", "Orus", "Algenib"]


def gemini_sample(vertex, auth, model: str, voice: str, style: str, spoken: str, out: Path) -> bool:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": f"{STYLES[style]}\n\n{spoken}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    for location in (None, "global"):
        url = vertex.model_url(model, "generateContent", location)
        response = requests.post(url, headers=auth.headers(), json=payload, timeout=300)
        if response.ok:
            part = response.json()["candidates"][0]["content"]["parts"][0]
            data = part.get("inlineData") or part["inline_data"]
            _pcm_to_mp3(base64.b64decode(data["data"]), out)
            return True
        logger.warning(f"{model}/{voice} @{location or vertex.location}: {response.status_code} {response.text[:200]}")
    return False


def chirp_sample(auth, voice: str, spoken: str, out: Path) -> bool:
    name = f"ko-KR-Chirp3-HD-{voice}"
    payload = {
        "input": {"text": spoken},
        "voice": {"languageCode": "ko-KR", "name": name},
        "audioConfig": {"audioEncoding": "MP3"},
    }
    response = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers=auth.headers(),
        json=payload,
        timeout=120,
    )
    if not response.ok:
        logger.warning(f"{name}: {response.status_code} {response.text[:200]}")
        return False
    out.write_bytes(base64.b64decode(response.json()["audioContent"]))
    return True


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "output/tts_samples")
    out_dir.mkdir(parents=True, exist_ok=True)

    vertex, auth = build_vertex(load_settings())
    if vertex is None:
        logger.error("Vertex 인증이 없습니다")
        return 1

    spoken = read_aloud(TEXT)
    logger.info(f"낭독문: {spoken}")

    made = 0
    for index, (model, voice, style) in enumerate(GEMINI_RUNS, start=1):
        tag = "pro" if "pro" in model else "flash"
        out = out_dir / f"{index:02d}_gemini-{tag}_{voice}_{style}.mp3"
        if gemini_sample(vertex, auth, model, voice, style, spoken, out):
            logger.info(f"만듦: {out.name}")
            made += 1

    for index, voice in enumerate(CHIRP_VOICES, start=len(GEMINI_RUNS) + 1):
        out = out_dir / f"{index:02d}_chirp3hd_{voice}.mp3"
        if chirp_sample(auth, voice, spoken, out):
            logger.info(f"만듦: {out.name}")
            made += 1

    logger.info(f"샘플 {made}개 완료")
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
