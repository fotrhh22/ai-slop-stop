# RunPod 이미지 생성·Zeus 계측 전환 계획 및 실행 절차

작성일: 2026-11-02 (Asia/Seoul)
기준: GitHub `fotrhh22/ai-slop-stop`, `533d69f` (CLIP worker 병합 이후).
작업 브랜치: `codex/zeus-gpu-migration`. 운영 배포·DB 변경·유료 GPU 생성은 아직 하지 않았다.

## 1. 전환 범위와 현재 상태

1차에서는 **이미지 생성만 Gemini → 직접 운영하는 RunPod API**로 바꾼다.
기존 Gemini Router와 CLIP worker, 세션/첫 PASS 영수증, Supabase Storage, 자동 인쇄 구조를 유지한다.
Gemini를 완전히 제거하는 단계가 아니다. 이미지 판정까지 원격 VLM으로 바꾸려면 품질·VRAM·판정 기준을 별도로 검증해야 한다.

| 구성 | 현재 운영 | 전환 작업본 |
|---|---|---|
| 프런트 | Vercel React/Vite | 유지. 생성 작업 접수 후 polling, Wh 표시 추가 |
| 업무 API | Render FastAPI | 유지. 이미지 공급자 스위치, 원격 GPU 호출 추가 |
| 이미지 생성 | Gemini 이미지 API | RunPod GPU Pod 1개 / GPU 1장 / Diffusers |
| 판정 | CLIP + Gemini Router | 최신 GitHub 로직 유지 |
| 소비량 | API 사용 토큰 | 생성 GPU의 Zeus 계측 J + Wh, 토큰은 별도 기록 |
| 보존 | Supabase 시도·호출·성공 원장 | 호출별 `energy_measurement` JSON 추가 |
| 인쇄 | 기존 프린터 브리지 | 실측 확인분·미계측 생성 횟수 추가 |

코드는 로컬 계약·회귀 테스트를 위한 구현이다. GPU 실행, 실제 NVML 접근, 클라우드 HTTP proxy 대기 제한,
SQL 적용, 실물 프린터 출력까지 검증되었다는 뜻은 아니다. `/health`의 `gpu_configured`도 설정 유무이며 GPU 연결 성공 검사가 아니다.

## 2. 요청 흐름

```text
Vercel browser
  POST /api/challenges/{cid}/generations {attempt_id, prompt}
       ↓ X-Challenge-Token
Render: authorize → 작업 ID 반환(202) → background generation
       ↓ 서버 전용 Bearer GPU_API_KEY
RunPod: POST /v1/generate {request_id=attempt_id, prompt}
  요청 ID 원장 조회 → GPU 단독 잠금 → Zeus begin_window
  → Diffusers 이미지 생성 → Zeus end_window → PNG 인코딩
  → SQLite에 이미지·계측 결과 저장 → JSON 응답
       ↓
Render: 요청 ID·모델·이미지 SHA-256·계측 계약 검증
  → api_calls 저장 → attempts 이미지 해시 기록 → generation job 완료
       ↓
Browser GET /api/challenges/{cid}/generations/{attempt_id}
  → 생성 이미지와 GPU Wh 표시
  → 기존 Try/STAFF → 첫 PASS 영수증 → 기존 자동 인쇄
```

Render도 **1 worker / 1 replica**, GPU도 **1 worker / 1 GPU**로 운영한다.
현재 generation job 상태는 Render 메모리에 있으므로 다중 replica로 늘리려면 DB 작업 큐로 교체해야 한다.
완료 이미지가 포함된 작업 결과는 최대 8개이며 10분 지난 완료 작업은 다음 작업 접수 때 정리한다.
GPU가 바쁘면 무제한 대기열에 쌓지 않고 접수를 거절한다.

## 3. 측정 경계

