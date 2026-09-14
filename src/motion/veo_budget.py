"""Veo 호출 예산과 캐시.

Veo 는 요청 한 번이 곧 돈이다. 그래서 호출 코드보다 이 파일이 먼저 있어야 한다.
- 같은 (사진, 프롬프트, 파라미터)면 이미 만든 클립을 재사용한다. 재렌더가 공짜가 된다.
- 에피소드/하루 클립 수에 상한을 두고, 넘으면 호출하지 않고 멈춘다.
- 얼마를 썼는지 기록해 승인 요청에 같이 보여준다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from loguru import logger

# 8초 클립 1개 기준 단가(USD/초). 공식 가격표가 바뀌면 여기만 고친다.
TIER_USD_PER_SECOND = {
    "lite": 0.05,
    "fast": 0.15,
    "standard": 0.40,
}
DEFAULT_USD_TO_KRW = 1470


class BudgetExceeded(RuntimeError):
    """상한에 걸려 호출하지 않았다."""


@dataclass
class ClipCost:
    clips: int
    seconds_each: int
    tier: str

    @property
    def usd(self) -> float:
        rate = TIER_USD_PER_SECOND.get(self.tier, TIER_USD_PER_SECOND["lite"])
        return self.clips * self.seconds_each * rate

    def krw(self, usd_to_krw: int = DEFAULT_USD_TO_KRW) -> int:
        return round(self.usd * usd_to_krw)

    def summary(self, usd_to_krw: int = DEFAULT_USD_TO_KRW) -> str:
        return f"Veo {self.tier} {self.clips}클립 · 약 {self.krw(usd_to_krw):,}원"


class VeoBudget:
    def __init__(
        self,
        cache_dir: Path,
        tier: str,
        max_clips_per_episode: int,
        max_clips_per_day: int,
    ):
        self.cache_dir = cache_dir
        self.clip_dir = cache_dir / "clips"
        self.usage_path = cache_dir / "usage.json"
        self.tier = tier
        self.max_clips_per_episode = max_clips_per_episode
        self.max_clips_per_day = max_clips_per_day
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        self.episode_clips = 0

    def cache_key(self, image_path: Path, prompt: str, params: dict) -> str:
        digest = hashlib.sha256()
        digest.update(image_path.read_bytes())
        digest.update(prompt.encode("utf-8"))
        digest.update(json.dumps(params, sort_keys=True).encode("utf-8"))
        return digest.hexdigest()[:20]

    def cached_clip(self, key: str) -> Path | None:
        path = self.clip_dir / f"{key}.mp4"
        return path if path.exists() and path.stat().st_size > 0 else None

    def clip_path(self, key: str) -> Path:
        return self.clip_dir / f"{key}.mp4"

    def ensure_can_generate(self) -> None:
        """호출 직전에 상한을 확인한다. 캐시 적중은 여기까지 오지 않는다."""
        if self.episode_clips >= self.max_clips_per_episode:
            raise BudgetExceeded(
                f"이번 편에서 이미 {self.episode_clips}클립을 생성했습니다 "
                f"(상한 {self.max_clips_per_episode})"
            )
        today = self._today_usage()
        if today >= self.max_clips_per_day:
            raise BudgetExceeded(
                f"오늘 이미 {today}클립을 생성했습니다 (상한 {self.max_clips_per_day})"
            )

    def record(self, clips: int = 1) -> None:
        self.episode_clips += clips
        usage = self._load_usage()
        key = date.today().isoformat()
        usage[key] = usage.get(key, 0) + clips
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        self.usage_path.write_text(json.dumps(usage, indent=2), encoding="utf-8")
        logger.info(f"Veo 사용량: 이번 편 {self.episode_clips}클립 / 오늘 {usage[key]}클립")

    def episode_cost(self, seconds_each: int) -> ClipCost:
        return ClipCost(clips=self.episode_clips, seconds_each=seconds_each, tier=self.tier)

    def _today_usage(self) -> int:
        return self._load_usage().get(date.today().isoformat(), 0)

    def _load_usage(self) -> dict[str, int]:
        if not self.usage_path.exists():
            return {}
        try:
            return json.loads(self.usage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
