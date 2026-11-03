# Phase 0–5 적용 안내

**최신 변경:** Gemini 자전거는 이제 [고정 GPU 실측 평균 346.3 mWh 비교 목표](gemini-reference-bike-target.md)를 사용한다.
추가 SQL과 최신 코드 적용 후에는 수동 입력 없이 100% 자동 종료한다.

모델 선택/자동 준비를 추가하려면 이 문서 적용 후 [Phase 6–7 안내](model-profiles-and-gpu-preparation.md)를 따른다.
Gemini 토큰 전용·자전거 종료 출력 규칙은 유지한다. Phase 8은 현장 검증, Phase 9는 대체 모델 검증이다.

작성/검증: 2026-11-04. 구현 브랜치: 두 저장소 모두 `codex/campaign-bike-flow`.
운영 DB·Render/Vercel 설정은 사용자가 적용한다. 이 작업에서 운영 환경을 변경하지 않았다.

## 구현한 규칙

- 기본 구성: Gemini 생성/판정. 토큰만 기록·표시하며 Wh를 추정하지 않는다.
- Gemini AI target_wh는 null을 유지한다. 별도 reference_target_wh=0.3463을 자전거 고정 비교 목표로 사용한다.
  ×100 환산 발전량이 목표에 도달하면 자동 종료·출력하며, 미달 수동 종료도 출력한다.
- 기존 Zeus 구성도 지원한다. 모든 생성/판정 에너지가 확인되면 첫 PASS 누적 GPU Wh를 목표로 고정한다.
- 오픈모델은 ×100 환산 에너지가 목표에 도달하면 자동 종료한다. 미달 수동 종료도 출력한다.
- 실제 발전량은 내부 적분/DB에 보존하고 참가자 화면·종이에는 환산 발전량만 표시한다.
- AI PASS는 웹 영수증/코드/대기 기록만 생성한다. 종이 인쇄 트리거는 계측 종료다.
- STAFF 수동 PASS는 UI/API/신규 DB 성공 저장에서 차단한다.
- 종료 결과는 먼저 Mac SQLite에 저장한다. 클라우드 재전송은 같은 session ID를 사용한다.
  DB 결과·인쇄 큐 생성은 한 트랜잭션이며, 최초 인쇄 작업은 1개다.
- 프린터 전송 결과 UNKNOWN은 자동 재인쇄하지 않는다. 운영자가 기존 종료 영수증을 재출력할 수 있다.

## 1. 기존 출력 프로그램 중지

기존 Mac 프린터 worker가 실행 중이면 해당 터미널에서 Ctrl+C로 중지한다.
DB 큐 정책과 새 renderer가 함께 적용된 뒤 새 worker를 실행한다.
진행 중인 SENDING 작업이 있으면 종이 출력 여부를 확인하고 임의로 재전송하지 않는다.

## 2. Supabase SQL (사용자 직접 실행)

기존 프로젝트 SQL Editor에서 아래 파일 **전체**를 실행한다.

`supabase/migrations/202611030001_campaign_bike_flow.sql`

이 파일의 선행 조건:

1. 기존 challenges/attempts/api_calls/successes 및 영수증/세션 migration.
2. `202611010001_print_jobs.sql`.
3. `202611020001_gpu_energy.sql`.

Supabase 확인 쿼리:

```sql
select to_regclass('public.successes') as successes,
       to_regclass('public.print_jobs') as print_jobs;
select column_name from information_schema.columns
where table_schema='public' and table_name='api_calls' and column_name='energy_measurement';
```

새 migration은 신규 테이블 2개와 함수·트리거를 한 transaction으로 만든다.
이미 적용한 뒤 다시 실행하지 않는다. 중간 오류가 나면 transaction은 롤백되므로 오류부터 확인한다.
기존 데이터/인쇄 기록은 삭제하지 않으며 과거 `ai_pass_legacy` 작업은 새 claim에서 제외한다.
적용 이후 신규 AI PASS부터 대기 목록에 들어온다. 과거 PASS를 무조건 재등록하지 않는다.

## 3. Render 환경변수

Environment에서 다음을 설정한다. 아래 변수는 실제 코드가 읽는 이름이다.

```dotenv
IMAGE_PROVIDER=gemini
ROUTER_PROVIDER=gemini
GEMINI_API_KEY=<기존 Gemini 키>
GEMINI_IMAGE_MODEL=<기존 사용하던 실제 이미지 모델 ID>
GEMINI_ROUTER_MODEL=<기존 사용하던 실제 판정 모델 ID>
CLIP_MODE=off
PUBLIC_APP_ORIGIN=https://ai-slop-stop.vercel.app
PRINT_STATION_ID=booth-1
BIKE_WORKER_TOKEN=<새 난수, 32자 이상>
PRINT_WORKER_TOKEN=<프린터 브리지와 같은 기존 난수, 24자 이상>
```

`SUPABASE_URL`, `SUPABASE_SECRET_KEY`는 기존 값을 유지한다. GPU 환경변수는 이 기본 Gemini 경로에서
사용하지 않는다. `PRINT_PAIRING_TOKEN`은 새 챌린지 생성/대기 등록에 필요하지 않다.
BIKE_WORKER_TOKEN과 PRINT_WORKER_TOKEN은 서로 다른 역할이므로 별도 값을 사용한다.
키를 GitHub·채팅·Vercel의 VITE_ 변수에 넣지 않는다.

