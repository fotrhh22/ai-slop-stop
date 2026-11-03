# 현재 RunPod를 기존 Render/Vercel에 연결

2026-11-03: GPU API 외부 인증 health 및 생성/판정/계측 시험 완료.
GitHub push, Supabase SQL, Render/Vercel 배포는 아직 실행하지 않았다.
별도 Network Volume 이전은 사용자의 현재 선택이 아니며 연결의 선행 조건도 아니다.

## 순서

1. `codex/zeus-gpu-migration` 브랜치를 GitHub에 push한다. 기존 운영 브랜치로 자동 배포되는
   서비스라면 해당 코드가 실제 배포 대상으로 지정돼야 한다. 환경변수만 바꿔서는 새 구현이 생기지 않는다.
2. 기존 Supabase 프로젝트 SQL Editor에 `supabase/migrations/202611020001_gpu_energy.sql`을 적용한다.
   기존 api_calls 테이블 및 이전 마이그레이션이 있는 프로젝트가 전제다. 운영 원장 데이터를 삭제하지 않는다.
3. Render의 이 저장소 API 서비스에 다음 서버 환경변수를 설정하고 같은 코드로 배포한다.

```dotenv
IMAGE_PROVIDER=zeus
GPU_IMAGE_MODEL=black-forest-labs/FLUX.2-klein-4B
ROUTER_PROVIDER=zeus
GPU_ROUTER_MODEL=Qwen/Qwen3.5-4B
GPU_API_URL=https://uoy29nqz1znz6b-8000.proxy.runpod.net
GPU_API_KEY=<현재 Pod worker.env의 동일한 비밀키>
GPU_TIMEOUT_SECONDS=150
CLIP_MODE=off
PUBLIC_APP_ORIGIN=https://ai-slop-stop.vercel.app
```

Supabase·인쇄 등 기존 환경변수는 보존한다. Gemini 키도 롤백용으로 유지할 수 있다.
API 키는 채팅·GitHub·Vercel VITE_ 변수에 넣지 않는다. 새로 만든 임의의 키로 대체하지 않는다.
현재 키는 `/workspace/ai-slop-stop/worker.env`에 있다. 로컬 Mac에서 SSH 출력을 `pbcopy`로
직접 전달하면 터미널에 출력하지 않고 Render Environment에 붙여넣을 수 있다.

Build: `pip install -r server/requirements.txt`
Start: `python -m uvicorn server.main:app --host 0.0.0.0 --port $PORT --workers 1`
Root directory: 저장소 루트. RunPod GPU 라이브러리는 Render에 설치하지 않는다.
Blueprint는 기본 ROUTER_PROVIDER=gemini를 선언하므로 추후 Blueprint 재동기화 시
환경변수 provider가 되돌아가지 않았는지도 확인한다.

4. Render `/health`가 image_provider=zeus, router_provider=zeus,
   gpu_configured=true, persistence_configured=true인지 확인한다.
   이것은 환경변수 존재 확인이며 RunPod 실호출 성공을 보장하지 않는다.
5. Vercel의 동일 코드 프런트 배포에 다음 설정을 적용하고 재배포한다.

```dotenv
VITE_USE_MOCK=false
VITE_API_BASE=https://<실제 Render API 도메인>
```

Vite 변수는 빌드에 들어가므로 재배포가 필요하다. `VITE_API_BASE`에 RunPod URL을 넣지 않는다.
기존 Vercel이 같은 Render를 보고 있으면 URL은 유지하지만 신규 비동기 생성과 Wh 표시 코드는 배포해야 한다.
Preview를 쓰는 경우 Render PUBLIC_APP_ORIGIN은 정확한 그 Preview origin으로 맞춘다.
6. 목표 선택 → 프롬프트 → 생성 → 판정 → 결과/영수증 흐름을 1회 실행한다.
   Supabase `api_calls`에 provider=zeus 및 `energy_measurement`가 기록되는지 확인한다.
   이전 Gemini 기록의 계측값 NULL은 정상이다. 전체 웹 지연은 GPU 내부 4~5초와 별개로 측정한다.

## 임시 Pod 사용 시 다음 회차

- 설치 전에 HTTP 포트와 SSH를 설정하고 코드·키·원장을 복원한다.
- 새 Pod의 GPU_API_URL이 달라지면 Render도 수정·재배포한다.
- 백업의 키를 복원하지 않고 새 키를 만들었으면 Render GPU_API_KEY도 갱신한다.
- 기존 Pod가 꺼졌거나 초기화되면 Gemini로 자동 전환하지 않고 요청이 실패한다.
  명시적 롤백은 IMAGE_PROVIDER=gemini 및 ROUTER_PROVIDER=gemini로 설정하고 재배포한다.
- 종료 전 측정 증거와 원장을 로컬 백업한다. Pod 종료는 사이트의 GPU 기능 중단을 뜻한다.
