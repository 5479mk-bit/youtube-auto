#!/usr/bin/env bash
# 다크 시네마틱 렌더에 필요한 고정 에셋을 한 번에 준비한다.
#
# 여기서 만들어지는 것은 전부 `assets/` 안에 들어가고, 파이프라인은 이 경로만 본다.
# 여러 번 실행해도 안전하다 — 이미 있는 파일은 건너뛴다(`--force` 로 다시 받는다).
#
# 주의: 원래 받으려던 위키미디어 파일 두 개는 실제로 존재하지 않았다
#   - Old_paper_texture.jpg  → 커먼즈에 없는 파일. 한지 텍스처는 직접 생성한다.
#   - Daedongyeojido.jpg     → 없는 파일명. 실제 파일은 Daedongyeojido-small.png 다
#                              (1177x1977, 세로 비율이 쇼츠와 맞아 오히려 낫다).
# 커먼즈는 요청이 몰리면 429 로 막으므로 재시도를 넣었다.

set -euo pipefail

cd "$(dirname "$0")"

FORCE="${1:-}"
FFMPEG="$(python3 -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"

have() { [ -s "$1" ] && [ "$FORCE" != "--force" ]; }

# 내려받기는 파이썬으로 한다 — upload.wikimedia.org 가 curl 요청만 429 로 막는 일이
# 잦았다(같은 URL 이 requests 로는 200).
fetch() {  # fetch <url> <dest>
  local url="$1" dest="$2"
  if have "$dest"; then echo "    건너뜀 (이미 있음): $dest"; return 0; fi
  python3 tools/fetch_asset.py "$url" "$dest"
}

echo ">>> [1/5] 디렉토리 구조 생성"
mkdir -p assets/fonts assets/textures assets/maps assets/overlays assets/audio

echo ">>> [2/5] 폰트 (나눔명조 Bold = 사료체, 프리텐다드 Bold = 자막체)"
fetch "https://raw.githubusercontent.com/google/fonts/main/ofl/nanummyeongjo/NanumMyeongjo-Bold.ttf" \
      "assets/fonts/NanumMyeongjo-Bold.ttf"
fetch "https://cdn.jsdelivr.net/gh/orioncactus/pretendard/packages/pretendard/dist/public/static/Pretendard-Bold.otf" \
      "assets/fonts/Pretendard-Bold.otf"

echo ">>> [3/5] 한지 텍스처(생성) + 대동여지도(위키미디어, 퍼블릭 도메인)"
if have assets/textures/hanji.jpg; then
  echo "    건너뜀 (이미 있음): assets/textures/hanji.jpg"
else
  python3 tools/make_paper_texture.py assets/textures/hanji.jpg
fi
fetch "https://upload.wikimedia.org/wikipedia/commons/3/34/Daedongyeojido-small.png" \
      "assets/maps/daedong_map.png"

echo ">>> [4/5] 미스터리 오디오 (ffmpeg 합성)"
if have assets/audio/dark_ambient_drone.mp3; then
  echo "    건너뜀 (이미 있음): assets/audio/dark_ambient_drone.mp3"
else
  # 45Hz 초저음 룸블링. 말 밑에 깔려 긴장만 만든다(멜로디 없음).
  "$FFMPEG" -y -f lavfi -i "sine=frequency=45:duration=15" \
    -af "lowpass=f=120,volume=0.9,aecho=0.8:0.88:1000:0.4" \
    assets/audio/dark_ambient_drone.mp3 -loglevel error
  echo "    만듦: assets/audio/dark_ambient_drone.mp3"
fi
if have assets/audio/sub_boom.wav; then
  echo "    건너뜀 (이미 있음): assets/audio/sub_boom.wav"
else
  # 충격적 사실·전환에 때리는 묵직한 타격음.
  "$FFMPEG" -y -f lavfi -i "sine=frequency=80:duration=1.5" \
    -af "afade=t=out:st=0.1:d=1.4,lowpass=f=110,volume=1.5" \
    assets/audio/sub_boom.wav -loglevel error
  echo "    만듦: assets/audio/sub_boom.wav"
fi

echo ">>> [5/5] 밤안개 오버레이 (합성 시 확대해 쓰므로 절반 해상도로 만든다)"
if have assets/overlays/fog.mp4; then
  echo "    건너뜀 (이미 있음): assets/overlays/fog.mp4"
else
  # 1080x1920 으로 만들면 픽셀별 수식 계산이라 40초 가까이 걸린다. 어차피 강하게
  # 흐려서 쓰는 레이어라 540x960 으로 만들고 합성 때 늘린다 — 눈으로 구분이 안 된다.
  "$FFMPEG" -y -f lavfi -i "nullsrc=s=540x960:d=8:r=30" \
    -vf "geq=r='(sin(X/50+T)*cos(Y/50+T*0.7)+1)*60':g='(sin(X/50+T)*cos(Y/50+T*0.7)+1)*60':b='(sin(X/50+T)*cos(Y/50+T*0.7)+1)*62',boxblur=20:3" \
    -c:v libx264 -pix_fmt yuv420p assets/overlays/fog.mp4 -loglevel error
  echo "    만듦: assets/overlays/fog.mp4"
fi

echo "✅ 모든 에셋 준비 완료 (assets/)"
find assets -type f | sort | sed 's/^/    /'
