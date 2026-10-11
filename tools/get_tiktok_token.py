"""틱톡 Content Posting API용 리프레시 토큰을 최초 1회 발급받는다.

사용법:
    1) https://developers.tiktok.com 에서 앱을 만들고 "Content Posting API" 제품을
       추가한다. client_key/client_secret 을 확인한다.
    2) pip install requests (이미 requirements.txt 에 있다)
    3) python tools/get_tiktok_token.py <client_key> <client_secret>
    4) 브라우저가 열리면 업로드할 틱톡 계정으로 로그인·동의한다.
    5) 인가가 끝나면 코드가 적힌 페이지로 이동한다 — 그 코드를 복사해서
       터미널에 붙여넣는다.
    6) 리프레시 토큰이 출력된다 — 이 값이 "GitHub Secrets에 넣을 값"이다.

이 틱톡 앱에는 별도의 "Redirect URI" 입력칸이 없다 — Basic information 의
"Web/Desktop URL"과 **완전히 똑같은 주소**만 redirect_uri 로 허용된다(도메인만
같아도 안 되고 경로까지 1바이트도 다르면 안 된다). 그래서 그 주소 그대로 쓰고,
로그인이 끝나면 브라우저 주소창에 "?code=..."가 붙은 채로 그 페이지가 뜬다 —
그 code 값을 사람이 주소창에서 직접 복사해 붙여넣는 방식을 쓴다.

리프레시 토큰은 쓸 때마다 새로 발급되므로(코드가 자동으로 캐시 파일에 최신 값을
저장한다), 여기서 받는 값은 "최초 시드"일 뿐이다.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sys
from urllib.parse import unquote, urlencode

import requests

REDIRECT_URI = "https://5479mk-bit.github.io/"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
# video.upload = 초안함(Inbox) 업로드에 필요한 권한(지금 쓰는 방식).
# video.publish = 다이렉트 포스트(바로 게시)에 필요한 권한 — 나중에 심사 통과 후
# 그쪽으로 바꿀 걸 대비해 같이 받아둔다.
SCOPES = "video.upload,video.publish"


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


def main() -> None:
    if len(sys.argv) != 3:
        print("사용법: python get_tiktok_token.py <client_key> <client_secret>")
        raise SystemExit(1)
    client_key, client_secret = sys.argv[1], sys.argv[2]
    code_verifier, code_challenge = _pkce_pair()

    query = urlencode(
        {
            "client_key": client_key,
            "scope": SCOPES,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "state": "get_token",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    auth_url = f"{AUTH_URL}?{query}"
    print(f"아래 주소를 브라우저로 열고, 업로드할 틱톡 계정으로 로그인·동의하세요:\n{auth_url}\n")
    print(
        "동의가 끝나면 브라우저 주소창에 '...?code=XXXX&state=...' 가 붙은 채로 "
        "우리 사이트가 뜹니다 — 그 'code=' 뒤에서 '&' 앞까지만 복사하세요."
    )

    received_code = unquote(input("code 값을 여기에 붙여넣으세요: ").strip())
    if not received_code:
        print("코드가 비어 있습니다.")
        raise SystemExit(1)

    response = requests.post(
        TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": received_code,
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
