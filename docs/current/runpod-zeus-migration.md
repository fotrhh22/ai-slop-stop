# FLUX.2 Klein 4B + Qwen3.5-4B 전환 실행 계획

갱신: 2026-11-03 (Asia/Seoul). 브랜치 `codex/zeus-gpu-migration`.
기준 GitHub commit `533d69f` 이후의 별도 작업본이다. 원래 작업 폴더의 미커밋 UI 변경은 건드리지 않는다.

현재 운영 선택(2026-11-03): 유료 Network Volume 이전은 보류한다. 단기 시험은 임시 Pod에서 수행하고
설정 편집 전 및 종료 전에 로컬로 백업한다. 새 컨테이너에서는 설치·모델 다운로드를 다시 수행할 수 있다.
8000 API 복구 및 외부 인증 health 검증은 완료했다. Render/DB/웹 운영 전환은 아직 수행하지 않았다.

## 2026-11-03 실제 시험 및 복구 기록

- RTX 5090 32GB에서 두 BF16 모델 상주, 생성·판정·Zeus 계측에 성공했다.
- 동일 이미지 비교 3회에서 Pod 내부 API 왕복 평균 4.47초, 생성+판정 평균 0.323Wh였다.
  이는 판정 정확도 시험이나 실제 웹사이트 전체 지연 시험이 아니다. 운영 전환은 아직 하지 않았다.
- HTTP 8000 추가 후 컨테이너 재생성이 발생했다. `/workspace`가 별도 볼륨이 아닌
  `overlay`였으므로 코드·가상환경·모델 캐시가 사라졌고, SSH도 다시 시작·공개키 등록이 필요했다.
- 현재는 로컬 백업으로 단기 시험 환경을 복구 중이다. 이 Pod는 여전히 임시 저장 공간이다.

설치 **전에** `findmnt -T /workspace`를 실행한다. SOURCE/FSTYPE이 루트 `overlay`라면
`/workspace`라는 경로만으로 영속성을 가정하지 않는다. 반복 운영에는 별도 Volume Disk 또는
Network Volume을 구성하고 실제 마운트를 확인한다. 추가 스토리지 비용과 Pod 변경 영향을 먼저 확인한다.
HTTP 포트 등 Pod 편집은 모델 설치 전에 마친다. 실행 중 편집은 컨테이너 재생성 및 프로세스 중단을
유발할 수 있으므로, 임시 저장 공간의 파일과 계측 결과를 먼저 외부로 백업한다.

아래 초기 계획의 '미진행' 상태는 최초 코드 준비 시점을 설명한다. 최신 실측/복구 상태는 이 기록을 따른다.

## 현재 상태: 코드 준비와 실제 구축은 다르다

이전 작업은 SDXL 생성 코드·Zeus 계측·백엔드 원장·화면 표시의 로컬 구현이었다.
이번 변경은 생성 모델을 **FLUX.2 Klein 4B distilled**, 판정을 **Qwen3.5-4B 비추론 모드**로 바꾸는 코드다.
GPU 대여·모델 다운로드·실제 CUDA/NVML 실행·DB SQL 적용·운영 배포를 수행하지 않았다.
따라서 **계측값 검증만 남은 완성 시스템이 아니다. 모델 런타임과 실제 서비스 연결 검증도 남아 있다.**

| 구성 | 준비된 내용 | 미확인 부분 |
|---|---|---|
| GPU 생성 | Flux2KleinPipeline, BF16, 1024², 4 steps, CFG 1 | 실제 설치·VRAM·한국어 품질·지연 |
| GPU 판정 | Qwen3_5ForConditionalGeneration, BF16, 두 이미지, thinking off, 출력 상한 256 tokens | 실제 실행·JSON 준수율·판정 품질·지연 |
| 계측 | 생성/판정 각각 Zeus window, J 저장·Wh 환산 | 인스턴스 NVML 지원·counter 동작·반복성 |
| GPU API | Bearer 인증, 단일 실행 잠금, 요청 ID 영속화·중복 방지 | RunPod proxy·장시간 운영 |
| 백엔드 | 공급자 분기, 호출 원장, 작업 polling, 실패/unknown 처리 | 운영 DB·Render 연결 |
| 영수증 | 생성 Wh·판정 Wh·GPU 확인 합계, 미계측 횟수 | 실제 웹 흐름·프린터 출력 |

현재 운영 사이트는 기존 Gemini 구성이다. 기본 환경변수도 Gemini라서 이 코드를 배포하는 것만으로 자동 전환되지 않는다.
Gemini fallback은 자동으로 실행하지 않는다. 필요하면 운영자가 환경변수로 명시적으로 되돌린다.

