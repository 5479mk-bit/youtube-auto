"""YouTube 업로드용 OAuth token.json 을 최초 1회 발급받는다.

사용법:
    1) 2단계에서 받은 client_secret_....json 을 이 스크립트와 같은 폴더에 둔다.
    2) pip install google-auth-oauthlib google-api-python-client
    3) python get_youtube_token.py client_secret_....json
    4) 브라우저가 열리면 업로드할 유튜브 채널 계정으로 로그인·동의한다.
    5) 같은 폴더에 token.json 이 생긴다.

이 token.json 안에는 refresh_token 이 들어있다 — 이게 곧 "GitHub Secrets에 넣을 값"이다.
절대 이 파일을 커밋하거나 공개된 곳에 올리지 않는다.
"""

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    if len(sys.argv) != 2:
        print("사용법: python get_youtube_token.py client_secret_....json")
        raise SystemExit(1)

    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    credentials = flow.run_local_server(port=0)

    with open("token.json", "w", encoding="utf-8") as handle:
        handle.write(credentials.to_json())

    print("\ntoken.json 을 만들었습니다.")
    print("이제 이 두 파일의 '내용 전체'를 각각 GitHub Secrets 에 넣어주세요:")
    print(f"  1) {sys.argv[1]} 의 전체 내용 → 시크릿 이름: YOUTUBE_CLIENT_SECRET_JSON")
    print("  2) token.json 의 전체 내용 → 시크릿 이름: YOUTUBE_TOKEN_JSON")
    print("(저장소 Settings > Secrets and variables > Actions > New repository secret)")


if __name__ == "__main__":
    main()
