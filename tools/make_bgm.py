"""배경음악을 직접 합성한다.

무료 BGM 은 대개 출처 표기 의무가 있고, 유명 제작자 트랙은 Content ID 에 걸려 유튜브에서
수익 클레임이 들어오기도 한다. 직접 만들면 저작권이 우리에게 있어 그 위험이 없다.

내레이션 밑에 깔리는 용도라 화성은 단순하게 두고(단조 드론 + 5도), 대신 느린 흔들림과
잔향으로 공간감만 만든다. 멜로디가 있으면 말과 부딪힌다.

    python tools/make_bgm.py            # resource/bgm/ 에 분위기별로 생성
    python tools/make_bgm.py --seconds 90
"""

from __future__ import annotations

import argparse
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Mood:
    name: str
    root_hz: float
    # 드론 위에 얹을 음정비. (1, 1.5) 면 근음과 5도.
    intervals: tuple[float, ...]
    # 공기감(필터링된 노이즈) 크기
    air: float
    # 심장박동 같은 저음 펄스 간격(초). 0 이면 없음.
    pulse_seconds: float
    brightness: float


MOODS: tuple[Mood, ...] = (
    # 미스터리·추적: 낮은 드론에 느린 맥박. 말 밑에서 긴장만 만든다.
    Mood("mystery", root_hz=55.0, intervals=(1.0, 1.5, 3.0), air=0.05, pulse_seconds=2.0, brightness=0.28),
    # 장엄·유적: 펄스 없이 넓게. 궁궐·석조물 화면에 어울린다.
    Mood("solemn", root_hz=65.4, intervals=(1.0, 1.5, 2.0, 3.0), air=0.04, pulse_seconds=0.0, brightness=0.34),
    # 불길·사건: 단2도를 살짝 섞어 불협을 남긴다. 괴이한 사건 기사에.
    Mood("ominous", root_hz=49.0, intervals=(1.0, 1.06, 1.5, 2.0), air=0.07, pulse_seconds=3.2, brightness=0.22),
)


def _lowpass(signal: np.ndarray, cutoff: float) -> np.ndarray:
    """한 극 저역통과. 합성음의 날카로운 고음을 깎아 귀에 편하게 만든다."""
    alpha = np.clip(cutoff, 0.0, 1.0)
    out = np.empty_like(signal)
    acc = 0.0
    for index, value in enumerate(signal):
        acc += alpha * (value - acc)
        out[index] = acc
    return out


def _reverb(signal: np.ndarray, sample_rate: int) -> np.ndarray:
    """빗살 필터 몇 개로 만든 거친 잔향. 공간감이 있어야 싸구려로 안 들린다."""
    out = signal.copy()
    for delay_ms, gain in ((37, 0.32), (53, 0.26), (71, 0.22), (97, 0.18)):
        delay = int(sample_rate * delay_ms / 1000)
        echo = np.zeros_like(signal)
        echo[delay:] = signal[:-delay] * gain
        out += echo
    return out


def synthesize(mood: Mood, seconds: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    t = np.linspace(0.0, seconds, int(sample_rate * seconds), endpoint=False)
    audio = np.zeros_like(t)

    for index, ratio in enumerate(mood.intervals):
        freq = mood.root_hz * ratio
        # 아주 느린 흔들림. 완전히 고정된 음은 기계처럼 들린다.
        drift = 1.0 + 0.0015 * np.sin(2 * np.pi * (0.03 + 0.017 * index) * t)
        # 살짝 어긋난 두 개를 겹쳐 두께를 만든다(유니즌).
        voice = np.sin(2 * np.pi * freq * drift * t) + np.sin(
            2 * np.pi * freq * drift * 1.004 * t + 1.1
        )
        # 배음을 조금 섞어야 사인파 특유의 빈 소리가 가신다.
        voice += 0.18 * np.sin(2 * np.pi * freq * 2 * drift * t)
        # 성부마다 다른 속도로 숨쉬게 해 서로 어긋나며 움직인다.
        swell = 0.55 + 0.45 * np.sin(2 * np.pi * (0.05 + 0.021 * index) * t - index)
        audio += voice * swell / (index + 1.6)

    if mood.air > 0:
        noise = np.random.default_rng(7).normal(0, 1, t.size)
        audio += _lowpass(noise, 0.02) * mood.air * 12

    if mood.pulse_seconds > 0:
        phase = np.mod(t, mood.pulse_seconds) / mood.pulse_seconds
        envelope = np.exp(-phase * 14)
        audio += 0.5 * np.sin(2 * np.pi * mood.root_hz * 0.5 * t) * envelope

    audio = _lowpass(audio, mood.brightness)
    audio = _reverb(audio, sample_rate)

    # 앞뒤를 길게 여닫아야 반복 재생될 때 이음매가 안 튄다.
    fade = int(sample_rate * 2.5)
    audio[:fade] *= np.linspace(0, 1, fade)
    audio[-fade:] *= np.linspace(1, 0, fade)

    peak = np.abs(audio).max()
    return audio / peak * 0.85 if peak else audio


def write_wav(audio: np.ndarray, path: Path, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes((audio * 32767).astype(np.int16).tobytes())


def to_mp3(wav_path: Path, mp3_path: Path) -> bool:
    import imageio_ffmpeg

    try:
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                "-i", str(wav_path), "-codec:a", "libmp3lame", "-b:a", "192k", str(mp3_path),
            ],
            check=True,
        )
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="배경음악 합성")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--out", default=str(PROJECT_ROOT / "resource" / "bgm"))
    args = parser.parse_args()

    out_dir = Path(args.out)
    for mood in MOODS:
        audio = synthesize(mood, args.seconds)
        wav_path = out_dir / f"{mood.name}.wav"
        write_wav(audio, wav_path)
        mp3_path = out_dir / f"{mood.name}.mp3"
        if to_mp3(wav_path, mp3_path):
            wav_path.unlink()
            print(f"{mp3_path} ({args.seconds:.0f}초)")
        else:
            print(f"{wav_path} ({args.seconds:.0f}초, mp3 변환 실패)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
