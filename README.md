# 문화유산 야사 — 국가유산청 비하인드 쇼츠 자동화

국가유산청·한국관광공사 해설문과 위키백과 보강을 엮어, 문화재·유적에 얽힌 야사·비하인드
스토리를 30~40초짜리 세로 쇼츠 **1부/2부**로 자동으로 만든다. 1부는 유적의 기이한 디테일로
열어 설명되지 않는 의문을 남기고, 2부는 학계 해석 또는 전해지는 이야기로 답한다. 사실(공식
해설)과 전설(위키 보강)은 문장에서 항상 구분해서 말한다. 완성된 영상과 후킹 제목 후보는
**텔레그램으로 먼저 전송되고, 사람이 승인해야 유튜브에 올라간다.**

[MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)의 단계 분리 구조를
참고했지만, 스톡 영상을 잇는 대신 **공공 API 실사 사진을 실제로 움직이는 영상으로 만들어**
내레이션을 얹는다.

## 왜 이 방식인가

- 유튜브는 2026년 최우선 과제로 저품질 AI 콘텐츠('AI 슬롭') 정리를 내세웠다. 출처가 검증되는
  국가유산청 해설 기반 콘텐츠는 그 반대편에 선다.
- 한국 쇼츠 시장에 문화유산 야사를 사실/전설 구분까지 신경 쓰며 다루는 채널이 거의 없다.
- 클리프행어 구조라 1부→2부 시청 전환율이 자연히 올라간다.

## 사실 기준

대본은 두 재료 밖의 사실을 지어내지 않는다.

| 재료 | 역할 |
|---|---|
| 국가유산청/관광공사 해설문 (`facts`) | 정식 기록. 대본에서 사실로 단정해 말한다 |
| 위키백과 전설·논란 섹션 (`wiki_excerpt`) | 전해지는 이야기. "전해지는 말로는" 처럼 사실이 아닐 수 있음을 문장에서 드러낸다 |

나무위키는 공식 API가 없고 자동 수집이 이용약관과 충돌할 수 있어 자동 소스에서 제외했다
(수동 참고 자료로만 쓴다).

## 동작 흐름

```
국가유산청 문화재 목록+해설문 수집 → (해설문이 얇으면) 관광공사 overview 보강
   → 위키백과 전설/논란 섹션 보강 → 이야깃거리 스코어링으로 주제 1건 선정
   → 1부/2부 대본(사실·전설 구분) → 공공 API 사진 수집 → TTS 내레이션 → 자막
   → 컷 배치(엔진 배정) → 사진을 움직이는 영상으로 → mp4 2편
   → 텔레그램 검수 → 승인 시 유튜브 업로드
```

## 사진을 움직이는 세 가지 엔진

정지 사진을 훑는 화면이 아니라, 사진 자체가 움직이는 영상을 만든다.

| 엔진 | 무엇을 하나 | 비용 | 한계 |
|---|---|---|---|
| **공식 동영상** (국가유산청) | 문화재 항목에 공식 매핑된 실사 영상을 그대로 씀 | 무료 | 있는 항목에만, 세로로 크롭해야 함(16:9 추정) |
| **Veo** (`veo-3.1-*`) | 사진을 넣으면 사람이 걷고 차가 지나가는 8초 영상을 생성 | 유료 | 8초 단위 과금 |
| **2.5D 파랄랙스** | 깊이를 추정해 카메라가 장면 **안으로** 이동 (가까운 것과 먼 것이 다른 속도로 지나감) | 무료 | 장면 속 사람·차는 안 움직임. 카메라만 이동 |

시청 이탈이 결정되는 **도입부를 실사 영상 하나로 덮고**, 나머지는 파랄랙스로 채운다. 우선순위는
**공식 동영상 → Veo → 파랄랙스**다. 문화재에 국가유산청 공식 동영상이 있으면 그걸 도입부에
바로 쓰고 Veo 호출 자체를 건너뛴다 — 그만큼 과금이 안 나간다. 없을 때만 Veo로 생성한다(8초
단위로 과금되므로 3초만 쓰고 버리지 않도록 컷 배치가 도입부를 통째로 묶는다). 세 경로 모두 같은
그레인·비네트·컬러 그레이드를 입혀 한 카메라로 찍은 것처럼 보이게 한다.

