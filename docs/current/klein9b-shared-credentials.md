# Klein 9B NF4와 공통 키 — 2026-11-06

기존 챌린지의 4090/5090 생성 경로를 이벤트와 같은 Klein 9B NF4로 고정했다.
Qwen3.5-4B 판정은 유지하며, 목표별 체크리스트는 이번 전환에 포함하지 않는다.

- 생성 모델: `black-forest-labs/FLUX.2-klein-9B`
- revision: `92196c8e11f7b6cf2b7493e037d8c5345c559216`
- 생성 모델·텍스트 인코더 NF4, BF16 compute, double quantization.
- 1024×1024, 4 steps, guidance 1. 단일/동종 2 GPU 지원 유지.
- SSH 개인키: 기존 `~/.ssh/runpod_esg`를 챌린지·이벤트 모두 재사용.
- GPU 인증 키: 기존 이벤트의 `GPU_5090_API_KEY`를 챌린지 4090/5090에도 재사용.
- SSH 키와 GPU API 키는 다른 종류다. 같은 문자열로 바꾸거나 서로 복사하지 않는다.

## 키 우선순위

Render와 로컬 prepare는 `GPU_5090_API_KEY` → `GPU_API_KEY` → `GPU_4090_API_KEY` 순서로 공통 키를 선택한다.
따라서 기존 Render의 5090 키가 이벤트와 같으면 4090 키가 별도로 남아 있어도 두 챌린지 프로필은 5090 키를 사용한다.
Gemini 키·측정소 키·프린터 키·GPU_CONTROL_TOKEN은 변경하지 않는다.

이 Mac의 비공개 `.runpod/operator.env` 두 GPU 키는 이벤트 `.env.gpu.local` 값과 동기화했다.
개인키 파일 자체와 RunPod 계정의 공개키 등록은 변경하지 않았다.
복원한 worker.env 키가 다르면 포트 8000이 닫힌 상태에서만 다음 준비 시 동기화한다.
실행 중인 worker의 인증 키를 자동 교체하거나 프로세스를 재시작하지 않는다.

## 적용

1. 새 코드를 commit/push하고 Render가 새 버전을 배포한 것을 확인한다.
2. Pod는 운영자가 상태를 확인해 시작한다. 기존 worker가 있으면 진행 중 요청이 없는 것을 확인하고 정상 종료한다.
3. 같은 SSH 공개키로 접속을 확인하고 `./runpod prepare --profile open-5090` 또는 `open-4090`을 실행한다.
4. Klein 9B 이용 동의·HF 인증이 필요하다. 새 Pod의 HF 로그인은 이전 Pod에서 자동 승계되지 않는다.
5. worker /health에 9B revision과 `image_quantization=nf4`가 있어야 한다. Render도 이를 검증하고 새 런타임으로 등록한다.

과거 4B로 고정된 진행 세션은 런타임이 달라졌다고 거절할 수 있으므로 새로운 챌린지를 시작한다.
기존 영수증·사용량·생성 이미지·요청 원장은 삭제하거나 새 모델로 덮어쓰지 않는다.

이벤트용 worker는 이미지 전용, 챌린지 worker는 이미지＋Qwen 판정이다. 키·모델이 같아도
이벤트 전용 Pod를 챌린지 프로필에 등록할 수는 없다. 서로 다른 Pod로 실행하거나 역할에 맞는 worker를 배포한다.

## 검증 범위

CPU 계약 테스트로 모델 고정·공통 키·복원 설정 변경·잘못된 정밀도/revision 거절을 확인한다.
이 전환 작업에서 유료 Pod를 시작하거나 모델을 실제 로드하지 않는다. 기존 생성 단독 실측과
9B＋Qwen의 동시 상주/전체 응답 시간은 구분하며 운영 준비 후 확인한다.
