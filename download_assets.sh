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
# 나눔명조에도 프리텐다드에도 **한자가 한 글자도 없다**(직접 확인함). 사료 컷은 실록 원문을
# 한자로 띄우는 게 전부라 한자가 있는 명조가 따로 필요하다. 노토 세리프 KR 은 23,000자를
# 담고 있어 실록 원문에 나오는 한자를 거의 다 덮는다(23MB).
fetch "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifkr/NotoSerifKR%5Bwght%5D.ttf" \
      "assets/fonts/NotoSerifKR.ttf"
# 노토 세리프 KR 로도 실록의 벽자(예: 壼)는 빠진다. 한자만 담당할 보조 폰트를 하나 더 둔다
# (한글은 없고 한자는 3만 자). 글자마다 있는 폰트를 골라 그린다.
fetch "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf" \
      "assets/fonts/NotoSerifSC.ttf"

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
  # 충격적 사실·전환에 때리는 묵직한 타격음. 저음(80Hz) 몸통에 아주 짧은 고역
  # 클릭(타격 순간의 "딱")을 얹어야 진짜 임팩트처럼 들린다 — 저음 하나만 있으면
  # 뭉툭하고 "짜치게" 들린다는 지적이 있었다.
  "$FFMPEG" -y \
    -f lavfi -i "sine=frequency=80:duration=1.5" \
    -f lavfi -i "anoisesrc=d=0.05:c=white:a=1.0" \
    -filter_complex "[0]afade=t=out:st=0.1:d=1.4,lowpass=f=110,volume=1.5[boom];\
[1]highpass=f=2500,afade=t=out:st=0.015:d=0.04,volume=0.9[click];\
[boom][click]amix=inputs=2:duration=first:dropout_transition=0" \
    assets/audio/sub_boom.wav -loglevel error
  echo "    만듦: assets/audio/sub_boom.wav"
fi
if have assets/audio/paper_rustle.wav; then
  echo "    건너뜀 (이미 있음): assets/audio/paper_rustle.wav"
else
  # 사료(HISTORICAL_DOC) 컷 전용 — 종이 넘기는 소리. sub_boom 을 재활용하던 걸
  # 분리했다. 화이트노이즈를 고역대만 남기고 짧게 감쌌다.
  "$FFMPEG" -y -f lavfi -i "anoisesrc=d=0.6:c=white:a=0.6" \
    -af "highpass=f=1200,lowpass=f=6000,afade=t=in:st=0:d=0.05,afade=t=out:st=0.35:d=0.25,volume=0.8" \
    assets/audio/paper_rustle.wav -loglevel error
  echo "    만듦: assets/audio/paper_rustle.wav"
fi
if have assets/audio/braam.wav; then
  echo "    건너뜀 (이미 있음): assets/audio/braam.wav"
else
  # 1부 마지막 클리프행어 전용 — sub_boom 보다 낮고 길게, 여운(에코)을 크게 준다.
  "$FFMPEG" -y -f lavfi -i "sine=frequency=42:duration=2.6" \
    -af "afade=t=out:st=0.2:d=2.4,lowpass=f=95,aecho=0.8:0.9:600:0.5,volume=1.6" \
    assets/audio/braam.wav -loglevel error
  echo "    만듦: assets/audio/braam.wav"
fi
# 아래 세 개는 "효과음이 sub_boom/braam 뿐이라 밋밋하다"는 지적에 맞춰 추가했다 —
# 화면 내용에 맞는 효과음을 고를 수 있게 갈래를 늘린다(구조물 균열/물·잔해 쏟아짐/
# 경보). 전부 sub_boom 과 같은 방식(ffmpeg 합성)이라 저작권 걱정이 없다.
if have assets/audio/crack.wav; then
  echo "    건너뜀 (이미 있음): assets/audio/crack.wav"
else
  # 구조물이 갈라지는 순간 — 날카로운 노이즈 버스트(고역 "딱") 밑에 짧은 저음
  # "퍽"을 깔아 몸집을 준다. 노이즈 하나만 쓰면 라디오 잡음처럼 얇게 들린다.
  "$FFMPEG" -y \
    -f lavfi -i "anoisesrc=d=0.35:c=white:a=0.9" \
    -f lavfi -i "sine=frequency=180:duration=0.3" \
    -filter_complex "[0]highpass=f=1200,afade=t=in:st=0:d=0.005,afade=t=out:st=0.06:d=0.2,volume=1.3[snap];\
[1]lowpass=f=300,afade=t=out:st=0.02:d=0.25,volume=0.5[thud];\
[snap][thud]amix=inputs=2:duration=first:dropout_transition=0" \
    assets/audio/crack.wav -loglevel error
  echo "    만듦: assets/audio/crack.wav"
