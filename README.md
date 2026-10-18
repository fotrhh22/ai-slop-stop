# AI SLOP STOP CHALLENGE

목표 이미지를 설정하고 프롬프트로 이미지를 생성한 뒤, CLIP 유사도와 LLM Router의 PASS/FAIL을 확인하는 채팅형 챌린지입니다.

## 구성

- `web/`: React + TypeScript + Vite 프런트엔드
- `server/`: FastAPI, Gemini 이미지 생성·판정, 로컬 CPU CLIP ViT-B/32 유사도 계산
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

프로젝트 루트 `.env` 또는 `.env.local`에 `GEMINI_API_KEY`와 사용할 실제 Gemini 모델 ID를 설정합니다. CLIP용 외부 API 키는 필요하지 않습니다. 환경변수 우선순위는 셸/호스트 설정 → 루트 `.env.local` → 루트 `.env`입니다. 예시 Gemini 모델 ID의 제공 여부는 사용하는 계정에서 확인해야 합니다. 프런트엔드 주소를 변경하면 `PUBLIC_APP_ORIGIN`도 맞춥니다.

```sh
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

상태 확인: `http://127.0.0.1:8000/health`. `similarity_provider=local`, `similarity_loaded=false`라면 서버만 기동했고 모델은 아직 로드하지 않은 상태입니다. 실제 Try를 한 번 실행해야 모델 로딩과 평가 성공을 확인할 수 있습니다.

첫 Try 때 OpenCLIP `ViT-B-32/openai`를 CPU에 로드하고 이후 재사용합니다. 이미지 임베딩에 필요 없는 텍스트 인코더는 로딩 후 해제합니다. 동시에 여러 CLIP 추론을 실행하지 않고 이미지도 순서대로 인코딩해 메모리 피크를 줄입니다. Router에는 -1~1 원시 코사인을 전달하며 화면 기본 점수는 음수만 0으로 제한합니다. 기존의 임의 0.45~0.90 재스케일은 복원하지 않습니다. 점수는 일치율이 아닙니다.

모델 가중치는 공개 저장소에서 다운로드해 `CLIP_CACHE_DIR`(기본 `.cache/clip`)에 캐시합니다. 빌드 때 미리 준비하려면 `python -m server.prepare_clip`을 실행합니다. 모델 생성은 첫 평가 때 수행하므로 무료 Render 512MB에서 로딩·추론이 가능한지는 실제 배포 후 확인해야 합니다. 메모리 부족 시 프로세스가 종료될 수 있으며 health 성공만으로 이 검증을 대신할 수 없습니다.

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

STAFF 버튼은 운영자 토큰·이름·승인 사유를 입력한 뒤 서버 인증을 통과해야 실제 PASS를 저장합니다. 원래 Router 판정은 덮어쓰지 않습니다. 로컬 API는 Vite 프록시를 사용하며 분리 배포는 `VITE_API_BASE`와 백엔드 `PUBLIC_APP_ORIGIN`을 설정합니다.

## Render 무료 백엔드 배포

`render.yaml`은 싱가포르 리전의 `free` **Python 네이티브 웹서비스** 하나를 정의합니다. `.python-version`으로 Python 3.11을 선택하고 Render 전용 requirements로 Linux CPU용 torch/torchvision을 설치합니다. Dockerfile은 사용하지 않습니다. DB는 Supabase를 사용하므로 Render DB/디스크를 만들지 않습니다. Render 연결과 실제 배포는 사용자가 진행합니다.

1. 이 변경을 GitHub에 push한 뒤 Blueprint로 저장소를 연결하거나 직접 Web Service를 설정합니다. Root Directory는 **비워두고**, Language/Runtime은 **Python 3**를 선택합니다.
2. 직접 설정할 경우 Build Command와 Start Command는 아래와 같습니다. 빌드 때 가중치만 캐시하며 모델 인스턴스를 만들지는 않습니다. 기존 Docker 서비스는 코드 push만으로 Python 서비스가 되었다고 가정하지 말고 Render에서 runtime과 명령을 확인합니다.

```sh
# Build Command
pip install -r server/requirements-render.txt && python -m server.prepare_clip

# Start Command
python -m uvicorn server.main:app --host 0.0.0.0 --port $PORT --workers 1
```

3. 다음 환경변수를 입력합니다. `sync: false` 변수는 이후 Blueprint sync에서 새 값을 요구하지 않으므로 변경은 서비스 Environment에서 처리합니다. CLIP은 추가 키가 필요하지 않습니다.

