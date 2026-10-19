# AI SLOP STOP CHALLENGE

목표 이미지를 설정하고 프롬프트로 이미지를 생성한 뒤, Gemini의 항목별 이미지 비교와 PASS/FAIL을 확인하는 채팅형 챌린지입니다.

## 구성

- `web/`: React + TypeScript + Vite 프런트엔드
- `server/`: FastAPI, Gemini 이미지 생성·직접 비교 판정
- 실제 모드에서는 세션·프롬프트·모델 호출/토큰을 Supabase에 기록하고, 첫 PASS 이미지와 누적값을 저장해 영수증을 발급합니다.
- 현재 화면 목록·세션 접근 토큰·FAIL 이미지는 브라우저 메모리에만 유지됩니다. 새로고침하면 UI 세션은 초기화되지만 DB 기록은 남습니다.

Supabase 초기 SQL과 **영수증용 추가 SQL**의 실행 순서·테이블 설명은 [supabase/README.md](supabase/README.md)에 있습니다.

## 로컬 실행

아래 명령은 프로젝트 루트를 기준으로 실행합니다.

### 백엔드

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
cp .env.example .env
```

프로젝트 루트 `.env` 또는 `.env.local`에 `GEMINI_API_KEY`와 사용할 실제 Gemini 모델 ID를 설정합니다. 환경변수 우선순위는 셸/호스트 설정 → 루트 `.env.local` → 루트 `.env`입니다. 예시 Gemini 모델 ID의 제공 여부는 사용하는 계정에서 확인해야 합니다. 프런트엔드 주소를 변경하면 `PUBLIC_APP_ORIGIN`도 맞춥니다.

```sh
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

상태 확인: `http://127.0.0.1:8000/health`. `evaluation_method=gemini_vision`은 두 이미지를 Gemini Router에 전달하는 구성입니다. 실제 API 연결 성공 여부는 생성 → Try로 확인합니다.

로컬 추론 모델·가중치 다운로드·torch 의존성은 없습니다. Try에서는 목표/생성 이미지를 직접 비교해 피사체·구도·색감·배경의 정성 평가와 PASS/FAIL 및 이유를 반환합니다. 숫자 유사도는 계산·저장하지 않습니다.

### 프런트엔드

별도 터미널에서 실행합니다.

```sh
cd web
npm ci
cp .env.example .env.local
npm run dev
```

`http://127.0.0.1:5173`에서 접속합니다. `web/.env.local`의 `VITE_USE_MOCK=true`는 샘플 이미지·가짜 판정을 사용하며, `false`는 실제 백엔드를 호출합니다. 환경변수를 변경하면 개발 서버를 재시작합니다.

```sh
cd web
npm run build
```

## Git에 포함하지 않는 파일

실제 `.env` 파일, 비밀키, 가상환경, `node_modules`, 빌드 결과, 모델 캐시와 루트의 참고용 PDF/Word 문서는 제외합니다. 환경변수 예시와 `package-lock.json`은 포함합니다.

STAFF ON이면 생성된 이미지를 평가 전·FAIL 여부와 관계없이 버튼 한 번으로 수동 PASS 처리합니다. 추가 인증이나 입력 폼은 없습니다. DB 영수증의 이름은 `이름 미입력`, 사유는 `STAFF 버튼으로 수동 승인`으로 기록합니다. 원래 Router 판정은 덮어쓰지 않습니다. 로컬 API는 Vite 프록시를 사용하며 분리 배포는 `VITE_API_BASE`와 백엔드 `PUBLIC_APP_ORIGIN`을 설정합니다.

## Render 무료 백엔드 배포

`render.yaml`은 싱가포르 리전의 `free` **Python 네이티브 웹서비스** 하나를 정의합니다. `.python-version`으로 Python 3.11을 선택하고 `server/requirements.txt`의 웹/API 의존성만 설치합니다. Dockerfile은 사용하지 않습니다. DB는 Supabase를 사용하므로 Render DB/디스크를 만들지 않습니다. Render 연결과 실제 배포는 사용자가 진행합니다.