사진도 마찬가지로 문화재 항목에 공식 매핑된 이미지(국가유산청 이미지 API, 식별자 기반 조회)를
그 항목의 다른 어떤 텍스트 검색보다 먼저 쓴다(`pipeline.build_collector`). 국가유산청 공식
나레이션(다국어 음성) API는 지금 대본 말투와 안 맞을 수 있어 이번 범위에서는 쓰지 않는다.

### Veo 비용 (8초 클립 1개, 720p, 환율 1,470원 기준)

| 티어 | 클립 1개 | 한 편 전부(9클립) | 도입부만(2클립) | 하루 1편 월비용(도입부만) |
|---|---|---|---|---|
| `lite` (기본값) | 약 590원 | 약 5,300원 | 약 1,180원 | 약 3.5만원 |
| `fast` | 약 1,760원 | 약 15,900원 | 약 3,500원 | 약 10.6만원 |
| `standard` | 약 4,700원 | 약 42,000원 | 약 9,400원 | 약 28만원 |

`config.yaml` 의 `veo.tier` 와 `veo.clips_per_part` 로 조절한다. `clips_per_part: 0` 이면
전부 무료 파랄랙스로 간다.

**돈이 나가는 경로라 안전장치를 먼저 깔았다.**
- 같은 (사진, 프롬프트, 파라미터)면 이미 만든 클립을 재사용한다 — 재렌더가 공짜
- `veo.max_clips_per_episode`, `veo.max_clips_per_day` 를 넘으면 호출하지 않고 멈춘다
- 실패도 과금될 수 있어 재시도는 1회, 폴링에는 타임아웃
- `--dry-run`, `photos`, 테스트는 Veo 를 절대 호출하지 않는다
- 텔레그램 승인 요청에 "Veo lite 2클립 · 약 1,176원" 이 함께 뜬다

## 내레이션

목소리가 AI 같아지는 건 엔진 탓만이 아니다. 세 가지를 같이 잡는다.

**1. 자막용 글과 낭독용 글을 나눈다.** 화면에는 `12,480곳` 을 띄우고, 입으로는
`만 이천사백팔십 곳` 이라고 읽는다. LLM 이 둘을 같이 만들고, 빠뜨리면 `src/text_ko.py` 의
숫자 변환이 채운다.

**2. 숨 쉴 틈을 준다.** 문장을 그냥 이어 붙이면 쉼표도 숨도 없이 계속된다.

| 위치 | 쉼 |
|---|---|
| 훅 문장 뒤 | 0.5초 |
| 일반 문장 사이 | 0.25초 |
| 클리프행어 직전 | 0.6초 |

속도도 문장 역할에 따라 다르다. 훅은 느리게(-8%), 데이터 전개는 보통(+6%), 닫는 질문은
다시 느리게(-4%). 이 쉼은 음원 길이에 포함되므로 자막 타임코드와 컷 배치가 자동으로 따라온다.

**3. 엔진은 Gemini TTS, 실패하면 edge-tts.**

| | Gemini TTS | edge-tts |
|---|---|---|
| 비용 | 무료 티어 (preview 라 분당 요청 한도가 빡빡) | 완전 무료 |
| 톤 제어 | 스타일을 말로 지시 | 속도 숫자만 |
| 키 | `GEMINI_API_KEY` (Veo 와 공유) | 불필요 |

`audio.engine` 으로 고르고, Gemini 가 한도에 걸리거나 실패하면 자동으로 edge-tts 로
내려간다. `audio.gemini_style_prompt` 에 "차분하고 낮은 목소리의 다큐멘터리 내레이터처럼"
같은 지시를 적으면 그대로 반영된다.

`python main.py voice-test "문장"` 으로 엔진별 샘플을 뽑아 귀로 비교할 수 있다.

## 영상 구성 규칙

세로 1080×1920, 30fps.

| 구간 | 화면 | 카메라 |
|---|---|---|
| 도입부 (0~8초) | 지정 등급/훅 문구 초대형 타이포 + Veo 생성 영상 | 생성 영상 자체의 움직임 |
| 공식 기록 | 유적 실사 사진 | 파랄랙스 pan, 컷당 2.5~3초 |
| 의문·전설 제기 | 사진이 내레이션에 맞춰 전환 | 파랄랙스 dolly in/out 교차 |
| 클리프행어 (1부 끝) | "2부에서 계속" | freeze |

