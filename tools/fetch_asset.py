"""에셋 파일 하나를 재시도하며 내려받는다.

`curl` 로도 되게 만들려 했는데 upload.wikimedia.org 가 curl 요청만 429 로 막는 일이
잦았다(같은 URL 이 파이썬 requests 로는 200). 파이프라인이 이미 requests 를 쓰므로
내려받기도 같은 클라이언트로 통일한다.

    python tools/fetch_asset.py <url> <dest>
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

HEADERS = {
    "User-Agent": (
        "youtube-auto/0.2 (joseon mystery shorts; "
        "https://github.com/5479mk-bit/youtube-auto)"
    ),
    "Accept": "*/*",
}
MAX_ATTEMPTS = 5
TIMEOUT = 180


def download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            dest.write_bytes(response.content)
            print(f"    받음: {dest} ({len(response.content) / 1024:.0f}KB)")
            return True
        except Exception as exc:
            print(f"    재시도 {attempt}/{MAX_ATTEMPTS}: {exc}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(attempt * 3)
    return False


def main() -> int:
    url, dest = sys.argv[1], Path(sys.argv[2])
    return 0 if download(url, dest) else 1


if __name__ == "__main__":
    raise SystemExit(main())
