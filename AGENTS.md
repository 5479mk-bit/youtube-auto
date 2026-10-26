# AGENTS.md — 재난 쇼츠 자동화 (Codex/에이전트용 작업 안내)

이 파일은 이 저장소에서 작업하는 코딩 에이전트(OpenAI Codex 등)가 가장 먼저 읽는 안내서다.
사람용 상세 설명은 `README.md`, 설정 값의 이유는 `config/config.yaml` 주석에 있다.

## 한 줄 요약
해외 재난·인재(人災) 사건을 위키백과(한국어판) 본문을 사실 재료로 삼아 **스페인어 쇼츠(60~70초)**로
자동 제작하고, **텔레그램 승인 없이 바로 유튜브에 공개 업로드**(+틱톡 초안함)한다. 텔레그램에는
"업로드 완료 + 링크 + 크레딧 요약", 영상, 설명(+해시태그 5개)만 보낸다.

## 운영 브랜치와 스케줄 (중요)
- 스케줄 워크플로는 **`claude/planning-session-uvu3hb` 브랜치(저장소 기본 브랜치)** 코드로 돈다.
  `main` 은 없다. 운영에 반영하려면 이 브랜치에 푸시해야 한다.
- 스케줄은 GitHub Actions 가 돌린다 — 에이전트가 켜져 있을 필요가 없다.
  - `.github/workflows/shorts.yml` — 매일 `0 0 * * *`(UTC 00:00 = KST 09:00) 한 회차 생성·업로드.
    수동 실행 입력: `image_only`(이미지 1장만, 약 30원 — 그림체 샘플 확인용), `no_veo`,
    `reuse_topic`, `combine_parts`, `preview_seconds`, `review_wait_seconds`.
  - `.github/workflows/review-pending.yml` — `*/15`(실제로는 GitHub 이 3~5시간 간격으로 늦게 돌림).
    승인 방식 시절의 대기 회차와, 자동 업로드가 실패한 회차의 재시도를 맡는다.
  - 두 워크플로는 같은 텔레그램 봇 토큰으로 `getUpdates` 를 쓰므로 `concurrency: telegram-review-poll`
    로 겹치지 않게 묶여 있다(동시에 돌면 승인 콜백이 사라졌다).
- `.cache`(지출 원장 `spend_ledger.json`, Veo 클립 캐시, 발굴 제목 캐시)는 `actions/cache` 로
  회차 사이에 이어진다. **저장 단계는 `if: always()` 로 분리돼 있다** — 예전엔 아티팩트 용량 초과로
  작업이 실패하면 저장이 건너뛰어져 지출 장부가 비었다. 이 구조를 되돌리지 말 것.

## 시크릿 (GitHub Actions Secrets — 저장소 파일에는 없다)
`GEMINI_API_KEY`, `GEMINI_API_KEY_BACKUP`, `VERTEX_PROJECT_ID`, `GCP_WORKLOAD_IDENTITY_PROVIDER`,
`GCP_SERVICE_ACCOUNT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `YOUTUBE_CLIENT_SECRET_JSON`,
`YOUTUBE_TOKEN_JSON`, `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN_SEED`,
`DATA_GO_KR_SERVICE_KEY`(선택), `META_*`/`INSTAGRAM_*`/`GCS_PUBLIC_BUCKET`(미사용), `DOTENV`(위 값들을
.env 형식으로 한꺼번에 넣는 대안). 로컬 실행은 `.env.example` 을 `.env` 로 복사해 채운다.
**키·토큰을 커밋하거나 로그·출력에 찍지 말 것.**

## 로컬 실행·검증
```bash
python -m pip install -r requirements.txt
python -m pytest tests -q          # 커밋 전 반드시 전부 통과(현재 215 passed, 1 skipped)
python main.py topics              # 주제 후보만 확인(과금 없음)
python main.py cloud-check --generate   # Vertex 이미지 1장(약 30원)
python main.py run --review --wait 1200 --combine-parts   # 한 회차 전체(과금 발생)
python main.py credits             # 지출 원장 요약
python main.py review-pending --wait 30
```

## 파이프라인 구조 (src/)
- `pipeline.py` — 전체 흐름: 주제 선정 → 대본 → 장면 계획 → TTS → 자막 → 렌더 → 업로드.
  `review_and_publish()` 가 `review.auto_publish` 에 따라 자동 업로드/승인 대기를 가른다.
- `disasters/` — 재난 후보. `config topics.disaster.titles`(사람이 지정)를 **먼저** 쓰고, 안 쓴 게
  없으면 `discovery.py` 가 위키백과 분류(`topics.disaster.discover.roots`)에서 자동 발굴한다.
  한국 국내 사건은 제목·분류·본문 3단계로 막는다(절대 다루지 않는 원칙).
- `script/script_writer.py` — 대본 프롬프트(`GLOBAL_DISASTER_PROMPT`, 스페인어). 그림체·길이·톤·
  VEO 비율·루프 구조가 여기 있다.
- `publish/seo_metadata.py` — 제목·설명·해시태그 프롬프트(`_COMMON_RULES_ES`), 캡션 해시태그 5개 규칙.
- `motion/veo_client.py` — Veo(text-to-video 옵션), `render/motion_sources.py` — `VEO_MOTION_BOOST`.
- `cloud/spend_guard.py` — 지출 원장과 상한(크레딧 바닥나면 호출 중단).
- `review/telegram_bot.py` — 텔레그램 전송·승인 폴링.

## 현재 확정된 콘텐츠 방향 (사용자 결정, 2026-10)
- 채널 언어 스페인어(중남미 중립). 사실은 위키백과 원문만 — 지어내지 않는다.
- 그림체: **초실사 시네마틱 항공샷**(레퍼런스: 인스타 15segundosmas). 흐린 폭풍 하늘, 압도적 규모.
  미니어처 디오라마·빨간 주석은 폐기됨. 첫 문장 = 썸네일 프레임(가장 압도적인 와이드 한 컷).
- 길이 60~70초(사용자 지시: 60초 이상). 부당 350/380자, 7~8문장, VEO 부당 최소 5.
- 톤: 긴박한 재난 예고편 내레이터, 어그로 제목(사실 기반). 마지막 문장이 첫 문장으로 이어지는 루프.
- 움직임: Veo text-to-video + "장면 자체가 크게 움직이게" 모션 부스트.
- 설명: 스포일러 금지(결말·원인·최종 숫자 X), "핵심은 영상 마지막에" 예고.
- 해시태그: 텔레그램 캡션은 **정확히 5개** = 주제 3개(그중 1개는 나라) + `#desastres #misterio`.
  유튜브 태그는 주제 3개 + 고정 12개(태그는 노출에 큰 영향 없어 더 늘리지 않음).