## 목표 구조

```text
Vercel 웹 → Render FastAPI → RunPod (GPU 1장, worker 1개)
                             ├ FLUX.2 Klein 4B distilled → 생성 이미지 + 생성 J
                             └ Qwen3.5-4B → PASS/FAIL JSON + 판정 J
                     ↓
            Supabase 호출 원장 → 웹/영수증/기존 프린터 브리지
```

두 모델은 시작할 때 로딩·워밍업하고 GPU에 상주시킨다. 요청마다 모델 교체·CPU offload를 하지 않는다.
생성/판정 API는 같은 잠금을 써서 한 GPU에서 계측 window가 겹치지 않도록 한다.
기존 CLIP worker는 보존하되 **첫 비교 시험에서는 CLIP_MODE=off**로 설정해 Qwen 자체 성능과 시간을 확인한다.
CLIP을 다시 켜면 CLIP 소비량은 이 GPU 합계에서 제외하며, 조기 FAIL 시 Qwen 요청이 없을 수 있다.

## Phase 0 — 코드 확정·로컬 검증 (이번에 진행)

담당: Codex. 사용자는 GPU 결제를 아직 할 필요가 없다.

- SDXL → FLUX.2 Klein 4B distilled 전환.
- Qwen 판정 API, 명시적 provider switch, non-thinking·최대 출력 길이 설정.
- 생성과 판정 각각 계측·저장·표시. 판정 JSON 오류도 소비량이 확인되면 원장에 보존.
- HTTP 중복 요청, timeout, 재시작, 계측 단위/부분 합계, 기존 기능 회귀 테스트.
- 업로드 압축파일과 아래 실행 명령 준비.

완료 기준: 로컬 테스트·프런트 빌드 성공. 이것은 실제 GPU 시험을 대체하지 않는다.

## Phase 1 — GPU 인스턴스 확보 (사용자)

1. RunPod에서 **Pods**를 선택한다. 이미지 생성 API만 제공하는 managed endpoint를 선택하지 않는다.
2. **48GB VRAM 단일 NVIDIA GPU**를 초기 시험 후보로 확보한다. 예: RTX 6000 Ada 48GB / L40S 48GB.
   이는 두 모델 상주와 작업 메모리를 위한 계획상 권장치이며, 15초 보장이나 실측 VRAM 수치가 아니다.
   4090 24GB에 두 모델이 여유 있게 상주한다고 가정하지 않는다. 80GB GPU도 시험 후보지만 비용을 함께 확인한다.
3. Python 3.11+, CUDA/PyTorch가 준비된 템플릿. HTTP port 8000을 노출한다.
4. 모델 캐시·원장용 `/workspace` 디스크를 확보한다. 초기 60GB 이상을 계획하되 실제 모델 파일 크기와 여유 공간을 확인한다.
5. 요금·스토리지 비용을 확인하고 짧은 on-demand 시험부터 시작한다.

사용자가 준비할 정보: **Pod ID, GPU 종류, HTTPS 8000 proxy URL, SSH 접속 방법**.
API 키·SSH 개인키를 채팅이나 GitHub에 올리지 않는다. 운영 환경 편집기에 직접 넣는다.

완료 기준: `nvidia-smi`에 할당된 GPU 한 장이 보이고 CUDA/PyTorch 실행이 가능하다.

## Phase 2 — GPU worker 설치·시작 (사용자 환경에서 실행)

압축파일 `zeus-gpu-source.tar.gz`를 `/workspace/ai-slop-stop/`에 푼다. 파일 업로드는 RunPod가 제공하는 전송/SSH 경로로 한다.
GitHub main에는 이 코드가 아직 없으므로, 압축파일 대신 clone하려면 작업 브랜치를 먼저 push해야 한다.

```bash
cd /workspace/ai-slop-stop
python --version
nvidia-smi
python -m pip install -r gpu_worker/requirements.txt
python -m zeus.show_env
```

새 FLUX/Qwen 지원 때문에 Diffusers 최신 소스와 Transformers 5.2 이상을 사용한다.
실제 GPU 시험 후 설치 버전 목록과 소스 commit을 저장하고 재현 가능한 이미지로 고정한다. 현재 requirements는 GPU 검증된 lockfile이 아니다.
Qwen의 고속 attention/DeltaNet 커널이 없는 환경은 느릴 수 있어, 실제 trace를 확인한 뒤 공식 문서에 맞게 보완한다.

두 모델의 immutable SHA를 조회한다.