- Zeus GPU 에너지(J)를 원본으로 보존하며 `Wh = J / 3600`으로 계산한다.
- **모델 로딩·워밍업·시작 계측 시험을 제외하고, 생성 pipeline 실행 구간만 측정**한다.
- 이 구간의 GPU 유휴 소비도 포함한 GPU 보드 범위다. 유휴 baseline을 임의로 빼지 않는다.
- CPU·메모리·전원장치 손실·냉각·네트워크·Gemini 판정은 제외한다. PUE를 임의로 곱하지 않는다.
- GPU UUID, 모델 commit SHA, 해상도·step·seed, Zeus 버전과 시간 구간을 같이 저장한다.
- 실패한 생성도 계측값을 받았다면 합산한다. 응답 유실·재시작은 미확인으로 남긴다.
- 계측 0/NaN/음수는 측정 성공값으로 표시하지 않는다. 유효값이 하나도 없으면 합계도 NULL/미계측이다.
- 영수증 표기: **누적 이미지 생성 GPU 전력량 / 실측 확인분 / 미계측 생성 요청**.
- 자전거 계측은 USB 출력에서 부하로 전달된 Wh다. 같은 단위로 비교할 수 있지만 측정 경계가 다르며 실제 상쇄량은 아니다.
- 이 모델·GPU의 결과를 Gemini·ChatGPT 또는 온라인 토큰 추정용 공통 계수로 재사용하지 않는다.

## 4. RunPod 첫 시험 구성

- 상품: GPU **Pods**, 단독 GPU 인스턴스.
- GPU: RTX 4090 24GB × 1. 재고가 없다면 3090 24GB × 1에서 별도 검증한다.
- 초기 테스트는 중단 위험이 적은 on-demand로 한다. 공급 가용성·실제 요금은 생성 직전 확인한다.
- 템플릿: Python 3.11 이상, CUDA/PyTorch가 작동하는 PyTorch 템플릿. 이 코드는 GPU UUID 속성을 사용하는 PyTorch 버전을 요구한다.
- HTTP port: 8000. SSH/Jupyter는 운영자가 접속할 때만 이용한다.
- `/workspace`에 모델 캐시·요청 원장을 저장한다. Pod 제거 시 보존 조건을 확인하고 결과를 먼저 export한다.
- 초기 디스크 예산: 모델 캐시·패키지·시도 결과를 고려해 40~60GB부터 시험하되 실제 사용량을 확인한다.
  SQLite에는 PNG base64도 들어가므로 행사 후 원장 export와 보관 정책이 필요하다.
- GPU 대여료 외 storage 등 비용이 있고, 모델 다운로드·워밍업 시간도 대여 시간에 포함된다.

**초기 모델은 SDXL base 1.0 / 1024×1024 / 30 steps / batch 1 / CFG 5.0.**
이는 실행·계측 검증용 출발점이다. 실제 운영 모델은 아래 품질 시험 후 확정한다.
SDXL의 텍스트 인코더 길이 제한·한국어 이해·복합 목표 재현은 기존 Gemini와 다르다.
프롬프트를 몰래 번역하거나 API 토큰을 SDXL 토큰으로 위장하지 않는다. 길이가 긴 입력은 모델에서 잘릴 수 있다.
공개 행사 전에는 생성물 검수와 운영자 통제 등 기존 Gemini 제공 기능과 달라진 운영 조건도 확인한다.

## 5. RunPod 구축 순서

### A. 코드와 환경 준비

제공된 `zeus-gpu-source.tar.gz`는 worker에 필요한 파일만 포함하며 `.env`·키·웹 전체 코드를 포함하지 않는다.
RunPod의 `/workspace/ai-slop-stop/`에 압축을 풀고 그 디렉터리에서 실행한다.
GitHub에서 받는 경우에는 이 작업 브랜치가 실제 push된 뒤 정확한 commit으로 checkout한다. 현재 main에는 아직 이 변경이 없다.

```bash
cd /workspace/ai-slop-stop
python --version
nvidia-smi
python -m pip install -r gpu_worker/requirements.txt
python -m zeus.show_env
```

PyTorch는 템플릿에 설치된 CUDA 호환 버전을 사용한다. 없으면 해당 CUDA 환경용 PyTorch를 먼저 설치한다.
GPU 라이브러리는 Render에 설치하지 않는다.

RunPod 환경변수 화면에 다음을 설정한다 (`gpu_worker/.env.example` 참고).

| 변수 | 값 |
|---|---|
| `GPU_API_KEY` | 32자 이상 난수. Render와 동일한 값, 브라우저에는 넣지 않음 |
| `GPU_IMAGE_MODEL` | `stabilityai/stable-diffusion-xl-base-1.0` |
| `GPU_MODEL_REVISION` | 모델 저장소의 **40자리 immutable commit SHA**, 필수 |
| `HF_HOME` | `/workspace/huggingface` |
| `GPU_LEDGER_PATH` | `/workspace/ai-slop/requests.sqlite3` |

모델 SHA는 모델 페이지의 Files/history에서 확인하거나 다음으로 조회 후 고정한다.

