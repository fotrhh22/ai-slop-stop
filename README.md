# AI SLOP STOP CHALLENGE

Google의 프롬프트 학습 서비스 **[Say What You See](https://artsandculture.google.com/experiment/say-what-you-see/jwG3m7wQShZngw)**의 이미지 재현 챌린지 형식을 가져와, AI 생성 과정의 자원 소비를 기록하고 자전거 페달링으로 체감하는 오프라인 경험으로 확장한 캠페인입니다.

목표 이미지 관찰 → 프롬프트 입력 → 이미지 생성·판정 → 누적 사용량 확인 → 참가자 코드 → 자전거 발전 체험 → 최종 영수증 출력으로 이어집니다. Google 서비스는 프롬프트 작성·이미지 관찰 학습의 레퍼런스이며, 자원 계측과 오프라인 연결은 이 캠페인에서 추가한 부분입니다.

| 접속·소스 | 링크 |
|---|---|
| 챌린지 웹 | **https://ai-slop-stop.vercel.app/** |
| 챌린지·API·GPU·프린터 코드 | https://github.com/fotrhh22/ai-slop-stop |
| 자전거 계측·대시보드 코드 | https://github.com/fotrhh22/ai-slop-bike-calculation |

**캠페인 진행 완료 — 2026-11-13 업데이트:** 운영자 확인에 따라 실제 오프라인 캠페인 진행을 완료했습니다. 이미지 생성·판정과 사용량 기록, 참가자 연계, 자전거 발전 계측, 최종 영수증 출력을 연결한 현장 운영까지 마쳤습니다. 이 저장소는 캠페인 구현과 운영 절차를 보관합니다. 행사 완료는 운영자 확인에 근거하며, 참가자 수·만족도·행사 이후 AI 사용량 변화 등 효과 지표는 별도 집계 대상입니다.

모델 후보 선정과 판정 프롬프트 비교는 행사 전 수행한 [검증 보고서](docs/model-validation-results.md)에 정리했습니다. 보고서의 소표본 벤치마크는 캠페인 전체 성과 통계와 구분합니다.

**[실제 캠페인 운영 결과 — 2026-11-12 13:00~16:00 KST](docs/campaign-20261112/README.md):** 성공 영수증 DB·AI 생성/판정 GPU 에너지·자전거 원본/환산 발전량·Mac 표본·인쇄 journal을 대조했습니다. 구간 내 영수증·자전거 종료 각 15건, 시간대별 집계, 출력 실패 후 복구를 구분해 기록했습니다. **부록에는 사용 모델 설명과 Bertazzini et al. (2025)의 구체적 소비량·실험 환경을 비교**했습니다.

## 참가자 경험과 실행 구조

```text
챌린지 웹(Vercel)
  → API(Render) → Gemini 또는 RunPod 생성·판정 → Supabase 사용량·이미지 저장
  → AI/STAFF PASS → 성공 영수증·참가자 코드·자전거 대기 기록
  → Mac 측정소에서 참가자 선택·목표 자동 입력
  → FNB58 실측 수신·페달링·환산 에너지 누적
  → 자동/수동 종료 → 종료 결과 DB 저장·인쇄 큐 생성
  → Mac printer_bridge → CUPS → PT-210
```

PASS 시 웹 영수증을 만들고 자전거 목록에 등록합니다. **AI PASS 자체로 종이를 자동 출력하지 않습니다.** 같은 참가자 코드로 AI 기록·자전거 결과·최종 영수증을 연결합니다.

## 기술 스택

| 계층 | 기술·역할 |
|---|---|
| 웹 | React 18, TypeScript, Vite 5 / Vercel |
| API | Python 3.11, FastAPI, Uvicorn, Pydantic, HTTPX / Render |
| 저장 | Supabase PostgreSQL·Storage |
| 자체 GPU | RunPod, PyTorch, Diffusers, Transformers, bitsandbytes |
| GPU 계측 | Zeus·NVML: 생성·판정 GPU 실행 구간 에너지 |
| 이미지 유사도 | 선택적 Mac CLIP ViT-B/32, MPS/CPU |
| 현장 측정소 | 별도 Bike 저장소의 FastAPI·SQLite·hidapi·Bleak |
| 출력 | Pillow·Atkinson·ESC/POS·macOS CUPS·PT-210 |

## 모델 경로와 검증 결과

기본 프로필은 Gemini입니다. 자체 GPU 프로필 `open-4090` / `open-5090`은 사용 가능한 GPU에 따라 선택하며, GPU 종류와 모델 종류는 별개입니다. 운영 생성 코드는 **Klein 9B NF4**로 고정하고 판정은 Qwen3.5-4B를 유지합니다. 4090·5090 모두 검증에 사용한 NF4 생성 모델·텍스트 인코더 로더를 사용합니다. 이전 Pod는 새 코드로 다시 준비하고 런타임을 재등록해야 합니다. Render는 모델 revision·NF4를 확인하며 기존 4B 런타임은 사용 가능으로 표시하지 않습니다. [전환·공유 키 안내](docs/klein9b-shared-credentials.md)

RTX 5090 1장·동일 한국어 프롬프트 5개·1024×1024·동일 시드 규칙, 워밍업 제외 결과:

| 생성 모델 | 설정 | 평균 생성 시간 | 평균 생성 에너지 | GPU 점유 최대 표본 |
|---|---|---:|---:|---:|
| Klein 4B | BF16·4스텝 | 1.07초 | 142.7mWh | 20.72GiB |
| **Klein 9B** | **NF4·4스텝** | **2.05초** | **291.2mWh** | **16.02GiB** |
| Qwen-Image-2512 | NF4＋Lightning·4스텝 | 2.26초 | 313.8mWh | 22.45GiB |
| Z-Image-Turbo | BF16·9스텝 | 2.99초 | 446.6mWh | 25.57GiB |

실제 생성 이미지·지시 이행 분석·판정/CLIP 비교는 **[모델 검증 결과](docs/model-validation-results.md)**에서 확인할 수 있습니다. NVML 메모리 최대는 50ms 표본입니다. 단독 모델 수치를 웹 전체 시간·4090 성능·동시 상주 메모리로 해석하지 않습니다.

판정 비교는 저장된 10쌍(사전 PASS 4 / FAIL 6)을 사용했습니다.

| 판정 | 기준 일치 | 정답→FAIL | 오답→PASS |
|---|---:|---:|---:|
| Qwen 기존 | 8/10 | 0/4 | 2/6 |
| Qwen 목표별 체크리스트 | 10/10 | 0/4 | 0/6 |
| Gemini 기존 | 9/10 | 1/4 | 0/6 |

운영 우선순위는 **적절한 결과를 FAIL시키는 오류를 줄이는 것**입니다. 체크리스트는 추가 정보를 받았으므로 모델 자체의 순위로 해석하지 않습니다. 개선안은 아직 운영 프롬프트에 적용하지 않았습니다.

### CLIP

LLM의 PASS/FAIL과 서술형 이유만으로는 결과 간 유사성을 같은 수치 기준으로 비교하기 어렵습니다. 이를 보완하고 판정 검토의 객관성을 높이기 위해 **CLIP 이미지 임베딩 간 코사인 유사도를 보조값으로 함께 기록**합니다. 이 값은 비교 가능한 별도 수치 근거이며, 정답 확률이나 단독 합격 기준은 아닙니다.

Render의 제한된 메모리에 PyTorch·CLIP 가중치와 추론 메모리를 상주시켜 API 서버와 경쟁시키지 않도록 **모델은 로컬 Mac에서 실행**합니다. Render는 비교 작업을 관리하고 목표/생성 이미지 사본을 로컬 워커에 전달합니다. 워커는 임베딩을 로컬에서 계산한 뒤 **코사인 점수와 모델·전처리·실행 시간 등 메타데이터만 Render에 반환**합니다. 임베딩 벡터나 모델 가중치를 서버로 전송하는 구조가 아닙니다.

```text
Render: 비교 작업·이미지 준비
  → Mac CLIP 워커: 작업 수신 → 두 이미지 임베딩 → 코사인 계산
  → Render: 점수·실행 메타데이터 수신 → 판정 기록에 연결
  → 챌린지 응답에서 보조값 제공
```

Mac이 Render에 HTTPS로 작업을 요청하는 방식이므로 Mac에 공인 인바운드 포트나 터널을 열지 않습니다. 모델을 RunPod에서 실행하는 생성·판정 경로와도 분리됩니다. 운영 모드의 작업 전달은 Render를 거치며, 저장된 10쌍을 비교한 **오프라인 벤치마크만** Render 없이 실행했습니다.

[운영 워커](clip_worker/README.md)는 Mac에서 이미지 임베딩 코사인을 계산해 API에 전달하는 선택적 구성입니다. `observe`는 점수 기록, `gate`는 설정한 임계값 미만에서 후속 판정을 생략하는 기능입니다. 현재 비교 표본에서는 정답·오답 점수가 겹쳤으므로, 낮은 CLIP 점수만으로 탈락시키는 기준을 채택하지 않습니다. 코사인은 정답 확률이나 일치율이 아닙니다.

운영과 분리된 [오프라인 비교](docs/clip-offline-audit.md)는 저장된 이미지 쌍을 Mac에서 읽고 기존 판정과 함께 CSV·JSON·HTML로 내보냅니다. Render·RunPod·Gemini 호출이나 운영 DB 변경은 없습니다. 최초 실행에는 CLIP 가중치 다운로드가 필요할 수 있습니다.

## 사용량·에너지 기록

- **Gemini:** `gemini-3.1-flash-lite-image` 생성, `gemini-3.5-flash-lite` 판정. 실제 모델 설정은 `/health`에 표시합니다. 영수증에는 제공된 토큰만 기록하며 Wh로 임의 환산하지 않습니다.
- **자체 GPU:** 생성·판정별 Zeus/NVML 에너지와 모델·revision·GPU·실행 시간 등을 기록합니다. 첫 PASS까지 측정된 누적 GPU 에너지를 비교 목표로 사용합니다. 필요한 계측이 누락되면 목표를 확정하지 않습니다.
- **계측 범위:** GPU 실행 구간의 에너지이며 상주 모델 유휴 소비가 포함될 수 있습니다. CLIP·CPU·냉각·네트워크·모델 다운로드를 포함한 서비스 전체 소비량은 아닙니다. 미계측은 0이 아닙니다.
- 타임아웃은 FAIL과 구분합니다. 공급자 처리 여부가 미확인인 요청을 자동 재시도하지 않고 호출 원장에 남깁니다. 영수증 저장 재시도는 모델을 다시 호출하지 않습니다.

## ID·DB·자전거 연결

```text
challenges(UUID) → attempts·api_calls → successes
  → campaign_entries(8자리 코드·모델/사용량 스냅샷·목표)
  → bike_results(자전거 session UUID·최종값·종료 사유)
  → print_jobs(lease·출력 상태·CUPS 작업 ID)
```

내부 challenge UUID는 시작 시, 현장 참가자 코드는 PASS 시 생성합니다. 코드는 고유 제약·충돌 재시도로 중복을 방지합니다. AI 또는 STAFF PASS를 구분하고 원래 판정·사용량을 보존합니다. STAFF ON/OFF는 별도 인증/RBAC 기능이 아닙니다.

Mac은 측정소별 waiting/measuring 목록을 동기화합니다. 선택하면 코드·목표를 표시하고, 시작 시 서버 claim으로 해당 참가자를 세션에 연결합니다. 실측 모드·최근 유효 수신·유효 목표·claim 성공이 필요합니다. 측정 표본은 Mac SQLite에서 누적하고 로컬 대시보드에 전달하며, 종료 결과는 outbox로 클라우드에 동기화합니다.

- 자체 GPU 목표: 참가자의 첫 PASS까지 누적 생성＋판정 GPU 실측 에너지.
- Gemini 목표: **346.3mWh 고정 비교 기준**. 과거 RTX 4090 Klein 4B BF16＋Qwen INT8 3회 평균이며 **Gemini 소비량 측정/추정값이 아닙니다**. [상세](docs/gemini-reference-bike-target.md)
- 미통과 기록은 [24시간 자동 정리](docs/abandoned-session-cleanup.md), 사용하지 않은 대기 기록은 [목록 × 삭제](docs/delete-waiting-participant.md)를 지원합니다.

### ×100 환산

현재 장치의 대표 실측 0.55W를 **55W급 발전 비교 기준**으로 확대하기 위해 고정 100배를 적용합니다. 55W는 모든 자전거 발전기의 공통 출력이나 현재 장비의 실제 출력이 아닙니다.

```text
환산 발전량 = 원본 실측 Wh × 100
환산 달성률 = 환산 발전량 / 목표 Wh × 100
```

원본·배율·환산값은 구분해 저장합니다. 대시보드는 배율을 안내하고, 최종 종이 영수증은 “환산 발전량”·“환산 달성률”로 표시합니다. 종이 길이를 줄이기 위해 상세 배율 설명은 인쇄에서 제외했습니다. 실제 출력이 0.36W이면 환산 출력도 36W이며 항상 55W가 되는 것은 아닙니다. 0.55W 유지 시 Gemini 비교 목표 346.3mWh에 약 22.7초가 필요합니다. 실제 AI 전력 공급·상쇄를 뜻하지 않습니다.

## 종료·최종 영수증·PT-210

| 이벤트 | 결과 |
|---|---|
| AI/STAFF PASS | 웹 영수증·자전거 대기 등록, 종이 자동 출력 없음 |
| 원본 Wh×100 ≥ 목표 | 누적 자동 종료·최종값 고정 |
| 미달 상태의 수동 종료 | 최종값 고정, 동일하게 인쇄 대상 |
| 종료 결과 서버 저장 | bike_results 저장＋참가자 ended＋print_jobs 생성 |
| 프린터 브리지 작업 수신 | 최종 영수증 렌더링·CUPS 전송 |
| 같은 세션의 SENT＋CUPS ID 확인 | 측정 화면 초기화·다음 참가자 선택 대기 |

네트워크가 끊기면 Mac에 결과를 보존하고 재연결 후 전송합니다. 같은 종료 이벤트를 재전송해도 인쇄 작업을 추가하지 않습니다. journal·lease로 중복을 방지하며 전송 여부가 불명확하면 UNKNOWN으로 남기고 자동 재출력하지 않습니다. SENT는 CUPS 접수이고, 실제 종이 배출 감지 신호는 아닙니다. 실제 PT-210 출력 검증은 완료했습니다.

최종 영수증은 58mm 용지의 48mm/384도트 인쇄 폭을 사용합니다. 성공 이미지는 232도트 폭으로 중앙에 배치하고 **Atkinson 오류 확산**을 적용하며, 문자는 흑백으로 선명하게 렌더링합니다. 생성·판정 횟수, 평균 프롬프트 길이, GPU 에너지 또는 Gemini 토큰, 자전거 환산 결과·목표·달성률·측정 시간, 성공 프롬프트와 해당 시 통과 이유를 담습니다.

하단에는 AIMI 접속 QR과 “AIMI 체험하기 · 테스트 운영 중 / 접속 코드 발급 후 이용하세요”를 표시합니다. 참가자 코드·승인 방식·모델 목록·유효 계측률·통신 방식·상세 종료 사유·GPU 범위 설명·마지막 브랜드 문구는 인쇄에서 제외했습니다. 참가자 코드는 DB와 대시보드의 매칭에 계속 사용합니다. 같은 예시 내용 기준 배출 여백 포함 약 **19.7cm**이며, 프롬프트·통과 이유 길이에 따라 달라지는 가변 길이입니다. [프린터 설치·실행](printer_bridge/README.md)

## 로컬 개발

### API

```sh
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
cp .env.example .env.local
# .env.local에 Gemini·Supabase 등 실제 설정 입력
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --workers 1
```

API 기본 의존성에는 GPU/CLIP 모델이 없습니다. GPU는 Pod에서, 선택적 CLIP은 Mac에서 별도 실행합니다. 환경변수 우선순위는 셸/호스트 → 루트 `.env.local` → `.env`입니다.

### 웹

```sh
cd web
npm ci
cp .env.example .env.local
npm run dev
```

`VITE_USE_MOCK=false`는 실제 API, `true`는 샘플입니다. 분리 배포는 `VITE_API_BASE`와 서버의 `PUBLIC_APP_ORIGIN`을 맞춥니다. 운영 웹은 참가자 영수증에서 현장 프린터를 직접 조작하지 않습니다.

### 현장 프로그램

자전거 프로그램은 [Bike 저장소](https://github.com/fotrhh22/ai-slop-bike-calculation#실행)에서 별도 터미널로 실행합니다. 이 저장소에서는 다음을 실행합니다.

```sh
python -m pip install -r printer_bridge/requirements.txt
python -m printer_bridge.worker --env printer_bridge/.env.local --status
python -m printer_bridge.worker --env printer_bridge/.env.local --run
```

`PRINT_WORKER_TOKEN`은 Render와 같은 값, `PRINT_CUPS_QUEUE`는 실제 설치 큐를 사용합니다. 기존 출력 journal은 임의 삭제하지 않습니다.

## 배포·GPU 운영 문서

- [DB migration 순서](supabase/README.md): 기존 DB에는 적용하지 않은 후속 migration을 순서대로 적용합니다.
- [자전거·프린터 적용](docs/campaign-bike-rollout.md), [STAFF 복구 SQL](supabase/migrations/202611040005_restore_staff_pass.sql)
- [모델 프로필·GPU 준비](docs/model-profiles-and-gpu-preparation.md), [RunPod 자동화](docs/runpod-automation.md)
- [Render–RunPod 연결](docs/render-runpod-connection.md), [Pod 복구·백업](docs/runpod-session-operations.md)
- [생성 후보 실행](docs/image-model-benchmark.md), [판정 비교](docs/router-benchmark.md), [검증 결과·대표 이미지](docs/model-validation-results.md)

새 Pod는 SSH 공개키·HTTP 8888/8000 설정 후 `./runpod prepare --profile open-4090` 또는 `open-5090`을 사용합니다. 같은 GPU 종류 1/2장을 지원합니다. 준비 스크립트의 자동화는 임시 저장소를 영구 저장소로 바꾸지 않습니다. 네트워크 볼륨은 모델 캐시·파일 보존용이며 VRAM을 늘리지 않습니다.

Render는 `server/requirements.txt`만 설치하고 `python -m uvicorn server.main:app --host 0.0.0.0 --port $PORT --workers 1`로 실행합니다. Vercel Root Directory는 `web`입니다. SQL·프로필·측정소·프린터 토큰을 포함한 실제 설정은 위 적용 문서를 따릅니다.

## 개발 검증

```sh
python -m unittest discover -s server/tests -v
(cd web && npm run test:evaluation && npm run build)
python -m unittest benchmarks.test_clip_local
```

실제 장비·현장 검증과 자동 테스트를 구분합니다. 원시 결과·캐시·가상환경·`.env`·API 키·개인 백업·출력 journal은 Git에 넣지 않습니다. 모델 비교의 전력량은 실제로 측정한 범위만 설명하며, 토큰이나 미계측 값을 0Wh로 바꾸지 않습니다.
