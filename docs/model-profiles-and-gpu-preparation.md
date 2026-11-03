# 모델 선택·GPU 준비 (2026-11-04)

**최신 Gemini 자전거 규칙:** [346.3 mWh 고정 비교 목표](gemini-reference-bike-target.md)를 사용한다.
Gemini AI 사용량은 토큰만 기록하며 자전거 목표를 수동 입력하지 않는다.

## 단계 재정리

| Phase | 내용 | 이번 작업 |
|---|---|---|
| 0–5 | PASS 목록 → 자전거 ×100 환산 → 종료 시 영수증 출력 | 기존 구현 유지 |
| 6 | Gemini / 오픈모델 4090 / 오픈모델 5090 선택·호출 분리 | 구현 |
| 7 | 운영자 명령으로 기존 Pod 시작·설치/복원·로딩·인증·주소 자동 등록 | 구현 |
| 8 | 전체 현장 검증: 브라우저·Render·GPU·자전거·PT-210 | 사용자 직접 진행 |
| 9 | 대체 오픈모델 품질·속도·메모리·전력 검증 후 교체 | 이후 진행 |

Gemini AI 사용량은 **토큰만 표시**하고 Wh를 추정하지 않는다. 자전거에는 별도의 GPU 실측 평균 비교 목표를 사용한다.
현재 오픈모델은 FLUX.2-klein-4B distilled(4 steps) + Qwen3.5-4B를 그대로 사용한다.
4090은 Qwen INT8, 5090은 BF16이다. 이 메뉴의 '사용 가능'은 연결/인증/런타임 확인을 뜻하며,
새로운 이미지 품질 검증을 통과했다는 뜻이 아니다.

## Phase 6: 동작

- 목표 이미지 선택 모달에서 모델 선택. 기본값은 Gemini다.
- `/api/model-profiles`가 연결 상태를 확인한다. 4090/5090 키·등록 기록이 없거나 Pod가 꺼져 있으면 비활성화한다.
- 선택만으로 GPU를 켜거나 결제를 시작하지 않는다. 운영자가 먼저 Phase 7 준비 명령을 실행한다.
- 챌린지를 만들 때 프로필·생성/판정 모델·runtime ID·GPU UUID·모델 revision·양자화를 서버에 고정한다.
- 이후 새 Pod를 등록해도 기존 참가자는 기존 runtime을 사용한다. 기존 GPU가 없어지면 명시적으로 실패하며
  Gemini/다른 GPU로 자동 전환하지 않는다. 새 모델을 쓰려면 새 챌린지를 만든다.
- 공급자별 API 키는 Render 환경변수에만 있다. URL은 검증된 Pod ID로부터 서버가 구성한다.
  브라우저는 임의 URL·키를 제출할 수 없다. DB에는 키를 저장하지 않는다.
- GPU 응답의 GPU UUID·model revision도 챌린지에 고정한 값과 비교한다.
- 생성/판정은 실행 전 준비 상태를 확인한다. 저장된 영수증 조회에는 GPU 연결이 필요 없다.

## 최초 적용: SQL → Render → 프런트

Phase 0–5 SQL이 아직 적용되지 않았다면 [기존 적용 안내](campaign-bike-rollout.md)를 먼저 따른다.
그 다음 아래 SQL **전체**를 Supabase SQL Editor에서 한 번 실행한다.

`supabase/migrations/202611040001_model_profiles.sql`

신규 테이블: `gpu_runtimes`(변경하지 않는 runtime 기록), `gpu_profile_bindings`(새 참가자가 사용할 runtime).
기존 참가자·AI 영수증·자전거·인쇄 기록은 삭제하지 않는다.

Render Dashboard에서 아래 키를 설정한다. 기존 Supabase/Gemini/Bike/Print 값은 유지한다.

```dotenv
IMAGE_PROVIDER=gemini
ROUTER_PROVIDER=gemini
GPU_CONTROL_TOKEN=<운영자 등록용 새 난수, 32자 이상>
GPU_4090_API_KEY=<4090 worker와 동일한 키, 32자 이상>
GPU_5090_API_KEY=<5090 worker와 동일한 키, 32자 이상>
```