1. 이 변경을 GitHub에 push한 뒤 Blueprint로 저장소를 연결하거나 직접 Web Service를 설정합니다. Root Directory는 **비워두고**, Language/Runtime은 **Python 3**를 선택합니다.
2. 직접 설정할 경우 Build Command와 Start Command는 아래와 같습니다. 기존 수동 설정 서비스는 Build Command도 직접 변경한 뒤 **Clear build cache & deploy**로 재배포해 과거 ML 의존성 캐시를 제거합니다.

```sh
# Build Command
pip install -r server/requirements.txt

# Start Command
python -m uvicorn server.main:app --host 0.0.0.0 --port $PORT --workers 1
```

3. 다음 환경변수를 입력합니다. `sync: false` 변수는 이후 Blueprint sync에서 새 값을 요구하지 않으므로 변경은 서비스 Environment에서 처리합니다.

| 변수 | 입력값 |
|---|---|
| `GEMINI_API_KEY` | 이미지 생성·Router용 서버 키 |
| `GEMINI_IMAGE_MODEL` | 계정에서 사용 가능한 이미지 생성 모델 ID |
| `GEMINI_ROUTER_MODEL` | 계정에서 사용 가능한 이미지 판정 모델 ID |
| `SUPABASE_URL` | 생성한 Supabase 프로젝트 URL |
| `SUPABASE_SECRET_KEY` | 서버용 secret key 또는 legacy service-role key |

4. 먼저 **Render**에서 `/health` 응답과 실제 생성 → Try를 확인합니다. `PORT`는 Render가 제공합니다. `PUBLIC_APP_ORIGIN`은 아직 입력하지 않아도 기본 로컬 주소로 서버가 기동합니다. 이는 프런트의 브라우저 호출 허용 설정이며 모델 실행에 필요한 키가 아닙니다.
5. 이후 **Vercel**에서 Root Directory=`web`, `VITE_USE_MOCK=false`, `VITE_API_BASE=https://실제-서비스.onrender.com`으로 설정 후 배포합니다. Vercel 주소가 정해지면 Render 환경변수에 `PUBLIC_APP_ORIGIN=https://실제-프런트.vercel.app`을 추가하고 재배포합니다. Origin 값 끝에는 `/`를 넣지 않습니다.

실제 모드는 Supabase URL·서버 키와 스키마/버킷 설정이 필요합니다. 기존 초기/영수증 SQL을 실행했다면 `202610190003_remove_similarity.sql`만 추가 실행합니다. 새 DB는 migrations의 세 파일을 번호순으로 실행합니다. 세 번째 파일은 `attempts.similarity_raw`, `attempts.similarity_display` 컬럼을 삭제하며, 새 백엔드는 컬럼 삭제 전후 모두 작동합니다. 기존 `/api/generate`, `/api/evaluate`는 DB 설정 시 비활성화되며 새 UI는 `/api/challenges/...` 세션 API를 사용하므로 백엔드와 프런트를 함께 업데이트해야 합니다.

## 판정 대기 시간

