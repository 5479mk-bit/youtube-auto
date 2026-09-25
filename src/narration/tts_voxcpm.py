"""로컬 VoxCPM(OpenBMB) TTS.

Supertonic(HTTP 서버 호출)과 달리 이건 모델을 파이썬에서 직접 로딩해 추론한다.
VoxCPM 은 **GPU 전용**이다(PyTorch>=2.5.0, CUDA>=12.0 필요) — GitHub Actions 러너처럼
GPU 가 없는 환경에서는 `import voxcpm` 부터 실패하므로 항상 조용히 다음 엔진
(Supertonic → edge-tts)으로 폴백된다. 사용자가 직접 GPU 머신에서 파이프라인을 돌릴
때만 실제로 음성을 만든다.

이 저장소의 `requirements.txt` 에는 voxcpm/soundfile/torch 를 넣지 않는다 — GPU 전용
무거운 패키지를 CI 설치 목록에 넣으면 GPU 가 없는 Actions 러너에서 설치 자체가 느려지거나
실패해 파이프라인 전체가 막힐 위험이 있다. 로컬 GPU 머신에서 이 엔진을 쓰려면 직접
`pip install voxcpm soundfile` 를 실행해야 한다.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

# VoxCPM 의 "Voice Design" 은 별도 API 파라미터가 아니라, 텍스트 맨 앞에 괄호로 묘사를
# 붙이는 방식이다. 매 문장마다 이 태그를 자동으로 앞에 붙여 목소리 톤을 고정한다.
VOICE_DESIGN_TAG = "(차분하고 무게감 있는 30대 남성 다큐멘터리 성우 목소리, 진지하고 긴장감 넘치는 톤)"

DEFAULT_MODEL_ID = "openbmb/VoxCPM2"


class VoxCPMEngine:
    name = "voxcpm"

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        cfg_value: float = 2.0,
        inference_timesteps: int = 10,
    ):
        self.model_id = model_id
        self.cfg_value = cfg_value
        self.inference_timesteps = inference_timesteps
        self._model = None
        self._load_failed = False

    @property
    def enabled(self) -> bool:
        # 실제 로딩 가능 여부는 GPU/CUDA 환경에 달려 있어 호출 시점에만 알 수 있다 —
        # 여기서 미리 임포트를 시도하지 않는다(Supertonic 과 같은 설계).
        return True

    def _ensure_model(self) -> None:
        if self._model is not None or self._load_failed:
            return
        try:
            from voxcpm import VoxCPM

            self._model = VoxCPM.from_pretrained(self.model_id, load_denoiser=False)
        except Exception as exc:
            logger.warning(
                f"[voxcpm] 로컬 모델 로딩 실패({exc}) — GPU/CUDA 환경이 아니거나 "
                "voxcpm 이 설치되지 않았을 수 있습니다. 다음 엔진으로 폴백합니다"
            )
            self._load_failed = True

    def synthesize(self, text: str, rate: str, out_path: Path) -> bool:
        self._ensure_model()
        if self._model is None:
            return False

        try:
            import soundfile as sf

            wav = self._model.generate(
                text=f"{VOICE_DESIGN_TAG}{text}",
                cfg_value=self.cfg_value,
                inference_timesteps=self.inference_timesteps,
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(out_path), wav, self._model.tts_model.sample_rate)
        except Exception as exc:
            logger.warning(f"[voxcpm] 추론 실패({exc}) — 다음 엔진으로 폴백합니다")
            return False

        return out_path.exists() and out_path.stat().st_size > 0