사용하지 않는 GPU 프로필 키는 비워 둬도 된다. 그러면 해당 메뉴는 비활성화된다.
두 GPU를 동시에 빌릴 필요는 없다. 현재 모델 이름은 코드의 기본값과 같아 추가 설정이 필요 없다.
나중에 다른 이름을 검증할 때는 `GPU_4090_IMAGE_MODEL`, `GPU_4090_ROUTER_MODEL`,
`GPU_5090_IMAGE_MODEL`, `GPU_5090_ROUTER_MODEL`을 사용할 수 있다. 모델을 실제 설치하는 것은 별도 Phase 9다.

`GPU_CONTROL_TOKEN`은 기존 `BIKE_WORKER_TOKEN`, `PRINT_WORKER_TOKEN`과 별도 값이다.
`GPU_API_URL`/`GPU_API_KEY`는 과거 세션의 기존 경로에 남길 수 있다. 새 모델 메뉴는 GPU별 키와
DB runtime 주소를 사용하므로 Pod 변경 때마다 이 URL을 수정하지 않는다.
Render에는 RUNPOD_API_KEY나 SSH 개인키를 올리지 않는다. Vercel에도 이 비밀값을 넣지 않는다.

기존 서비스에서는 render.yaml의 `sync: false`만 추가해도 새 키가 자동 입력되지 않는다.
Dashboard에서 추가하고 새 코드로 배포해야 적용된다.
SQL 후 두 저장소 연동 브랜치/병합된 main을 배포한다. Vercel의 VITE_API_BASE는 기존 Render 주소를 유지한다.

## Phase 7: Mac 운영자 준비

이 자동화는 **이미 만들어 둔 지정 Pod를 시작/재사용**한다. GPU 부족 시 다른 Pod를 만들지 않으며,
기존 Pod 설정 편집·강제 재시작·종료·자동 과금 중지도 하지 않는다.
RunPod REST v2의 GET `/v2/pods/{id}` 및 POST `/v2/pods/{id}/action` (`action=start`)만 사용한다.

최초 전제:

1. Pod 생성 전부터 `22/tcp`, `8000/http`, 필요하면 `8888/http`를 노출한다.
2. RunPod에 SSH 공개키를 등록한다. 기본 개인키 경로는 `~/.ssh/runpod_esg`다.
3. 처음 접속하는 SSH 주소는 Connect 화면과 지문을 확인하고 Mac known_hosts에 등록한다.
   자동화는 StrictHostKeyChecking을 끄지 않으므로 새 IP/포트의 첫 신뢰 확인은 필요하다.
4. 현재 검증한 PyTorch 2.8.0/CUDA 12.8/Python 3.12 템플릿과 RTX 4090 또는 5090 1개를 사용한다.

프로젝트 루트에서 한 번 설정:

```bash
mkdir -p .runpod
chmod 700 .runpod
cp docs/runpod-operator.env.example .runpod/operator.env
chmod 600 .runpod/operator.env
```

`.runpod/operator.env`를 Mac에서 직접 편집한다. 실제 비밀값을 채팅이나 GitHub에 올리지 않는다.

```dotenv
RUNPOD_API_KEY=<RunPod Pods 읽기/시작 권한 API 키>
GPU_CONTROL_API_BASE=https://ai-slop-stop-api.onrender.com
GPU_CONTROL_TOKEN=<Render와 동일한 운영자 키>
GPU_4090_API_KEY=<Render의 4090 키와 동일>
GPU_4090_POD_ID=<4090 Pod ID>
GPU_5090_API_KEY=<Render의 5090 키와 동일>
GPU_5090_POD_ID=<5090 Pod ID>
```

4090만 쓰면 5090 항목은 비워 둬도 된다. 기존 worker.env/백업을 복원할 때는 해당 프로필의 키를
기존 worker 키와 맞춘다. 다른 프로필의 키/백업을 섞으면 덮어쓰지 않고 멈춘다.
한 번 설정한 키는 Pod가 바뀌어도 재사용한다. 새 Pod의 빈 설정은 해당 키로 만든다.

매번 시작할 때:

