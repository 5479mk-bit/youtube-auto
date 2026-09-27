"""텔레그램 검수 게이트.

python-telegram-bot 대신 Bot HTTP API 를 직접 쓴다. 필요한 건 "영상 두 편과 제목
후보를 보내고, 버튼 하나를 기다리는 것"뿐이라 의존성을 더 늘릴 이유가 없다.

승인 버튼은 제목 후보마다 하나씩 만든다. 사람이 제목을 고르는 순간이 곧 승인이다.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from loguru import logger

API_BASE = "https://api.telegram.org/bot"
POLL_TIMEOUT = 50
# 텔레그램 봇 API 의 sendVideo 크기 상한은 50MB 다(413 Request Entity Too Large 로
# 거부된다). 합본 영상이 목표 길이(63~90초)를 넘겨 커지면 검수 자체가 불가능해지므로
# 여유를 두고 이 값을 넘으면 화질을 낮춘 사본을 대신 보낸다.
TELEGRAM_MAX_VIDEO_BYTES = 48 * 1024 * 1024


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
            # 텔레그램 롱폴 자체가 caller 가 정한 timeout_seconds 보다 길면(예:
            # review-pending 은 --wait 30 인데 POLL_TIMEOUT 은 50) 요청 하나가
            # 남은 시간을 넘겨서 끝나고, 클라이언트 타임아웃(구 코드는 +10초 여유만
            # 뒀다)과 거의 같은 시점에 걸려 ReadTimeout 이 나며 프로세스 전체가
            # 죽었다("승인을 눌러도 반응 없음"의 실제 원인 중 하나였다). 서버
            # 롱폴 시간을 남은 시간 안으로 잘라 요청 하나가 deadline 을 넘기지
            # 않게 하고, 클라이언트 타임아웃 여유도 넉넉히(30초) 둔다.
            remaining_seconds = max(deadline - time.time(), 1.0)
            poll_timeout = max(1, min(POLL_TIMEOUT, int(remaining_seconds)))
            try:
                updates = self._call(
                    "getUpdates",
                    {
                        "timeout": poll_timeout,
                        "offset": self._offset,
                        "allowed_updates": ["callback_query"],
                    },
                    timeout=poll_timeout + 30,
                )
            except Exception as exc:
                # 네트워크가 일시적으로 흔들린 것뿐일 수 있다 — 루프를 죽이지 않고
                # deadline 안에서 다시 시도한다.
                logger.warning(f"getUpdates 일시 실패(재시도): {exc}")
                continue
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
                try:
                    self._call(
                        "answerCallbackQuery",
                        {
                            "callback_query_id": query["id"],
                            "text": "승인했습니다" if action.startswith("a") else "거절했습니다",
                        },
                    )
                except Exception as exc:
                    # answerCallbackQuery 는 사용자 폰의 로딩 스피너를 꺼주는 것뿐인
                    # UI 피드백이다 — 콜백이 폴링 지연으로 늦게 도착하면 텔레그램이
                    # "query is too old"로 거부하는 일이 실제로 있었다("승인을
                    # 눌렀는데 업로드가 안 됐다"는 문의의 원인이었다: 이 호출이
                    # raise_for_status()로 예외를 던지면 아래 승인 기록 자체가
                    # 실행되지 못하고 프로세스가 죽었다). 이 알림 자체가 실패해도
                    # 실제 승인/거절 판정과 업로드는 그대로 진행돼야 한다.
                    logger.warning(f"answerCallbackQuery 실패(무시하고 계속 진행): {exc}")
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
        send_path = path
        compressed: Path | None = None
        if path.stat().st_size > TELEGRAM_MAX_VIDEO_BYTES:
            logger.warning(
                f"{path.name} 이(가) 텔레그램 크기 제한(48MB)을 넘어 검수용 압축본을 만듭니다"
                f" ({path.stat().st_size / 1_048_576:.1f}MB) — 실제 업로드는 원본을 그대로 씁니다"
            )
            compressed = _compress_for_telegram(path)
            if compressed:
                send_path = compressed
                caption = f"{caption} (검수용 저화질 — 실제 업로드는 원본)"
            else:
                logger.warning(f"{path.name} 압축본 생성 실패 — 원본으로 그대로 시도합니다")

        try:
            with send_path.open("rb") as handle:
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
        finally:
            if compressed is not None and compressed.exists():
                compressed.unlink()

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


def _compress_for_telegram(path: Path) -> Path | None:
    """텔레그램 검수용으로만 화질을 낮춘 사본을 만든다. 유튜브 업로드는 원본을 쓴다.

    720p·800kbps 로 인코딩하면 90초짜리도 10MB 안팎이라 48MB 상한에 넉넉히 들어온다.
    """
    import imageio_ffmpeg

    out_path = path.with_name(f"{path.stem}_tg{path.suffix}")
    try:
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-i",
                str(path),
                "-vf",
                "scale=720:-2",
                "-b:v",
                "800k",
                "-maxrate",
                "900k",
                "-bufsize",
                "1200k",
                "-b:a",
                "96k",
                "-preset",
                "veryfast",
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
    except Exception as exc:
        logger.warning(f"텔레그램용 압축 실패({path.name}): {exc}")
        return None
    return out_path if out_path.exists() else None


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
