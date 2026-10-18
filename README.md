# AI SLOP STOP CHALLENGE

목표 이미지를 설정하고 프롬프트로 이미지를 생성한 뒤, CLIP 유사도와 LLM Router의 PASS/FAIL을 확인하는 채팅형 챌린지입니다.

## 구성

- `web/`: React + TypeScript + Vite 프런트엔드
- `server/`: FastAPI, Gemini 이미지 생성·판정, Jina CLIP 외부 API 유사도 계산
- 현재 이미지와 챌린지 기록은 브라우저 메모리에만 유지되며 새로고침 시 초기화됩니다.
- Supabase 저장, 호출·토큰 집계, Render/Vercel 배포 연결은 후속 작업입니다.

Supabase 프로젝트 생성 후 실행할 SQL과 테이블·저장 정책 설명은 [supabase/README.md](supabase/README.md)에 있습니다. 스키마 준비와 앱 연동은 별도 단계입니다.

## 로컬 실행

아래 명령은 프로젝트 루트를 기준으로 실행합니다.

### 백엔드

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
cp .env.example .env
```

프로젝트 루트 `.env` 또는 `.env.local`에 `GEMINI_API_KEY`, `JINA_API_KEY`와 사용할 실제 Gemini 모델 ID를 설정합니다. 환경변수 우선순위는 셸/호스트 설정 → 루트 `.env.local` → 루트 `.env`입니다. 예시 Gemini 모델 ID의 제공 여부는 사용하는 계정에서 확인해야 합니다. 프런트엔드 주소를 변경하면 `PUBLIC_APP_ORIGIN`도 맞춥니다.

```sh
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

상태 확인: `http://127.0.0.1:8000/health`. `similarity_configured`는 Jina 키 입력 여부만 나타내며 공급자 인증·잔액·실제 추론 성공을 보장하지 않습니다. 서버에는 torch, numpy, CLIP 가중치가 필요하지 않습니다.

Try마다 이미지 두 장을 최대 512px로 축소해 Jina `jina-clip-v2` API로 한 번에 전달하고, 반환된 벡터의 코사인 유사도를 Python 표준 라이브러리로 계산합니다. Router에는 -1~1 원시 코사인을 전달하며 화면 기본 점수는 음수만 0으로 제한합니다. 이전 ViT-B/32의 0.45~0.90 재스케일은 사용하지 않습니다. 점수는 일치율이 아니며 기존 모델 점수와 직접 비교하지 않습니다.

Jina API 키는 [Jina API 대시보드](https://jina.ai/api-dashboard/)에서 발급합니다. 이미지가 Jina에도 전달되며, Render 무료 플랜과 별개로 외부 API 사용량·요금이 발생할 수 있습니다. 키 미설정 또는 외부 호출 실패 시 로컬 모델이나 가짜 점수로 대체하지 않고 평가 오류를 반환합니다.

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

현재 STAFF 전환은 인증 없는 로컬 UI 기능입니다. 공개 운영 전에 제한하거나 서버 인증을 구현해야 합니다. 로컬 API는 Vite 프록시를 사용하며 분리 배포는 `VITE_API_BASE`와 백엔드 `PUBLIC_APP_ORIGIN`을 설정합니다.

## Render 무료 백엔드 배포

`render.yaml`은 싱가포르 리전의 `free` Docker 웹서비스 하나를 정의합니다. DB는 Supabase를 사용하므로 Render DB/디스크를 만들지 않습니다. Docker는 Python 3.11 기반이며 비밀키·로컬 환경파일을 복사하지 않습니다.

1. 이 변경을 GitHub에 push한 뒤 Render에서 **New → Blueprint**로 저장소를 연결합니다.
2. 최초 생성 시 다음 환경변수를 입력합니다. `sync: false` 변수는 이후 Blueprint sync에서 새 값을 요구하지 않으므로 변경은 서비스 Environment에서 처리합니다.

| 변수 | 입력값 |
|---|---|
| `GEMINI_API_KEY` | 이미지 생성·Router용 서버 키 |
| `GEMINI_IMAGE_MODEL` | 계정에서 사용 가능한 이미지 생성 모델 ID |
| `GEMINI_ROUTER_MODEL` | 계정에서 사용 가능한 이미지 판정 모델 ID |
| `JINA_API_KEY` | 외부 이미지 임베딩 API 키 |
| `PUBLIC_APP_ORIGIN` | 실제 프런트 주소 하나, 끝의 `/` 제외. 로컬 테스트는 `http://127.0.0.1:5173` |
| `SUPABASE_URL` | 생성한 Supabase 프로젝트 URL |
| `SUPABASE_SECRET_KEY` | 서버용 secret key 또는 legacy service-role key |

3. `/health` 응답과 실제 생성 → Try를 확인합니다. Render는 `PORT`를 제공하며 컨테이너는 `0.0.0.0:$PORT`에 바인딩합니다.
4. Vercel에서는 Root Directory=`web`, `VITE_USE_MOCK=false`, `VITE_API_BASE=https://실제-서비스.onrender.com`으로 설정 후 빌드합니다. Vercel 주소가 정해지면 Render의 `PUBLIC_APP_ORIGIN`을 그 주소로 바꾸고 재배포합니다.

Supabase 키를 설정해도 아직 PASS 기록은 저장되지 않습니다. 현재 코드에는 설정 로딩만 있고 세션 생성·Storage 업로드·호출/토큰 기록·성공 RPC 연동은 후속 구현입니다. Jina 호출·사용량도 저장하려면 기존 `api_calls`의 operation/성공 집계에 외부 임베딩을 추가해야 합니다. 최초 스키마는 생성·Router 호출만 집계합니다.

검증 명령:

```sh
python -m unittest discover -s server/tests -v
render blueprints validate render.yaml --output json --confirm
```

Render CLI 검증은 Render 인증이 필요합니다. Docker 빌드와 실제 API 호출 성공 여부는 별도로 확인해야 합니다.

API 계약 참고: [Jina Embeddings API](https://api.jina.ai/docs), [Jina CLIP v2](https://jina.ai/news/jina-clip-v2-multilingual-multimodal-embeddings-for-text-and-images/).