- 업로드: 공개(`publish.privacy_status: public`), 승인 없이 자동(`review.auto_publish: true`).
- 정치·시위 같은 시사 주제는 광고 제한 위험으로 다루지 않는다.
- 업로드 시각: 크론은 KST 09:00 그대로 둔다(2026-10-26 사용자 결정). GitHub 스케줄 지연으로 실제
  업로드는 KST 12~13시경 = 멕시코·중남미 저녁 7~10시 — 주 시청층 골든타임. 스페인 저녁(KST 새벽)은 노리지 않는다.
- 첫 3초: 4단어 이내 질문을 화면 정중앙에 크게(render_hook_question), 상단 배너는 3초 뒤부터.

## 비용·크레딧 (주의)
- Vertex(GCP 크레딧) 사용. Vertex 에는 Veo lite 가 없어 fast 단가(4초 클립 약 882원)로 과금된다.
  회차당 약 1만 원 안팎. 로그의 "Veo lite … 약 N원" 문구는 lite 단가 기준이라 실제보다 낮다.
- 지출 원장은 **예비 안전장치**(사용자 지시로 켜 둠, `cloud.stop_when_exhausted: true`).
  기준은 `cloud.balance_krw`(콘솔 실제 잔액) − `balance_adjust_krw`, `balance_as_of` 이후 지출만 뺀다.
  원장은 LLM 비용 등을 계산하지 않아 실제보다 낮게 나온다 — 사용자가 콘솔 값으로 가끔 갱신한다.
- 사용자가 "크레딧 있는 다른 API" 를 알려주면 영상 생성 백엔드를 바꿀 예정(요청 시에만).
- Veo 상한: 회차 14클립, 하루 28클립.

## 알려진 미해결 이슈
- 유튜브 토큰이 `youtube.upload` 좁은 스코프라 썸네일 지정·재생목록 추가가 403. 사람이 넓은
  스코프로 재로그인(`tools/get_youtube_token.py`)해야 한다. 썸네일은 채널 인증도 필요.
- `review-pending` 스케줄이 실제로는 몇 시간 간격으로만 돈다(GitHub 스케줄 지연).
- 1080p 는 Veo 3.1 이 8초 클립에서만 지원 — 지금(4초 클립)은 720p.
- 승인 방식 시절 대기 회차 6건(9/30~10/9)은 자동 업로드하지 않고 남겨 둠.

## 작업 규칙
- 변경은 테스트로 고정하고(`tests/`), 커밋 전 `pytest` 전부 통과.
- 운영 반영은 `claude/planning-session-uvu3hb` 에 빠른 병합(fast-forward)으로. 남의 이력 재작성 금지.
- 과금되는 실행(워크플로 수동 실행, `run`, `cloud-check --generate`)은 사용자 확인 후에.
- 사용자와는 한국어로 대화한다. 코드 주석도 기존처럼 한국어로, 결정 이유와 날짜를 남긴다.