```bash
python -c 'from huggingface_hub import model_info; print(model_info("stabilityai/stable-diffusion-xl-base-1.0").sha)'
```

API 키는 비밀 관리자 등으로 생성하고 환경변수 편집기에만 입력한다. `.env` 파일을 shell로 source할 필요는 없다.
플랫폼이 공급하는 GPU 할당/가시성 설정을 유지한다. 이 worker에는 정확히 GPU 한 장만 보여야 한다.

```bash
python -m uvicorn gpu_worker.app:app --host 0.0.0.0 --port 8000 --workers 1
```

서버는 모델 로딩·워밍업·실제 Zeus counter 시험을 통과한 후 요청을 받는다. 이 단계가 실패하면 지원 여부/드라이버/컨테이너를 먼저 점검한다.
`GPU_API_KEY`가 짧거나 모델 SHA가 없으면 시작하지 않는다. 파일 잠금으로 같은 원장을 쓰는 2번째 프로세스도 막는다.

### B. GPU 단독 시험

운영자 환경에 `GPU_API_URL`과 `GPU_API_KEY`를 설정한 뒤 실행한다. URL은 Pod의 HTTPS 8000 proxy URL 또는 SSH tunnel의 localhost 주소다.
명령에 키를 직접 붙이지 않는다.

```bash
python -m gpu_worker.smoke_test --count 1 --output gpu-smoke.json
python -m gpu_worker.smoke_test --count 10 --output gpu-benchmark.json
```

smoke test는 CPU 쪽에서도 실행할 수 있으며 pydantic만 있으면 된다. GPU 패키지가 설치된 worker에서도 실행 가능하다.
이미지는 출력하지 않고 request ID·시간·Wh·모델/GPU 조건을 증거 JSON에 남긴다. 이 호출은 실제 GPU 연산과 비용을 발생시킨다.
HTTP 응답 유실 시 자동 재생성하지 않는다. 기록한 ID로 인증된 `GET /v1/requests/{id}`를 조회해 원격 결과를 조사한다.

### C. DB와 Render 연결

1. Supabase에 기존 migrations와 최신 CLIP migration을 유지하고 `202611020001_gpu_energy.sql`을 적용한다.
   추가 컬럼만 도입하며 기존 Gemini 호출은 NULL이다. 아직 적용하지 않았다면 GPU 호출 전에 스키마 오류로 중단된다.
2. 스테이징 Render에 이번 backend 코드를 배포한다. 아래 변수 외 기존 DB·인쇄·Router·CLIP 설정은 보존한다.
3. `/health`의 공급자/모델 설정과 GPU 인증 health를 각각 확인한다.

```text
IMAGE_PROVIDER=zeus
GPU_IMAGE_MODEL=stabilityai/stable-diffusion-xl-base-1.0
GPU_API_URL=https://<Pod의 8000 proxy 주소>
GPU_API_KEY=<RunPod와 같은 서버 전용 비밀>
GPU_TIMEOUT_SECONDS=150
```

GPU worker와 Render의 모델 ID는 일치해야 한다. Gemini Router 때문에 `GEMINI_API_KEY`는 계속 필요하다.
자동으로 Gemini 이미지 생성으로 fallback하지 않는다. 장애 중 계측되지 않은 이미지를 측정 이미지로 오인하지 않도록 명시적 오류를 반환한다.
Render의 요청 제한/timeout과 GPU proxy 제한을 실제 생성시간과 함께 확인한다. 브라우저 polling은 긴 브라우저 연결 문제를 줄이지만 Render→GPU의 upstream timeout까지 없애지는 않는다.

### D. 프런트·영수증·인쇄 시험

1. Vercel preview를 이번 backend 스테이징에 연결하고 `VITE_USE_MOCK=false`로 빌드한다.
2. 기존 challenge 생성 → 이미지 생성 → FAIL 후 재생성 → PASS/STAFF → 영수증 재조회 → 출력 순서로 시험한다.
3. 화면의 생성 모델명이 SDXL을 가리키고, 시도 카드와 영수증 Wh가 원장의 J/3600과 일치하는지 확인한다.
4. 미계측·실패 호출을 포함한 세션에서 부분 합계와 누락 횟수가 유지되는지 확인한다.
5. 프린터 브리지도 이번 renderer로 업데이트한다. PNG 렌더링만으로 실제 출력 완료를 주장하지 않는다.

## 6. 운영 전 합격 조건