```bash
./runpod prepare --profile open-4090
# 5090을 사용할 때:
./runpod prepare --profile open-5090
```

새 Pod를 만들었으면 Pod ID만 갱신하거나 다음처럼 지정한다. URL·TCP 포트는 API에서 찾아온다.

```bash
./runpod prepare --profile open-4090 --pod-id NEW_POD_ID
```

백업을 처음 복원할 때만 다음 옵션을 추가할 수 있다. 이후 같은 프로필의 마지막 백업은 자동 선택된다.

```bash
./runpod prepare --profile open-4090 --backup /absolute/private/ai-slop-rescue.tar.gz
```

처리 순서: 지정 Pod/GPU/노출 포트 검사 → 정지 상태면 시작 요청 1회 → 새 SSH 주소 확인 →
SSH 신뢰 확인 → 커밋된 worker 코드 전송 → 설정/요청 원장 복원 → 설치 → 모델 로딩/Zeus 준비 확인 →
자동 백업 → HTTPS 인증 확인 → Render에서 GPU/모델/revision/양자화 재확인 → DB 주소 등록.

완료되면 프런트 메뉴에 해당 GPU가 사용 가능으로 나타난다. 준비 과정은 모델 다운로드가 필요하면
기존 캐시가 있을 때보다 오래 걸린다. `--timeout` 기본값은 1800초다.
실패/시간 초과 후 Pod가 자동으로 정지하지는 않는다. RunPod 상태와 요금을 확인한다.

프로필별 확인/로그/종료 전 백업:

```bash
./runpod status --profile open-4090
./runpod logs --profile open-4090 --setup
./runpod logs --profile open-4090
./runpod backup --profile open-4090
```

연결 정보·단계 상태·개인 백업은 `.runpod/open-4090/` 또는 `.runpod/open-5090/`에 분리된다.
작업을 마치고 백업 후 RunPod 콘솔에서 직접 정지한다. 네트워크 볼륨은 선택이며 새 컨테이너의
환경/캐시는 다시 설치될 수 있다. 이 스크립트가 임시 저장소를 영구 저장소로 바꾸지는 않는다.

### 기존 worker를 업데이트할 때

예전 worker의 `/health`에 GPU UUID/revision/양자화 정보가 없으면 새 등록 검증을 통과하지 못한다.
정상 worker나 진행 중 요청을 자동 재시작하지 않도록 의도적으로 막는다.
진행 중 챌린지가 없는 것을 확인하고 Pod를 정상 정지/시작한 뒤 새 코드의 prepare를 실행한다.
소스 전송은 `git archive HEAD`이므로, 새 worker 코드가 커밋된 버전을 사용해야 한다.

## Phase 8: 현장 검증 체크

- Gemini 기본 선택 → 토큰 영수증 → 자전거 대기 코드 → 고정 비교 목표 346.3 mWh → 종료 → PT-210 1장.
- 준비 안 된 오픈모델은 비활성화. prepare 후 사용 가능으로 바뀜.
- 4090/5090 선택 → 해당 GPU UUID와 두 모델의 원장 일치 → Wh 목표 자동 입력.
- ×100 환산 100% 자동 종료 및 미달 수동 종료 각각 1장.
- Gemini·GPU 참가자를 번갈아 처리해 모델/토큰/Wh/참가자 코드가 섞이지 않음.
- 새 Pod 등록 뒤 기존 참가자가 새 GPU로 이동하지 않음. 기존 GPU 중단은 명시적 오류.
- 인터넷 단절/복구·두 번 종료·재접속·프린터 UNKNOWN 처리.

화면 검증은 사용자 요청대로 생략한다. 실제 RunPod 시작, 모델 API 생성, 운영 SQL,
Render/Vercel 설정 변경, 자전거 및 PT-210 물리 출력은 이번 구현 중 수행하지 않는다.

공식 근거: [RunPod REST v2 스키마](https://api.runpod.io/v2/openapi.json),
[공식 Python SDK의 Pod 시작 구현](https://github.com/runpod/runpod-python/blob/main/runpod/api/ctl_commands.py),
[Render 환경변수](https://render.com/docs/configure-environment-variables).