- **안전 영역**: 상단 25%와 하단 25%는 유튜브 UI가 덮으므로 핵심 정보를 넣지 않는다.
- **무빙 교차**: 연속한 컷은 절대 같은 방향 무빙을 쓰지 않는다.
- **무음 대응**: 모든 내레이션은 자막으로 번인되고, 핵심 구절은 색으로 강조된다.
- **사실/전설 구분**: 대본은 문장 단위로 "전해지는 말로는" 같은 표현을 써서 공식 기록과
  전설을 구분하고, 화면 하단 배지에도 "국가유산청 해설 / 위키백과" 식으로 출처를 밝힌다.

## 설치

```bash
pip install -r requirements.txt
cp .env.example .env    # 키를 채운다
```

한국어 자막 폰트(Noto Sans KR)는 첫 실행 때 `resource/fonts/`에 자동으로 내려받는다.
배경음악을 쓰려면 `resource/bgm/`에 무료 라이선스 음원을 넣는다(없으면 내레이션만 나간다).

## 키 발급

| 키 | 발급처 | 없으면 |
|---|---|---|
| `DATA_GO_KR_SERVICE_KEY` | [공공데이터포털](https://www.data.go.kr) 한국관광공사 관광정보서비스(TourAPI 4.0) 활용신청 | 관광공사 사진·overview 보강만 안 되고, 국가유산청 해설문/사진/동영상은 그대로 동작한다 |
| `GEMINI_API_KEY` | Google AI Studio | 대본은 템플릿으로, 내레이션은 edge-tts 로, 영상은 무료 파랄랙스로 간다. TTS 는 무료 티어가 있지만 **Veo 는 유료 결제가 필요하다** |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | @BotFather | 검수를 건너뛰고 업로드하지 않는다 |
| `YOUTUBE_CLIENT_SECRET_FILE` | Google Cloud Console OAuth 데스크톱 클라이언트 | 업로드 단계만 실패한다 |

TTS(edge-tts)는 키가 필요 없다. 프록시 뒤에서는 `HTTPS_PROXY`를 읽어 자동으로 사용한다.

**국가유산청(khs.go.kr) 목록/상세/이미지/동영상 API는 인증키가 필요 없다** — 실제로 호출해
확인했다(`src/heritage/khs_client.py`). `DATA_GO_KR_SERVICE_KEY`는 관광공사 overview·사진
보강(2차 소스)에만 쓰이므로, 이 키가 없어도 국가유산청 해설문만으로 픽스처가 아닌 **실제
데이터**로 파이프라인이 돈다.

## 사용법

```bash
python main.py topics                  # 주제 후보 확인만
python main.py photos "강남 거리"       # 어떤 사진이 오는지 미리 확인 (Veo 호출 없음)
python main.py voice-test "문장"        # 엔진별 내레이션 샘플을 만들어 들어본다
python main.py veo-test 사진.jpg        # Veo 클립 1개만 생성해 결과·청구액 확인 (과금)
python main.py run --dry-run           # 주제·대본까지만 (영상 렌더 없음)
python main.py run --no-veo            # 전부 무료 파랄랙스로 mp4 2편 생성
python main.py run                     # 도입부는 Veo, 나머지는 파랄랙스
python main.py run --review            # 생성 후 텔레그램 검수 → 승인 시 유튜브 업로드
python main.py review-pending          # 끊긴 검수 대기를 다시 확인(재렌더 없이 업로드까지)
```

**렌더를 돌리기 전에 `photos` 로 사진이 실제로 오는지 먼저 확인하는 걸 권한다.** 사진이
한 장도 없으면 파이프라인은 단색으로 때우지 않고 그 자리에서 멈춘다.

산출물은 `output/<타임스탬프>/`에 남는다. `part1.mp4`, `part2.mp4`, 썸네일, `script.json`,
`.srt`, `result.json`.

## Windows에서 매일 자동 실행

개인 PC는 24시간 켜져 있지 않으므로, 작업 스케줄러로 매일 한 번 `run_daily.bat`
(`main.py run --review`를 감싼 스크립트)을 돌리는 방식을 쓴다. 실행 결과는
`logs/run_YYYY-MM-DD.log`(loguru, 자세한 로그)와 `logs/wrapper.log`(파이썬 자체가
못 떴을 때의 최소 기록)에 남는다.

### 등록

관리자 권한 없이 PowerShell/명령 프롬프트에서:

```bat
schtasks /create /tn "YoutubeAutoDaily" ^
  /tr "C:\path\to\youtube-auto\run_daily.bat" ^
  /sc daily /st 09:00 ^
  /rl HIGHEST
```

또는 GUI로: **작업 스케줄러 → 기본 작업 만들기** → 매일 원하는 시각 → 프로그램 시작 →
`run_daily.bat` 경로 지정. 만든 뒤 **속성**에서 아래 두 가지를 반드시 켠다.

- **[설정] 탭 → "예약된 시작을 놓쳤으면 최대한 빨리 작업 시작"**: PC가 꺼져 있어서
  예정 시각을 놓쳐도, 다음에 켰을 때 따라잡아 실행한다.
- **[일반] 탭 → "사용자가 로그온했는지 여부에 관계없이 실행"**: 화면 잠금 상태에서도
  돈다. 이 옵션을 쓰려면 Windows 계정 비밀번호를 작업 등록 시 입력해야 한다.

### 알아둬야 할 것 — 승인과 PC 전원

`run --review`는 렌더가 끝나면 텔레그램으로 검수 요청을 보내고, **그 파이썬 프로세스가
살아있는 동안만** 응답을 기다린다(기본 30분, `--wait`로 조절). 즉:

- **PC가 켜져 있고 그 프로세스가 아직 기다리는 중에 승인하면** → 곧바로 유튜브 업로드까지
  자동으로 끝난다.
- **응답을 기다리는 도중 PC가 꺼지거나 대기 시간을 넘기면** → 그 프로세스는 죽지만, 회차
  자체는 사라지지 않는다. 렌더된 영상(`output/<task_id>/`)과 승인 대기 기록
  (`state.sqlite3`의 `pending_reviews`)이 남아있어 나중에 이어서 처리할 수 있다.

**`python main.py review-pending`**이 이 이어받기 명령이다. 재렌더 없이:
1. `state.sqlite3`에서 아직 응답 못 받은 회차들을 전부 찾고
2. 그 회차들의 `result.json`(영상 경로·제목 등)을 다시 읽어
3. 텔레그램에서 응답을 다시 기다린다 — **PC가 꺼져 있는 동안 휴대폰으로 승인 버튼을 이미
   눌렀어도 괜찮다.** 텔레그램은 봇이 `getUpdates`로 가져가기 전까지 그 클릭을 들고 있으므로,
   PC를 다시 켜서 이 명령을 돌리면 그대로 받아진다.
4. 승인된 회차는 그 자리에서 유튜브 업로드까지 끝낸다.

`run_daily.bat`가 매일 `run --review` 한 번만 돌리므로, 승인이 자주 늦어진다면 작업
스케줄러에 `review-pending`용 트리거를 하나 더(예: PC 켤 때마다, 또는 몇 시간마다) 추가하는
걸 권한다.

## 안전장치

- **샘플 데이터 차단**: 서비스키가 없어 번들 픽스처로 만든 영상은 화면에 "샘플 데이터 · 실제
  해설문 아님"이 표시되고, 승인하더라도 업로드가 거부된다. 가짜 이야기가 정부 해설로
  둔갑하지 않게 하기 위한 장치다.
- **기본 비공개 업로드**: `config.yaml`의 `publish.privacy_status`가 `private`이다. 공개
  전환은 사람이 확인한 뒤에 한다.
- **중복 방지**: 이미 다룬 주제는 `state.sqlite3`에 기록되어 다시 선정되지 않는다.
- **이미지 라이선스 필터**: 재사용 가능한 라이선스만 내려받고, 사진 출처는 영상 말미와
  설명란에 자동으로 표기된다 (공공누리 1유형의 이용 조건).
- **사진이 없으면 중단**: 배경을 단색으로 채우고 조용히 렌더를 끝내지 않는다.
- **Veo 과금 상한**: 위 안전장치 참조.

## 문화유산 소스 구조

`src/heritage/`에 소스가 모여 있다. `collect_candidates()`(`src/heritage/__init__.py`)가
아래 순서로 후보를 채운다.

| 파일 | 역할 |
|---|---|
| `khs_client.py` | khs.go.kr 전용 클라이언트. 인증키 없이 XML로 응답한다(직접 호출로 확인) — `DataGoKrClient`(JSON 전제)와는 다른 클라이언트가 필요했다 |
| `heritage_source.py` | 국가유산청 문화재 목록 + 해설문(1차 소스), 이미지/동영상 조회용 식별자(`ccbaKdcd`/`ccbaAsno`/`ccbaCtcd`)도 여기서 받아둔다 |
| `heritage_media.py` | 그 식별자로 공식 이미지·동영상 조회(`SearchImageOpenapi.do`/`SearchVideoOpenapi.do`) |
| `tour_overview.py` | 해설문이 얇을 때 관광공사 overview 로 보강 (`DATA_GO_KR_SERVICE_KEY` 필요) |
| `wikipedia_enrich.py` | 전설·설화·논란 섹션을 찾아 `wiki_excerpt` 로 채움 |
| `story_scorer.py` | `story_density`/`mystery_score`/`prestige` 계산 |
| `fixtures/heritage.json` | 국가유산청 응답이 비었을 때(네트워크 문제 등) 쓰는 샘플 문화재 (실존 유적 기반, `is_sample=True`) |

각 후보는 네 가지 축(`story_density`, `mystery_score`, `prestige`, `freshness`)으로
점수가 매겨진다 (`src/topics/topic_scorer.py`).

`heritage_source.py`의 `KDCD_LABELS`(종목코드 → 국보/보물/사적 등 라벨)는 "11"(국보)만 직접
호출로 확인했고 나머지는 국가유산청이 공개한 종목코드 체계를 따른 추정치다. 실제 응답이 다르면
그 딕셔너리만 고치면 된다 — 이름 검색(`ccbaMnm1`)은 실제로 결과가 비어, 종목코드로 걸러야
목록이 온다는 것도 직접 호출로 확인했다.

상세 API(해설문)는 실제 호출로 필드까지 확인했다: 해설문은 `content`(약 500자 분량 확인),
카테고리는 `ccmaName`("국보" 등, 목록 API 엔 없는 필드), 소재지는 `ccbaLcad`(상세 주소).
`ccbaAsdt`는 해설문이 아니라 지정일자 문자열(예: "19621220")이라 대본 재료로 쓰지 않는다.
이미지 API는 `imageUrl`+`ccimDesc`(설명)만 오고, 동영상 API는 `videoUrl` 하나뿐이라
해상도·재생시간은 실제로 내려받아 확인하는 수밖에 없다(`render/motion_sources.py`가
다운로드 후 크롭·트림한다).

## 테스트

```bash
python -m pytest tests/ -q
```

## Vertex AI 설정 (생성 비용을 GCP 크레딧으로 내기)

AI Studio 키는 **선불 잔액**으로 과금된다. GCP 콘솔에 보이는 크레딧은 **Cloud Billing
계정**에 붙어 있는 다른 지갑이라, 그 크레딧을 쓰려면 Veo·Imagen 을 Vertex 로 불러야 한다.

자격증명은 **이 PC 밖으로 내보내지 않는다.** 아래는 전부 사용자 PC에서 한 번만 하면 된다.

1. GCP 콘솔에서 프로젝트에 **Vertex AI API**(`aiplatform.googleapis.com`)를 켠다.
2. 서비스 계정을 만들고 역할은 **`roles/aiplatform.user` 하나만** 준다.
   (이 키로 할 수 있는 일을 Vertex 호출로만 묶어두는 게 중요하다.)
3. 키를 JSON 으로 내려받아 `credentials/vertex-sa.json` 에 둔다. `credentials/` 는
   `.gitignore` 에 있어 저장소에 올라가지 않는다.
4. `.env` 에 경로를 적는다.
   ```
   VERTEX_PROJECT_ID=vid115
   VERTEX_SERVICE_ACCOUNT_FILE=credentials/vertex-sa.json
   ```
5. 점검한다. 기본은 **과금 없이** 인증·권한까지만 본다.
   ```
   python main.py cloud-check              # 과금 없음
   python main.py cloud-check --generate   # 이미지 1장까지 확인 (약 30원)
   ```
   막히면 단계별로 무엇을 고쳐야 하는지 로그에 찍힌다
   (403 = 역할 없음, 404 = API 안 켜짐).

### 크레딧이 떨어지면 자동으로 멈춘다

`config/config.yaml` 의 `cloud` 항목이 상한을 쥔다.

```yaml
cloud:
  credit_krw: 354417      # 가진 크레딧
  reserve_krw: 20000      # 이 금액은 아예 안 쓴다 (추정 오차 완충)
  stop_when_exhausted: true
```

호출 **전에** 남은 금액으로 감당되는지 보고, 안 되면 호출하지 않는다 — 크레딧이 바닥난
뒤의 호출은 그때부터 카드로 청구되기 때문이다. 누적 지출은 `.cache/spend_ledger.json`
에 쌓이고 `python main.py credits` 로 언제든 볼 수 있다.

여기 적히는 금액은 **우리가 계산한 추정치**다. 실제 청구액은 GCP 만 안다(크레딧 잔액을
주는 API 가 없다). 그래서 마지막 방어선으로 **GCP 콘솔의 예산 알림**도 같이 걸어두길
권한다.

## GitHub Actions 로 돌리기 (로컬 PC 없이)

로컬 PC 없이 이 저장소만으로 회차를 만든다. 자격증명은 **GitHub Secrets 와 GCP 안에만**
있고, 파일로 어디에도 저장되지 않는다.

`Actions → 쇼츠 한 회차 만들기 → Run workflow` 로 실행한다. 결과 mp4 는 텔레그램으로
가고, 워크플로 아티팩트로도 내려받을 수 있다.

### 1. GCP: 키 파일 없이 인증 붙이기 (Workload Identity)

서비스 계정 **키 JSON 을 만들지 않는다.** GitHub 이 OIDC 로 신원을 증명하고 GCP 가 그
신원에 권한을 빌려주는 방식이라, 유출될 파일 자체가 없다. 이미 키를 받아두셨다면 아래를
마친 뒤 그 키는 삭제하는 게 좋다.

GCP 콘솔 오른쪽 위 **Cloud Shell**(브라우저 터미널)에서 그대로 붙여넣는다. 로컬에
gcloud 를 깔 필요가 없다.

```bash
PROJECT_ID=vid115
REPO=5479mk-bit/youtube-auto
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')

gcloud services enable aiplatform.googleapis.com iamcredentials.googleapis.com --project=$PROJECT_ID

gcloud iam service-accounts create shorts-runner \
  --project=$PROJECT_ID --display-name="youtube-auto runner"

# 권한은 Vertex 호출 하나만 준다.
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:shorts-runner@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

gcloud iam workload-identity-pools create github --project=$PROJECT_ID --location=global \
  --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --project=$PROJECT_ID --location=global --workload-identity-pool=github \
  --display-name="GitHub" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='$REPO'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# 이 저장소에서 온 요청에만 서비스 계정을 빌려준다.
gcloud iam service-accounts add-iam-policy-binding \
  shorts-runner@$PROJECT_ID.iam.gserviceaccount.com --project=$PROJECT_ID \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"

echo "GCP_WORKLOAD_IDENTITY_PROVIDER=projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-provider"
echo "GCP_SERVICE_ACCOUNT=shorts-runner@$PROJECT_ID.iam.gserviceaccount.com"
```

마지막 두 줄이 그대로 시크릿 값이다.

### 2. GitHub: 시크릿 넣기

저장소 `Settings → Secrets and variables → Actions → New repository secret`.

| 이름 | 값 |
|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | 위 출력 그대로 |
| `GCP_SERVICE_ACCOUNT` | 위 출력 그대로 |
| `VERTEX_PROJECT_ID` | `vid115` |
| `GEMINI_API_KEY` | 대본·TTS 용 (Vertex 와 별개) |
| `GEMINI_API_KEY_BACKUP` | 있으면 쉼표로 여러 개 |
| `DATA_GO_KR_SERVICE_KEY` | 문화재 소재를 쓸 때만 필요 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 검수 알림용 |

### 3. 알아둘 것

- **승인 후 업로드는 아직 이 워크플로 밖이다.** 렌더 → 텔레그램 전송까지만 자동이고,
  승인 대기 상태는 러너가 사라지면 같이 사라진다(상태 DB 가 러너 안에 있다).
  유튜브 업로드까지 자동화하려면 상태를 저장소 밖에 두는 작업이 따로 필요하다.
- 생성물 캐시(`.cache`)는 회차 사이에 물려받는다. 같은 프롬프트를 두 번 만들지 않아
  그만큼 과금이 줄고, 실록 사이트가 끊겨도 받아둔 기사로 돌아간다.
- 크레딧 상한은 러너에서도 그대로 동작한다. 매 실행 끝에 `python main.py credits`
  결과가 로그에 남는다.