```bash
python -c 'from huggingface_hub import model_info; print(model_info("black-forest-labs/FLUX.2-klein-4B").sha); print(model_info("Qwen/Qwen3.5-4B").sha)'
```

RunPod 환경변수 편집기에 입력한다.

```text
GPU_IMAGE_MODEL=black-forest-labs/FLUX.2-klein-4B
GPU_MODEL_REVISION=<첫 번째 40자리 모델 SHA>
GPU_ROUTER_MODEL=Qwen/Qwen3.5-4B
GPU_ROUTER_REVISION=<두 번째 40자리 모델 SHA>
GPU_API_KEY=<32자 이상의 난수 비밀>
GPU_LEDGER_PATH=/workspace/ai-slop/requests.sqlite3
HF_HOME=/workspace/huggingface
```

플랫폼의 GPU 가시성 설정을 유지한다. GPU는 정확히 한 장만 보여야 한다.

```bash
python -m uvicorn gpu_worker.app:app --host 0.0.0.0 --port 8000 --workers 1
```

시작 시 두 모델 로딩·워밍업·실제 counter probe를 실행한다. Qwen warmup에서 JSON 판정도 확인한다.
잘못된 SHA·키·GPU 개수·메모리 부족·probe 실패가 있으면 서비스가 준비 상태가 되지 않는다.
인증된 `/health`에 `router_ready=true`와 두 모델 ID가 보이는지 확인한다.

완료 기준: 두 모델이 로드된 worker가 준비 상태가 되고, 시작 로그에 OOM·NVML 오류가 없다.

## Phase 3 — 생성·판정·계측 단독 시험 (사용자 + Codex 분석)

운영자 환경변수에 `GPU_API_URL`(HTTPS proxy 주소)과 동일한 `GPU_API_KEY`를 설정한다. 키를 명령 인자에 쓰지 않는다.

```bash
python -m gpu_worker.smoke_test --count 1 --with-router --output smoke.json
python -m gpu_worker.smoke_test --count 30 --with-router --target /path/to/target.png --prompt "실제 행사 프롬프트" --output benchmark.json
```

`--target`이 없으면 생성 이미지를 자기 자신과 비교하므로 **통신·형식 시험**일 뿐 품질 시험이 아니다.
`--target`을 주면 그 목표와 비교한다. 원래 Gemini에서 쓰던 목표 5종 이상과 쉬운/어려운 한국어 프롬프트를 나눠 실행한다.
smoke test는 생성마다 새 request ID, 판정마다 별도 ID를 만들며 HTTP 재시도는 하지 않는다.
응답 유실 시 ID를 기록한 JSON과 인증된 `GET /v1/requests/{id}`로 조사한다. 무작정 새 생성으로 재시도하지 않는다.

확인할 항목:

- 생성 이미지가 지시를 수행하는지. 한국어 원문을 몰래 번역·확장하지 않는다.
- 이미지 쌍에 대한 Qwen PASS/FAIL과 근거가 사람의 기준에 맞는지.
- 두 작업 각각 양수·유한 J, GPU UUID·모델 SHA·시간 구간이 기록되는지.
- 같은 ID 재전송으로 새 GPU 실행이 생기지 않는지.
- 측정할 수 없는 값은 NULL/미계측으로 남는지.
- 생성+판정의 GPU API 왕복 P50/P95와 메모리 피크.

합격 목표: GPU API 생성+판정 왕복 **P95 12초 이내**를 초기 시간 예산으로 삼고,
실제 웹 전체는 다음 단계에서 **P95 15초 이내**를 확인한다. 이 수치는 현재 측정 결과가 아니라 채택 기준이다.
30회는 초기 선별 표본이며 장시간 안정성을 증명하는 충분한 통계라고 주장하지 않는다.

Zeus와 nvidia-smi의 일치는 독립 전력계 교정이 아니다. 전력값의 타당성은 구간·반복성·측정 경계를 검증한다.
제출 증거로 smoke/benchmark JSON, GPU 사양, 모델 SHA, `pip freeze`, worker 로그를 보존한다.

## Phase 4 — Supabase·Render·Vercel 스테이징 연결 (사용자 환경 + Codex)

1. 기존 DB 구조에 `supabase/migrations/202611020001_gpu_energy.sql`을 적용한다. 기존 CLIP/인쇄 migration도 유지한다.
2. 작업 브랜치를 GitHub에 push하고 Render 스테이징을 이 코드로 배포한다. 단일 worker/replica로 운영한다.
3. Render에 아래 환경변수를 설정한다. 키는 서버 전용이며 `VITE_`로 시작하면 안 된다.

