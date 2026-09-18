"""생성 비용 원장. 크레딧을 다 쓰면 **호출 자체를 멈춘다.**

Veo·Imagen 은 부를 때마다 돈이 나간다. 크레딧이 바닥난 뒤에도 계속 부르면 그때부터는
카드로 청구된다 — 이 파일이 그 선을 지킨다.

한 가지는 분명히 해둔다. **여기 적히는 금액은 우리가 계산한 추정치다.** 구글이 실제로
얼마를 매겼는지는 GCP 청구서만 안다(크레딧 잔액을 알려주는 API 가 없다). 그래서 이
원장은 "쓴 만큼 더해 상한에서 멈추는" 안전장치이지 회계 장부가 아니다. GCP 콘솔의
예산 알림도 같이 걸어두는 걸 권한다.

원장은 `.cache/spend_ledger.json` 에 쌓인다 — 재실행해도 누적이 이어진다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger


class CreditsExhausted(RuntimeError):
    """남은 크레딧보다 비싼 호출이라 하지 않았다."""


@dataclass
class SpendEntry:
    when: str
    kind: str  # "veo" | "image"
    krw: int
    note: str


class SpendGuard:
    def __init__(
        self,
        ledger_path: Path,
        credit_krw: int,
        reserve_krw: int = 5_000,
        enabled: bool = True,
    ):
        self.ledger_path = ledger_path
        self.credit_krw = int(credit_krw)
        # 추정치가 실제보다 낮게 잡힐 수 있어 여유를 남긴다. 이 금액은 안 쓴다.
        self.reserve_krw = int(reserve_krw)
        self.enabled = enabled

    @property
    def spent_krw(self) -> int:
        return int(self._load().get("total_krw", 0))

    @property
    def usable_krw(self) -> int:
        return max(self.credit_krw - self.reserve_krw, 0)

    @property
    def remaining_krw(self) -> int:
        return max(self.usable_krw - self.spent_krw, 0)

    def ensure_can_spend(self, krw: int, note: str = "") -> None:
        """호출 직전에 부른다. 남은 금액으로 감당 못 하면 예외를 던진다."""
        if not self.enabled:
            return
        if krw <= self.remaining_krw:
            return
        raise CreditsExhausted(
            f"크레딧이 부족해 생성을 멈춥니다 — 남은 {self.remaining_krw:,}원 / "
            f"이번 호출 {krw:,}원{f' ({note})' if note else ''}. "
            "추가 과금을 막기 위해 호출하지 않았습니다."
        )

    def record(self, krw: int, kind: str, note: str = "") -> None:
        data = self._load()
        entry = SpendEntry(datetime.now().isoformat(timespec="seconds"), kind, int(krw), note)
        data.setdefault("entries", []).append(entry.__dict__)
        data["total_krw"] = int(data.get("total_krw", 0)) + int(krw)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            f"[지출] {kind} {krw:,}원 · 누적 {data['total_krw']:,}원 / "
            f"남은 추정 {self.remaining_krw:,}원"
        )

    def summary(self) -> str:
        if not self.enabled:
            return "지출 상한: 꺼짐"
        return (
            f"크레딧 {self.credit_krw:,}원 중 추정 {self.spent_krw:,}원 사용 · "
            f"남은 {self.remaining_krw:,}원 (예비 {self.reserve_krw:,}원 제외)"
        )

    def _load(self) -> dict:
        if not self.ledger_path.exists():
            return {}
        try:
            return json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("지출 원장이 깨져 새로 시작합니다")
            return {}
