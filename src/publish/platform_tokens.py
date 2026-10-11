"""틱톡/인스타그램처럼 호출마다(또는 주기적으로) 값이 바뀌는 토큰을 캐시 파일에
보관한다.

GitHub Secrets 는 워크플로가 스스로 고쳐 쓸 수 없다 — 그래서 회전하는 토큰은
Secrets가 아니라, 렌더 산출물(state.sqlite3 등)과 같은 캐시 키로 같이
저장/복원되는 이 파일에 둔다. Secrets 에 있는 값은 "최초 시드"로만 쓰고, 그
다음부터는 이 캐시 파일의 최신 값을 우선한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import settings as settings_module

_TOKENS_PATH = settings_module.CACHE_DIR / "platform_tokens.json"


def _load() -> dict:
    if not _TOKENS_PATH.exists():
        return {}
    try:
        return json.loads(_TOKENS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get(key: str, default: str = "") -> str:
    return _load().get(key, default)


def set(key: str, value: str) -> None:
    data = _load()
    data[key] = value
    _TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKENS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
