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
        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
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
                if not data.startswith(f"{task_id}:"):
                    continue

                action = data.split(":", 1)[1]
                self._call(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": query["id"],
                        "text": "승인했습니다" if action.startswith("a") else "거절했습니다",
                    },
                )
                if action.startswith("a"):
                    return ReviewDecision(approved=True, title_index=int(action[1:] or 0))
                return ReviewDecision(approved=False)

        logger.warning("검수 응답을 기다리다 시간이 초과되었습니다")
        return None

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
            logger.warning(f"영상 전송 실패({path.name}): {response.text[:200]}")

    def _call(self, method: str, payload: dict, timeout: int = 30) -> dict:
        response = self.session.post(
            f"{API_BASE}{self.bot_token}/{method}", json=payload, timeout=timeout
        )
        response.raise_for_status()
        return response.json()


def build_summary(title_seed: str, titles: list[str], descriptions: str, is_sample: bool) -> str:
    lines = [f"<b>{title_seed}</b>", "", "<b>제목 후보</b>"]
    lines.extend(f"{index + 1}. {title}" for index, title in enumerate(titles))
    lines.extend(["", "<b>설명</b>", descriptions])
    if is_sample:
        lines.insert(0, "⚠️ 샘플 데이터로 만든 영상입니다. 업로드가 차단됩니다.")
    return "\n".join(lines)
