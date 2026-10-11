"""틱톡 Content Posting API용 리프레시 토큰을 최초 1회 발급받는다.

사용법:
    1) https://developers.tiktok.com 에서 앱을 만들고 "Content Posting API" 제품을
       추가한 뒤, client_key/client_secret 을 확인한다.
    2) 앱의 리다이렉트 URI를 http://localhost:8787/callback 으로 등록한다.
    3) pip install requests (이미 requirements.txt 에 있다)
    4) python tools/get_tiktok_token.py <client_key> <client_secret>
    5) 브라우저가 열리면 업로드할 틱톡 계정으로 로그인·동의한다.
    6) 리프레시 토큰이 출력된다 — 이 값이 "GitHub Secrets에 넣을 값"이다.

리프레시 토큰은 쓸 때마다 새로 발급되므로(코드가 자동으로 캐시 파일에 최신 값을
저장한다), 여기서 받는 값은 "최초 시드"일 뿐이다.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import requests

REDIRECT_URI = "http://localhost:8787/callback"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
SCOPES = "video.publish"

_received_code: str | None = None


def _pkce_pair() -> tuple[str, str]:
    # 틱톡이 2024년부터 PKCE를 요구한다 — code_verifier(원본)를 들고 있다가
    # 토큰 교환 때 같이 보내고, 인가 URL에는 그 해시(code_challenge)만 보낸다.
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server 관례)
        global _received_code
        query = parse_qs(urlparse(self.path).query)
        _received_code = query.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write("인가 완료 — 이 창은 닫아도 됩니다.".encode("utf-8"))


def main() -> None:
    if len(sys.argv) != 3:
        print("사용법: python get_tiktok_token.py <client_key> <client_secret>")
        raise SystemExit(1)
    client_key, client_secret = sys.argv[1], sys.argv[2]
    code_verifier, code_challenge = _pkce_pair()

    auth_url = (
        f"{AUTH_URL}?client_key={client_key}&scope={SCOPES}&response_type=code"
        f"&redirect_uri={REDIRECT_URI}&state=get_token"
        f"&code_challenge={code_challenge}&code_challenge_method=S256"
    )
    print(f"브라우저가 안 열리면 이 주소를 직접 여세요:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", 8787), _CallbackHandler)
    server.handle_request()

    if not _received_code:
        print("인가 코드를 받지 못했습니다.")
        raise SystemExit(1)

    response = requests.post(
        TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": _received_code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    print("\n리프레시 토큰을 받았습니다.")
    print("이제 다음 값들을 GitHub Secrets 에 넣어주세요:")
    print(f"  TIKTOK_CLIENT_KEY = {client_key}")
    print(f"  TIKTOK_CLIENT_SECRET = {client_secret}")
    print(f"  TIKTOK_REFRESH_TOKEN_SEED = {data['refresh_token']}")
    print("(저장소 Settings > Secrets and variables > Actions > New repository secret)")


if __name__ == "__main__":
    main()