fi
if have assets/audio/whoosh.wav; then
  echo "    건너뜀 (이미 있음): assets/audio/whoosh.wav"
else
  # 물·잔해가 쏟아지거나 카메라가 빠르게 휩쓸고 지나가는 순간 — 저역에서 고역으로
  # 실제로 올라가는 주파수 스윕(aevalsrc)에 핑크노이즈 질감을 살짝 얹는다.
  # 예전엔 고정 대역폭 밴드패스라 스윕감이 없어 그냥 뭉개진 노이즈로 들렸다.
  "$FFMPEG" -y \
    -f lavfi -i "aevalsrc=0.6*sin(2*PI*(250+900*t)*t):d=0.9" \
    -f lavfi -i "anoisesrc=d=0.9:c=pink:a=0.5" \
    -filter_complex "[0]afade=t=in:st=0:d=0.15,afade=t=out:st=0.55:d=0.35[sweep];\
[1]bandpass=f=1200:width_type=o:w=2,afade=t=in:st=0:d=0.2,afade=t=out:st=0.55:d=0.35[noise];\
[sweep][noise]amix=inputs=2:duration=longest,volume=1.2" \
    assets/audio/whoosh.wav -loglevel error
  echo "    만듦: assets/audio/whoosh.wav"
fi
if have assets/audio/alarm.wav; then
  echo "    건너뜀 (이미 있음): assets/audio/alarm.wav"
else
  # 경보·위험 신호 — 주파수가 위아래로 흔들리는 사이렌형 진동(진짜 경보기에 가깝다).
  # 예전엔 고정음 트레몰로라 장난감 삐삐 소리 같았다.
  "$FFMPEG" -y -f lavfi -i "aevalsrc=0.5*sin(2*PI*(900+300*sin(2*PI*2.2*t))*t):d=1.2" \
    -af "afade=t=in:st=0:d=0.05,afade=t=out:st=1.0:d=0.2,volume=1.0" \
    assets/audio/alarm.wav -loglevel error
  echo "    만듦: assets/audio/alarm.wav"
fi
if have assets/audio/epic_pad.mp3; then
  echo "    건너뜀 (이미 있음): assets/audio/epic_pad.mp3"
else
  # "배경음이 초저음 드론뿐이라 웅장함이 없다"는 지적에 맞춰 추가했다. 단순 사인파
  # 3개로 단조 화음(라-도-미)을 쌓고 아주 느리게 스웰을 준다 — 멜로디가 없어
  # 반복 루프 티가 안 나면서도 드론보다 훨씬 "웅장한 배경"에 가깝다. 특정 곡을
  # 흉내 낸 게 아니라 화음 자체를 새로 합성한 것이라 저작권 문제가 없다.
  # amix 는 기본값(normalize=1)이 입력 개수만큼 나눠 볼륨을 죽인다 — 처음 버전이
  # 그 기본값을 그대로 둬서 만들어진 파일이 평균 -36.7dB(드론보다 14dB 더 조용함)
  # 였다. bgm_volume_db(-16dB)까지 겹치면 사실상 안 들리는 수준이라 "배경음악이
  # 왜 없냐"는 지적으로 이어졌다. normalize=0 으로 감쇄를 끄고 volume 로 직접
  # 게인을 줘서 평균 -19dB 대(드론과 비슷한 존재감)로 맞춘다.
  "$FFMPEG" -y \
    -f lavfi -i "sine=frequency=110:duration=20" \
    -f lavfi -i "sine=frequency=130.81:duration=20" \
    -f lavfi -i "sine=frequency=164.81:duration=20" \
    -filter_complex "[0]volume=0.5[a];[1]volume=0.35[b];[2]volume=0.3[c];\
[a][b][c]amix=inputs=3:duration=longest:normalize=0,tremolo=f=0.1:d=0.25,\
afade=t=in:st=0:d=3,afade=t=out:st=17:d=3,lowpass=f=2200,volume=2.5" \
    assets/audio/epic_pad.mp3 -loglevel error
  echo "    만듦: assets/audio/epic_pad.mp3"
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
