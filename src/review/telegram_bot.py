"""텔레그램 검수 게이트.

python-telegram-bot 대신 Bot HTTP API 를 직접 쓴다. 필요한 건 "영상 두 편과 제목
후보를 보내고, 버튼 하나를 기다리는 것"뿐이라 의존성을 더 늘릴 이유가 없다.

승인 버튼은 제목 후보마다 하나씩 만든다. 사람이 제목을 고르는 순간이 곧 승인이다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import requests
from loguru import logger

API_BASE = "https://api.telegram.org/bot"
POLL_TIMEOUT = 50


@dataclass
class ReviewDecision:
    approved: bool
    title_index: int = 0


class TelegramReviewer:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.session = requests.Session()
        self._offset: int | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_review(
        self, task_id: str, summary: str, titles: list[str], videos: list[Path]
    ) -> None:
        for index, video in enumerate(videos, start=1):
            self._send_video(video, caption=f"{index}부")

        keyboard = [
            [{"text": f"✅ 승인 · {title[:40]}", "callback_data": f"{task_id}:a{index}"}]
            for index, title in enumerate(titles)
        ]
        keyboard.append([{"text": "❌ 거절", "callback_data": f"{task_id}:r"}])

        self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": summary,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": keyboard},
            },
        )

    def wait_for_decision(self, task_id: str, timeout_seconds: int) -> ReviewDecision | None:
        return self.wait_for_decisions([task_id], timeout_seconds).get(task_id)

    def wait_for_decisions(
        self, task_ids: list[str], timeout_seconds: int
    ) -> dict[str, ReviewDecision]:
        """여러 회차의 응답을 한 번에 기다린다.

        회차 하나씩 따로 기다리면(wait_for_decision 반복 호출) 먼저 기다리는 회차를
        찾다가 다른 회차의 콜백을 그냥 지나쳐버린다 — getUpdates 는 offset 을 넘긴
        업데이트를 다시 안 돌려주므로, 그 회차는 실제로 눌렀는데도 나중엔 응답을 놓친
        것처럼 보인다. 그래서 대기 중인 회차 전부를 한 번의 폴링 루프에서 같이 본다.
        """
        remaining = set(task_ids)
        resolved: dict[str, ReviewDecision] = {}
        deadline = time.time() + timeout_seconds

        while remaining and time.time() < deadline:
            updates = self._call(
                "getUpdates",
                {
                    "timeout": POLL_TIMEOUT,
                    "offset": self._offset,
                    "allowed_updates": ["callback_query"],
                },
                timeout=POLL_TIMEOUT + 10,
            )
            for update in updates.get("result", []):
                self._offset = update["update_id"] + 1
                query = update.get("callback_query")
                if not query:
                    continue

                data = str(query.get("data", ""))
                matched = next((t for t in remaining if data.startswith(f"{t}:")), None)
                if matched is None:
                    # 대기 중인 회차 중 어느 것과도 안 맞는 콜백이다 — 오래된 카드를
                    # 눌렀거나, task_id 가 안 맞는 경우다. "버튼을 눌렀는데 반응이
                    # 없다"는 문의가 왔을 때 원인을 바로 보려고 남긴다(응답 자체는
                    # 확인만 하고 승인 처리는 하지 않는다 — 어느 회차인지 모른다).
                    logger.warning(
                        f"대기 중인 회차({', '.join(sorted(remaining))})와 안 맞는 "
                        f"콜백을 받았습니다: {data!r}"
                    )
                    continue

                action = data.split(":", 1)[1]
                self._call(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": query["id"],
                        "text": "승인했습니다" if action.startswith("a") else "거절했습니다",
                    },
                )
                resolved[matched] = (
                    ReviewDecision(approved=True, title_index=int(action[1:] or 0))
                    if action.startswith("a")
                    else ReviewDecision(approved=False)
                )
                remaining.discard(matched)

        if remaining:
            logger.warning(f"검수 응답을 기다리다 시간이 초과되었습니다: {', '.join(remaining)}")
        return resolved

    def notify(self, text: str) -> None:
        self._call("sendMessage", {"chat_id": self.chat_id, "text": text})

    def _send_video(self, path: Path, caption: str) -> None:
        with path.open("rb") as handle:
            response = self.session.post(
                f"{API_BASE}{self.bot_token}/sendVideo",
                data={"chat_id": self.chat_id, "caption": caption},
                files={"video": handle},
                timeout=300,
            )
        if not response.ok:
            logger.warning(
                f"영상 전송 실패({path.name}) {response.status_code}: {_describe(response)}"
            )

    def _call(self, method: str, payload: dict, timeout: int = 30) -> dict:
        response = self.session.post(
            f"{API_BASE}{self.bot_token}/{method}", json=payload, timeout=timeout
        )
        if not response.ok:
            # 상태 코드만으로는 원인을 알 수 없다. 400 하나에 "chat not found"(챗ID가
            # 틀림)와 "Unauthorized"(토큰이 틀림)가 다 섞여 있는데, 어느 쪽인지는
            # 응답 본문의 description 에만 적혀 있다. 토큰·챗ID 값은 찍지 않는다.
            logger.warning(
                f"텔레그램 {method} 실패 {response.status_code}: {_describe(response)}"
            )
        response.raise_for_status()
        return response.json()


def _describe(response) -> str:
    """텔레그램이 알려준 실패 사유. 본문이 JSON 이 아니면 앞부분만 그대로 돌려준다."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    return str(body.get("description") or body)[:200]


def build_summary(
    title_seed: str,
    titles: list[str],
    descriptions: str,
    is_sample: bool,
    cost_note: str = "",
) -> str:
    lines = [f"<b>{title_seed}</b>", "", "<b>제목 후보</b>"]
    lines.extend(f"{index + 1}. {title}" for index, title in enumerate(titles))
    lines.extend(["", "<b>설명</b>", descriptions])
    if cost_note:
        # 승인 버튼을 누르기 전에 이번 편이 돈을 얼마나 썼는지 보이게 한다.
        lines.extend(["", f"<b>비용</b> {cost_note}"])
    if is_sample:
        lines.insert(0, "⚠️ 샘플 데이터로 만든 영상입니다. 업로드가 차단됩니다.")
    return "\n".join(lines)