| 변수 | 입력값 |
|---|---|
| `GEMINI_API_KEY` | 이미지 생성·Router용 서버 키 |
| `GEMINI_IMAGE_MODEL` | 계정에서 사용 가능한 이미지 생성 모델 ID |
| `GEMINI_ROUTER_MODEL` | 계정에서 사용 가능한 이미지 판정 모델 ID |
| `CLIP_CACHE_DIR` | `.cache/clip` (Blueprint에 설정됨) |
| `OMP_NUM_THREADS`, `MKL_NUM_THREADS` | `1` (Blueprint에 설정됨) |
| `SUPABASE_URL` | 생성한 Supabase 프로젝트 URL |
| `SUPABASE_SECRET_KEY` | 서버용 secret key 또는 legacy service-role key |
| `STAFF_AUTH_TOKEN` | STAFF 승인용 긴 임의 문자열. Blueprint 신규 생성 시 자동 생성되며 기존 서비스는 직접 설정 |

4. 먼저 **Render**에서 `/health` 응답과 실제 생성 → Try를 확인합니다. `PORT`는 Render가 제공합니다. `PUBLIC_APP_ORIGIN`은 아직 입력하지 않아도 기본 로컬 주소로 서버가 기동합니다. 이는 프런트의 브라우저 호출 허용 설정이며 모델 실행에 필요한 키가 아닙니다.
5. 이후 **Vercel**에서 Root Directory=`web`, `VITE_USE_MOCK=false`, `VITE_API_BASE=https://실제-서비스.onrender.com`으로 설정 후 배포합니다. Vercel 주소가 정해지면 Render 환경변수에 `PUBLIC_APP_ORIGIN=https://실제-프런트.vercel.app`을 추가하고 재배포합니다. Origin 값 끝에는 `/`를 넣지 않습니다.

실제 모드는 Supabase URL·서버 키와 스키마/버킷 설정이 필요합니다. `202610190001_initial_schema.sql`을 이미 실행했다면 `202610190002_receipt_session_integrity.sql`만 추가 실행합니다. 새 DB는 두 파일을 순서대로 실행합니다. 기존 `/api/generate`, `/api/evaluate`는 DB 설정 시 비활성화되며 새 UI는 `/api/challenges/...` 세션 API를 사용하므로 백엔드와 프런트를 함께 업데이트해야 합니다.

## PASS 영수증

- Router PASS 또는 STAFF 승인 → 성공 이미지 업로드 → DB의 첫 PASS 확정 → 성공 행·호출 원장·Storage 이미지를 다시 읽어 대조 → 영수증 자동 표시 순서입니다.
- 세션 ID, 영수증 ID, 성공 이미지·프롬프트 원문, PASS 출처, 생성/Router 호출 수, 모델별 입력·출력·추론·총 토큰을 표시합니다. FAIL과 자동 재시도도 누적합니다.
- API 타임아웃 등 토큰을 확인할 수 없는 호출은 NULL로 남기고 영수증에 누락 건수를 표시합니다. 확인된 토큰 합계를 전체 사용량으로 단정하지 않습니다.
- 저장 실패 시 PASS 카드에 저장 재시도를 표시합니다. 저장 재시도·영수증 다시 보기·인쇄는 유료 모델을 다시 호출하지 않습니다.
- 영수증의 **인쇄 / PDF 저장** 버튼으로 브라우저 인쇄 대화상자를 엽니다. 프린터 자동 출력이나 물리 프린터 연동은 포함하지 않습니다.
- Mock 영수증에는 DB 저장이 아닌 샘플이라고 표시하며 실제 측정값을 만들지 않습니다.
- CLIP은 로컬 계산이므로 외부 API 토큰 집계 대상이 아닙니다.
- 서버 중단이나 DB 장애로 `api_calls.pending`이 남으면 성공 확정을 차단합니다. 공급자 응답을 복구해 갱신하거나, 확인 불가능하면 관리자가 `status=unknown`, `finished_at=현재 시각`으로 정리해야 합니다. 사용량을 임의로 0으로 만들지 않습니다.

검증 명령:

```sh
python -m unittest discover -s server/tests -v
render blueprints validate render.yaml --output json --confirm
```

Render CLI 검증은 Render 인증이 필요합니다. 단위 테스트는 가중치를 다운로드하지 않고 모델 로딩 계약·코사인 계산·동시 요청 직렬화·API 오류 처리를 검증합니다. 실제 가중치와 Render 512MB 메모리 한도 검증은 별도입니다.