새 Bike 토큰을 Mac 클립보드에 만들려면:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' | pbcopy
```

그 값을 Render BIKE_WORKER_TOKEN과 다음 Mac Bike 설정에 동일하게 넣는다.

SQL 적용 후 이 브랜치 코드를 배포한다. 운영 main을 쓰려면 검토 후 두 저장소의 PR을 병합한다.
설정만 바꾸고 기존 코드로 배포하면 새 흐름이 생기지 않는다.

배포 후 Render `/health` 확인:

```json
{"image_provider":"gemini","router_provider":"gemini",
 "bike_configured":true,"receipt_print_trigger":"bike_session_end","manual_pass_enabled":false}
```

## 4. Vercel 설정과 배포

동일 기능 브랜치/병합된 main의 프런트를 배포한다.

```dotenv
VITE_USE_MOCK=false
VITE_API_BASE=https://ai-slop-stop-api.onrender.com
```

Production 값으로 재배포한다. Preview를 시험하면 Render PUBLIC_APP_ORIGIN도 정확한 Preview origin으로 맞춘다.
웹에서 STAFF/행사 프린터 연결/인쇄 버튼은 없어지고, AI PASS 영수증에 참가자 코드와 측정소 안내가 나온다.

## 5. Mac Bike 프로그램

Bike 저장소의 같은 기능 브랜치를 사용한다. 현재 작업본은
`/Users/hunhokang/Downloads/esg_campaign/output/bike-integration`이다.

프로젝트 루트의 `.env.example`을 `.env.local`로 복사하고 채운다.

```dotenv
BIKE_API_BASE=https://ai-slop-stop-api.onrender.com
BIKE_WORKER_TOKEN=<Render와 같은 Bike 토큰>
# 선택: 기존 계측 기록을 계속 쓰려면 원래 DB의 절대 경로
# BIKE_DB_PATH=/Users/hunhokang/Downloads/esg_campaign/bike/data/station.sqlite3
```

BIKE_DB_PATH를 생략하면 새 작업본의 bike/data/station.sqlite3를 사용한다.
기존 DB를 쓰려면 기존 수신 프로그램을 정상 종료하고 백업한 뒤 새 버전으로 연다.
SQLite는 새 열·동기화 테이블을 추가하고 기존 세션/샘플을 보존한다.

```bash
cd /Users/hunhokang/Downloads/esg_campaign/output/bike-integration
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r bike/requirements.txt
python -m bike --host 0.0.0.0 --port 8765
```

같은 포트의 기존 Bike 서버가 있으면 먼저 정상 종료한다.
iPad에서는 같은 Wi-Fi의 `http://<Mac의 Wi-Fi IP>:8765`로 접속한다.
대기 코드를 선택하고 USB/BLE의 유효 실측 수신을 확인한 뒤 시작한다.
공식 대기 참가자를 demo 데이터로 측정/출력하는 경로는 차단돼 있다.
인터넷이 끊기면 새 참가자 claim은 시작하지 않는다. 진행 중 계측과 종료 결과는 Mac에 남는다.
재연결 후 종료 이벤트가 전송되면 인쇄 큐에 들어간다.

## 6. Mac PT-210 브리지

새 ai-slop-stop 코드에서 실행한다. 기존 private env/인쇄 journal을 계속 사용할 수 있다.

```dotenv
PRINT_API_BASE=https://ai-slop-stop-api.onrender.com
PRINT_WORKER_TOKEN=<Render와 같은 프린터 토큰>
PRINT_CUPS_QUEUE=PT210_USB_BRIDGE
```

위 값을 `printer_bridge/.env.local`에 둔다. 큐 이름은 실제 `lpstat -p` 결과와 맞춘다.
기존 `.env.local`을 재사용하려면 --env에 그 파일의 절대 경로를 지정한다.

```bash
cd /Users/hunhokang/Downloads/esg_campaign/output/zeus-migration
python3 -m venv .venv-print
source .venv-print/bin/activate
python -m pip install -r printer_bridge/requirements.txt
python -m printer_bridge.worker --env printer_bridge/.env.local --status
python -m printer_bridge.worker --env printer_bridge/.env.local --run
```

Mac CUPS/PT-210 기존 하드웨어 설치는 재사용한다. 기본 journal 경로는
`~/Library/Application Support/AI Slop Print`다. 중복 출력 방지를 위해 기존 journal을 임의 삭제하지 않는다.
SENT는 CUPS 접수이며 종이 배출 완료의 보장은 아니다.

## 직접 확인할 항목

1. Gemini AI PASS → 토큰 표시, 참가자 코드 발급, 이 시점 종이 출력 없음.
2. iPad 대기 코드 선택 → 참가자 자동 입력, GPU 평균 비교 목표 346.3 mWh 자동 표시.
3. USB/BLE 실측 시작 → 환산값만 표시 → 100% 자동 종료 또는 미달 수동 종료 → 그 결과로 1장.
4. 종료 두 번 클릭/새로고침으로 추가 인쇄되지 않음.
5. 나중에 Zeus 실측 경로 사용 시 목표 자동 입력·100% 자동 종료 및 미달 수동 종료를 각각 확인.
6. 전송 중 인터넷 단절 후 복구해도 최초 결과가 유지되고 인쇄 작업이 하나임.

화면 점검은 사용자 요청으로 생략했다. 실제 Gemini API·GPU·FNB58·iPad·PT-210 물리 출력은
이번 구현 검증에서 실행하지 않았다.

자동 검증: 서버/인쇄 unit tests, Bike unit tests, 웹 빌드와 client tests,
임시 PostgreSQL 실제 migration 및 트랜잭션 테스트,
두 저장소 HTTP→SQL→검증 영수증→384-dot renderer 통합 테스트.
