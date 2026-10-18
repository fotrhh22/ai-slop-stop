# AI SLOP STOP CHALLENGE

목표 이미지를 설정하고 프롬프트로 이미지를 생성한 뒤, CLIP 유사도와 LLM Router의 PASS/FAIL을 확인하는 채팅형 챌린지입니다.

## 구성

- `web/`: React + TypeScript + Vite 프런트엔드
- `server/`: FastAPI, Gemini 이미지 생성·판정, 로컬 CLIP ViT-B/32 유사도 계산
- 현재 이미지와 챌린지 기록은 브라우저 메모리에만 유지되며 새로고침 시 초기화됩니다.
- Supabase 저장, 호출·토큰 집계, Render/Vercel 배포 연결은 후속 작업입니다.

## 로컬 실행

아래 명령은 프로젝트 루트를 기준으로 실행합니다.

### 백엔드

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
cp .env.example .env
```

`.env`에 `GEMINI_API_KEY`와 사용할 실제 모델 ID를 설정합니다. 예시 모델 ID의 제공 여부는 사용하는 계정에서 확인해야 합니다. 프런트엔드 주소를 변경하면 `PUBLIC_APP_ORIGIN`도 맞춥니다.

```sh
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

상태 확인: `http://127.0.0.1:8000/health`. CLIP은 첫 평가 시 로드되므로 이 응답만으로 CLIP 준비 완료를 확인할 수는 없습니다. 첫 평가에는 모델 가중치 다운로드가 필요할 수 있습니다.

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

현재 STAFF 전환은 인증 없는 로컬 UI 기능입니다. 공개 운영 전에 제한하거나 서버 인증을 구현해야 합니다. 현재 API는 개발용 Vite 프록시를 사용하므로 분리 배포 시 API 주소 및 CORS 설정 작업이 필요합니다.