- Router에 보내는 사본은 긴 변을 최대 1024px로 줄이고 큰 PNG 등의 전송량을 JPEG 압축으로 낮춥니다. 작은 원본은 재인코딩하지 않습니다. 원본 해시와 성공 이미지 저장에는 원본을 사용합니다. 작은 글자·미세 디테일은 축소 과정에서 손실될 수 있습니다.
- 기본 Router는 `gemini-3.5-flash-lite` + `thinking_level=MINIMAL`입니다. 기존 `GEMINI_ROUTER_MODEL` 환경변수가 있으면 그 값이 우선하므로 Render에서 직접 바꿔야 합니다. 일반 Gemini 3 Flash를 선택하면 LOW를 사용합니다. 이미지 생성 모델 설정은 변경하지 않습니다. 낮은 추론 강도의 판정 품질은 실제 목표 이미지로 확인해야 합니다.
- 새 UI는 `POST /evaluations`로 작업 ID를 받고 `GET /evaluations/{job_id}`로 2초마다 진행 상태를 확인합니다. 긴 Gemini 요청을 브라우저 연결 하나에 묶지 않습니다. 상태 조회는 DB·모델 호출 없이 서버의 완료 상태만 확인합니다. 시작 요청 재전송은 동일 작업 ID를 재사용합니다.
- Router는 SDK 대기 제한 45초, 자동 재시도 0회입니다. 시간 초과는 평가 FAIL과 구분된 오류이며, 알 수 없는 토큰은 원장에 누락으로 남깁니다. 사용자가 다시 Try할 때만 새 판정을 요청합니다.
- 추가 DB SQL·외부 큐 서비스는 없습니다. 현재 단일 Render 프로세스(`--workers 1`)에서 동시에 최대 2개 작업을 실행합니다. 진행 작업은 재배포·프로세스 재시작 시 중단되며 UI에 명시적 오류를 표시합니다. 완료된 판정·모델 호출 원장은 기존 DB에 저장됩니다. 프로세스 강제 종료로 남은 `api_calls.pending`은 아래 영수증 설명대로 별도 확인해야 합니다.
- `gemini_api operation=router attempt=... elapsed_ms=...` 로그는 DB 원장 저장을 제외한 실제 SDK 요청 시간을 기록합니다. 앞선 `evaluation stage=router` 로그와 비교하면 DB/이미지 준비 시간과 공급자 응답 대기를 구분할 수 있습니다.
- Supabase HTTP 연결을 재사용합니다.
- 새 UI는 판정 저장 직후 PASS/FAIL을 표시하고, PASS 영수증 업로드·검증은 별도 요청으로 자동 진행합니다. 저장 실패는 영수증 재시도로 처리하며 Router를 다시 호출하지 않습니다.
- Render 로그의 `evaluation stage=... elapsed_ms=...`로 authorize, verify_image, download_target, router, save_verdict 시간을 구분합니다. router 시간은 이미지 준비·호출 원장 기록·Gemini 재시도까지 포함합니다. 운영 응답 시간은 별도 측정해야 합니다.

## PASS 영수증

- Router PASS 또는 STAFF 승인 → 성공 이미지 업로드 → DB의 첫 PASS 확정 → 성공 행·호출 원장·Storage 이미지를 다시 읽어 대조 → 채팅 안에 영수증 카드 표시 순서입니다.
- 화면·인쇄 영수증에는 성공 이미지, 프롬프트 원문, 성공 시각(한국 시간), 승인 방식, 누적 이미지 생성·판정 요청 수, 총 사용 토큰만 표시합니다. 내부 ID, 검증 항목, 호출 원장과 모델별 토큰 내역은 출력하지 않으며 DB에 보관합니다. 사용량이 누락된 경우 확인된 합계임을 짧게 표시합니다.
- API 타임아웃 등 토큰을 확인할 수 없는 호출은 NULL로 남기고 영수증에 누락 건수를 표시합니다. 확인된 토큰 합계를 전체 사용량으로 단정하지 않습니다.
- 저장 실패 시 PASS 카드에 저장 재시도를 표시합니다. 저장 재시도·영수증 새로고침·인쇄는 유료 모델을 다시 호출하지 않습니다.
- 영수증의 **인쇄 / PDF 저장** 버튼으로 브라우저 인쇄 대화상자를 엽니다. 프린터 자동 출력이나 물리 프린터 연동은 포함하지 않습니다.
- Mock 영수증에는 DB 저장이 아닌 샘플이라고 표시하며 실제 측정값을 만들지 않습니다.
- 서버 중단이나 DB 장애로 `api_calls.pending`이 남으면 성공 확정을 차단합니다. 공급자 응답을 복구해 갱신하거나, 확인 불가능하면 관리자가 `status=unknown`, `finished_at=현재 시각`으로 정리해야 합니다. 사용량을 임의로 0으로 만들지 않습니다.

검증 명령:

```sh
python -m unittest discover -s server/tests -v
(cd web && npm run test:evaluation && npm run build)
render blueprints validate render.yaml --output json --confirm
```

Render CLI 검증은 Render 인증이 필요합니다. 단위 테스트는 실제 API를 호출하지 않고 이미지 입력 검증·멀티모달 평가 계약·API 오류·PASS/STAFF 영수증 누적과 재시도를 검증합니다. 운영 배포의 메모리 사용량은 Render Metrics에서 별도로 확인합니다.