```text
IMAGE_PROVIDER=zeus
GPU_IMAGE_MODEL=black-forest-labs/FLUX.2-klein-4B
ROUTER_PROVIDER=zeus
GPU_ROUTER_MODEL=Qwen/Qwen3.5-4B
GPU_API_URL=https://<Pod 8000 proxy 주소>
GPU_API_KEY=<RunPod와 동일한 비밀>
GPU_TIMEOUT_SECONDS=150
CLIP_MODE=off
```

150초는 장애 판정용 HTTP 상한이며 서비스 속도 목표가 아니다.
Supabase·인쇄 등 기존 환경변수는 유지한다. Gemini 키는 rollback용으로 보관할 수 있으며 zeus/zeus 경로에서는 호출하지 않는다.
4. Vercel preview에서 `VITE_USE_MOCK=false`, `VITE_API_BASE=<Render 스테이징 주소>`로 연결한다.
   Render `PUBLIC_APP_ORIGIN`도 정확한 preview origin과 맞춘다.
5. 목표 선택 → 생성 → Try → FAIL/재시도 → PASS/STAFF → 영수증 재조회 → 인쇄를 시험한다.

완료 기준: 실제 DB에 `provider=zeus` 생성·판정 원장이 쌓이고 이미지 해시·계측값·화면·영수증이 일치한다.
웹에서 사용자 입력/클릭 간 대기시간을 제외한 생성+판정 처리의 P95가 15초 안에 드는지 확인한다.
GPU API 시험의 시간은 Render·Supabase·브라우저 비용을 포함하지 않으므로 이 단계를 생략하지 않는다.

## Phase 5 — 행사 리허설·운영 전환 (사용자)

- 참가자 1명씩 순차 체험하는 운영으로 먼저 검증한다. GPU가 바쁘면 큐에 무한 대기하지 않고 busy로 거절한다.
- 실제 자전거의 USB 출력 Wh와 AI GPU 실측 확인분을 비교한다.
- 정지·재출발·중복 클릭·인터넷 단절·재시작·계측 미확인·재인쇄를 시험한다.
- 단위·문구·출력량·참가자 처리시간을 확인한 뒤 운영 배포로 전환한다.
- 모델을 상주시킨 Pod를 행사 전에 켜두고 warm 상태를 확인한다. 행사 후 증거를 export하고 대여를 종료한다.

속도 또는 품질이 기준을 충족하지 못하면 운영 전환을 보류하고 원인을 측정한다.
해상도·판정 이미지 크기·출력 길이·커널·GPU 변경을 하나씩 비교한다. 품질 저하를 숨기고 step만 줄이지 않는다.

## 측정값 해석·장애·롤백

- 생성: pipeline 실행 구간. 로딩·워밍업·PNG 인코딩 제외.
- 판정: processor 입력 준비부터 GPU 생성 완료까지의 GPU 소비. 이미지 디코딩·최종 JSON 검증은 제외.
- 두 모델이 GPU에 상주하므로 각 window는 **다른 상주 모델의 유휴 소비도 포함**한다.
- 총합은 관측한 생성·판정 GPU 구간의 확인분이다. CLIP·CPU·냉각·네트워크와 요청 사이 유휴 소비는 제외한다.
- 실패한 모델 요청도 계측값을 받았다면 합산한다. 누락·unknown은 0으로 채우지 않는다.
- Qwen 토큰은 그 모델 processor의 입력/출력 토큰이다. Gemini 토큰과 동일한 정의라고 주장하지 않는다.
- 자전거는 USB 부하 전달 Wh, AI는 GPU window Wh로 서로 측정 경계가 다르며 실제 상쇄량이 아니다.
- Render 재시작 시 메모리 작업은 interrupted, GPU 재시작 중이던 요청은 unknown이다. 자동으로 새 요청을 만들지 않는다.
- 완료 영수증은 닫힌 호출 원장에 기반한다. 나중에 찾은 값으로 기존 영수증을 몰래 덮어쓰지 않는다.
- 롤백: `IMAGE_PROVIDER=gemini`, `ROUTER_PROVIDER=gemini`로 되돌리고 기존 Gemini 모델·키를 유지한다.
  기존 GPU 기록은 삭제하지 않으며 Gemini 에너지는 미계측으로 표시한다.

## 공식 자료

- [FLUX.2 Klein 4B distilled / 4-step 예제](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)
- [Qwen3.5-4B / non-thinking 설정](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Transformers Qwen3.5 구현·커널 안내](https://huggingface.co/docs/transformers/model_doc/qwen3_5)
- [Zeus 측정 API](https://ml.energy/zeus/measure/)
- [RunPod Pods](https://docs.runpod.io/pods/overview)