| 시험 | 합격 조건 |
|---|---|
| GPU 계측 접근 | 실제 인스턴스에서 Zeus probe와 10회 연속 생성에 유효한 J·UUID·모델 SHA 기록 |
| 반복 요청 | 같은 ID+prompt를 재전송해도 같은 결과, 새 GPU 실행 없음 |
| 동시 요청 | 측정 창이 겹치지 않음. 초과 요청은 busy로 거절 |
| 생성 실패 | 실패 구간의 계측값은 보존하거나 미계측으로 명시, 0으로 대체하지 않음 |
| 네트워크 유실 | 같은 ID로 상태 조회, 자동으로 새 생성하지 않음 |
| worker 재시작 | 완료 결과 재사용; 처리 중이던 ID는 unknown으로 남음 |
| Render 재시작 | 메모리 작업 소실을 interrupted로 표시, 새 생성을 자동 실행하지 않음 |
| 누적/영수증 | 실패 포함 생성 호출 합계, 판정 제외, 재조회·재인쇄 시 값 일치 |
| 품질 | 기존 목표 이미지 5종 이상, 한국어·긴 문장·복합 피사체 입력을 기존 운영 흐름으로 검증 |
| 성능 | 행사에 허용되는 생성 대기시간·시간당 처리 인원을 실측 후 확정 |
| 현장 | 자전거 실측과 비교, 정지·재출발·세션 초기화 및 영수증 출력 확인 |

Zeus와 nvidia-smi는 같은 NVML 계통일 수 있다. 둘의 일치는 외부 전력계로 교정했다는 증거가 아니다.
모델·GPU·step이 바뀌면 품질·소비량·처리시간을 다시 측정한다. 더 큰 Wh를 만들려고 불필요하게 모델을 무겁게 설정하지 않는다.

## 7. 장애와 롤백

- 생성 실패/응답 미확인은 API 원장에 failed/unknown으로 남는다. 자동 재생성하지 않는다.
- pending 상태에서 Render가 죽으면 현재 원장의 pending을 자동 확정하지 않는다. 종료 원장과 GPU 요청 기록을 운영자가 대조해야 한다.
  미완료 세션은 보류/새 세션으로 처리하고, SQL로 임의 성공 처리하지 않는다.
- 성공 영수증은 기존 active-challenge guard로 호출 원장이 닫힌 후 재조회한다.
  응답 유실 뒤에 결과를 찾더라도 이미 발급된 영수증의 누락값을 몰래 바꾸지 않는다.
- GPU가 없는 상황에서는 `IMAGE_PROVIDER=gemini`로 되돌리고 `GEMINI_IMAGE_MODEL`을 기존 값으로 유지한다.
  프런트는 동일 polling API를 계속 사용한다. Gemini 호출의 에너지는 미계측으로 표시한다.
- SQL 추가 컬럼은 롤백 때 삭제할 필요가 없다. 이미 발급된 GPU 영수증과 증거는 보존한다.
- 행사 전·행사 중에는 동일 Pod를 유지하고 모델 warm 상태를 확인한다. 작업 종료 후 자료를 export하고 GPU 대여를 종료한다.

## 8. 이 작업에서 검증한 것과 다음 실행

로컬에서 서버 회귀·계측 계약·중복 방지·실패 구간 테스트, 프런트 빌드·생성/판정 client 테스트를 수행한다.
테스트의 가상 3600J는 단위 변환 검증용이며 실제 생성 에너지 측정 결과가 아니다.
GPU 패키지 조합은 실제 CUDA 환경에서 설치·시험한 뒤 `pip freeze`를 보존하고, 재현 가능한 이미지 digest로 고정한다.
현재 requirements는 설치 범위이며 GPU에서 검증된 lockfile이 아니다.

바로 다음 실행은 **RunPod 4090 Pod 한 개 확보 → worker 파일 업로드 → 모델 SHA·키 설정 → 단독 생성 1회/10회 측정**이다.
유료 자원 생성·결제, 실제 DB SQL 적용, 운영 배포는 별도의 실행 단계이며 이 작업에서는 수행하지 않았다.

## 공식 참고

- [RunPod Pods](https://docs.runpod.io/pods/overview)
- [RunPod templates](https://docs.runpod.io/pods/templates/manage-templates)
- [Zeus measurement API](https://ml.energy/zeus/measure/)
- [Zeus GPU window reference](https://ml.energy/zeus/reference/monitor/energy/)
- [SDXL model card](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)
