# 로컬 CLIP 리뷰 수정 및 Mac 실측 보고 — 2026-11-02 (Asia/Seoul)

판정: **리뷰 수정 적용 완료, 이번 로컬 검증 성공. 운영 Render 연결과 gate 정확도는 미검증.**

zaz대상은 `codex/local-clip-worker`, 기준 커밋 `47f4f18fc66959fbe9e0208d95cb2c98760dd4b6`다.
시작 시 tracked/untracked 로컬 변경이 없었다. 제공된 `REVIEW.md`, `review-fixes.patch`를 읽고
`git apply --check`로 충돌 없음을 확인했다. 원본 리뷰·패치 파일은 변경하지 않았다.
검증 당시 변경은 로컬 미커밋 상태였다. 검증 중에는 push, Render 배포/설정 변경, 유료 Gemini 호출을 하지 않았다.

## 적용 변경과 회귀 재현

| 항목 | 수정 전 | 수정 후 |
|---|---|---|
| 원본 서버 전체 테스트 | 46개 성공 | 최종 전체 50개 성공 |
| 리뷰 제공 회귀 테스트 3개 | 2개 실패, 1개 성공 | 3개 모두 성공 |
| CLIP claim 120회 후 UI 조회 | UI 요청이 429 | UI·인쇄와 CLIP 버킷 분리 |
| 직접 비교 CLI 전처리 | 원본을 사용해 서비스와 다름 | 서버와 같은 `evaluation_image()` 적용 |
| 추가 회귀 1개 | 추가 검증 | claim 한도 초과 429 이후에도 complete는 별도 버킷으로 처리 |

패치의 두 수정과 제공 회귀 테스트를 적용했다. 추가로 claim/result 버킷 분리를 직접 확인하는
회귀 테스트 1개와 재실행 가능한 `clip_worker/validate_local.py`를 작성했다.
기존 gate→비동기 평가→JSON 저장→STAFF 승인 테스트에서는 Gemini 미호출, `router_model=null`,
`router_called=false`, `api_calls` 미추가, 영수증 `router_calls=0`을 확인했다. DB는 MemoryStore다.

## 환경과 이미지

- Apple M1 Mac mini, 물리 메모리 16 GiB, MPS, Python 3.11.18.
- torch 2.15.0, torchvision 0.29.2, clip 1.0, Pillow 12.3.0, NumPy 2.4.6.
- 모델: `openai/ViT-B/32`, 공식 CLIP 소스 커밋 `d05afc436d78f1c48dc0dbf8e5980a9d471f35f6`.
- 가중치 SHA256: `40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af`.
- 전처리 버전: `clip-rgb-exif-white-1024-v1`.
- 서버 전송: 긴 변 최대 1024px, 조건에 따라 JPEG 변환. 워커: EXIF 보정, RGBA 흰 배경 합성,
  RGB, bicubic Resize 224, CenterCrop 224, ToTensor, CLIP mean/std Normalize, L2 정규화한 특징 내적.
- 사용자 별도 이미지 지정 없음. 공개 실사 사진 두 장을 사용했다. 서비스의 목표/생성 이미지 쌍은 아니다.
- [PyTorch dog.jpg](https://github.com/pytorch/hub/blob/master/images/dog.jpg): 1546×1213 → 1024×803.
- [Ultralytics bus.jpg](https://github.com/ultralytics/assets/blob/main/im/bus.jpg): 810×1080 → 768×1024.
- 이미지 원본과 전송 바이트 크기, SHA256은 [images.json](validation/2026-11-02/images.json),
  [benchmark.json](validation/2026-11-02/benchmark.json)에 있다. 사진 자체는 `.cache`에만 두었다.

## 실제 모델 측정

별도 새 Python 프로세스에서 모델을 로드했다. 파일/OS/GPU 커널 캐시를 강제로 비우지 않았다.
따라서 아래 첫 추론은 **해당 프로세스의 첫 비교**이며 재부팅 직후 cold start가 아니다.
서버 전송 전처리는 측정 전에 완료했다. 비교 시간에는 워커의 이미지 디코딩·CLIP 전처리·추론·
벡터 비교가 포함되고, MPS synchronize로 GPU 완료를 기다렸다. 캐시 비교에는 SHA256 조회·내적만 남는다.

| 측정 | 결과 |
|---|---:|
| 모델 준비(의존성 import와 가중치 로드 포함) | 3,265.93 ms |
| 첫 두 이미지 비교 | 198.214 ms |
| 임베딩 캐시 비운 반복 20회 중앙값 / 표본 p95 | 59.293 / 64.223 ms |
| 양쪽 임베딩 캐시 적중 20회 중앙값 / 표본 p95 | 0.197 / 0.233 ms |
| 모델 로드 후 RSS | 982.27 MiB |
| 첫 비교·반복·캐시 이후 RSS | 1,102.83 MiB |
| 프로세스 전체 생애 최대 RSS (`ru_maxrss`, macOS bytes 환산) | 1,715.69 MiB |
| MPS 현재 allocated / driver | 583.58 / 1,538.38 MiB |
| 전송 경로 기준 두 사진 코사인 | 0.3080339432 |

RSS는 `ps`의 현재 resident set이고 최대 RSS는 모델 준비를 포함한다. MPS 수치는 별도 회계이며
RSS와 더해서 총 메모리로 간주하지 않는다. p95는 20회 표본의 순서 통계량으로 서비스 SLA가 아니다.

원본 직접 인코딩의 코사인은 0.2969866097, 서비스 전처리 후에는 0.3080339432로
차이는 약 0.01105였다. 이번 패치가 고치는 경로 차이를 실제 모델에서도 확인했다.
이는 두 사진에 대한 차이이며 임계값 보정이나 정확도 평가 결과가 아니다.
패치 적용 후 실제 `--compare` CLI에서도 같은 코사인 0.3080339432를 확인했다.
이 CLI의 측정은 전송 전처리를 포함해 222.966 ms였으며 모델 준비는 제외한다.

패치 후 실제 CLIP smoke_test도 성공했다. MPS, 동일 이미지 1.0, 다른 합성 이미지 0.8772904277,
API 계약 왕복 75.8 ms(모델 준비·일부 임베딩 캐시 이후), 프로세스 최대 RSS 1,717.5 MiB,
유료 API 호출 0회였다. 이 스모크 테스트는 TestClient이며 아래 TCP 실험과 구분한다.

## macOS 메모리 압력·스왑

`memory_pressure -Q`, `sysctl kern.memorystatus_vm_pressure_level`, `sysctl vm.swapusage`, `vm_stat`을
모델 준비 전/후, 첫 비교 후, 실험 끝과 실제 워커 실행 중/종료 후에 읽었다. 연속 감시는 아니다.

| 벤치마크 시점 | system-wide memory free percentage | pressure level 원시값 | swap used |
|---|---:|---:|---:|
| 모델 준비 전 | 58% | 1 | 1.38M |
| 모델 로드 후 | 49% | 1 | 1.38M |
| 첫 비교 후 | 47% | 1 | 1.38M |
| 반복·캐시·추가 비교 완료 후 | 54% | 1 | 1.38M |

TCP 실험의 측정 시점에는 free percentage가 58% → 57% → 57%, pressure level은 모두 1,
swap used는 모두 1.38M였다. 순간의 최고 압력이나 장시간 스왑 증가가 없다고 보장하는 수치는 아니다.
다른 앱이 동작 중인 시스템 전체의 수치이므로 모든 변화를 CLIP에 귀속하지 않는다.
원시 명령 결과는 두 JSON에 보존했다.

## 로컬 TCP 서버 + 실제 caffeinate 워커 장애 검증

loopback 동적 포트의 Uvicorn 서버를 사용했다. 실제 `/api/challenges/{cid}/evaluations` 시작/조회 API,
메모리 DB fixture, 실제 워커 프로세스를 연결했다. Gemini 함수는 스텁으로 대체하고 SDK client
초기화도 로컬 예외로 차단했다. 서버 설정은 테스트 프로세스 안에서만 바꿨으며 env 파일은 수정하지 않았다.
워커는 `/usr/bin/caffeinate -i ... -m clip_worker.worker --env /dev/null`로 실행하고
임의 토큰을 환경으로 전달했다. 종료는 테스트가 만든 프로세스 그룹에만 적용했다.

| 시나리오 | 결과와 관측 |
|---|---|
| 순차 실제 사진 요청 3건 | 성공. 972.13~1,326.93 ms, 중앙값 1,045.34 ms |
| 동시에 독립 챌린지 2건 | 성공. 각 시작부터 약 1,045.53 / 2,037.22 ms |
| 실제 워커가 claim한 직후 강제 종료 | 성공. required, 테스트 제한 2초에서 약 2,125.54 ms 후 CLIP_TIMEOUT, Gemini 스텁 미호출, 큐 정리 |
| 워커 재실행 | 성공. 새 요청 약 1,230.62 ms. 중단된 이전 작업을 복구한 것은 아님 |
| 워커가 꺼진 상태에서 fallback | 성공. 테스트 제한 0.5초 후 Gemini 스텁 진행, 약 586.14 ms, unavailable/CLIP_TIMEOUT 기록 |
| lease 후 시간 초과한 결과 수동 제출 | 성공. 409로 거부, required는 스텁 미호출 |
| 동일 결과 재제출 / 다른 값 충돌 / 정리 후 재제출 | 성공. HTTP 200 / 200 / 409 / 409 |
| 유료 호출·외부 DB 접근 | 없음. 스텁 호출 8회, fixture api_calls 0개, 남은 큐 0개 |

지연·중복 결과는 HTTP로 제어 제출했으며 모델을 재실행한 테스트는 아니다.
중복의 pending 상태를 결정적으로 검사하기 위해 테스트 안에서 이벤트 완료 통지만 잠시 보류했다.
2초·0.5초는 장애 실험의 메모리 설정이며 운영 기본값 20초를 변경하지 않았다.

실제 Python 워커의 준비 후 RSS는 1,037.77~1,096.20 MiB, 요청 처리 후에는
1,159.53~1,183.20 MiB였다. `caffeinate`의 작은 보조 프로세스가 아니라 Python PID를 확인해 측정했다.
API 왕복에는 1초 워커 폴링과 순차 처리 대기가 포함된다. 따라서 캐시 추론이 0.2ms여도
사용자 관측 응답이 그만큼 빨라지지는 않는다. Gemini 실제 지연과 인터넷 지연은 포함되지 않는다.
원시 결과는 [integration.json](validation/2026-11-02/integration.json)에 있다.

## 실행 명령과 성공/실패

모든 명령의 작업 디렉터리는 이 저장소 루트다. 비밀값은 명령행·보고서에 넣지 않았다.

```sh
git status --short
git apply --check ../clip-review-47f4f18/review-fixes.patch
.venv/bin/python -m unittest discover -s server/tests -v
git apply --include=server/tests/test_clip_review.py ../clip-review-47f4f18/review-fixes.patch
.venv/bin/python -m unittest server.tests.test_clip_review -v
git apply --include=clip_worker/worker.py --include=server/main.py ../clip-review-47f4f18/review-fixes.patch
.venv/bin/python -m unittest discover -s server/tests -q
/usr/bin/caffeinate -i .venv/bin/python -m clip_worker.smoke_test
/usr/bin/caffeinate -i .venv/bin/python -m clip_worker.validate_local benchmark .cache/local-validation/images/dog.jpg .cache/local-validation/images/bus.jpg
/usr/bin/caffeinate -i .venv/bin/python -m clip_worker.validate_local integration .cache/local-validation/images/dog.jpg .cache/local-validation/images/bus.jpg
/usr/bin/caffeinate -i .venv/bin/python -m clip_worker.worker --compare .cache/local-validation/images/dog.jpg .cache/local-validation/images/bus.jpg
.venv/bin/python -m unittest discover -s server/tests -v
.venv/bin/python -m compileall -q server clip_worker
git diff --check
```

제공된 리뷰·패치의 SHA256은 [review-inputs.json](validation/2026-11-02/review-inputs.json),
최종 테스트 출력은 [server-tests.txt](validation/2026-11-02/server-tests.txt)에 보존했다.

원본 전체 46개 성공 → 제공 회귀 3개 중 2개 실패 재현 → 패치 후 전체 49개 성공 →
claim/result 격리 회귀 추가 후 최종 전체 **50개 성공**. 실제 smoke와 TCP 시나리오 8개도 성공했다.
초기 TCP 계측에서는 caffeinate 보조 프로세스를 선택해 잘못된 RSS를 얻어 계측기를 수정했다.
수정 후 Python PID를 확인하며 재실행한 결과만 본 보고서와 integration.json에 사용했다.
이는 서비스 코드 실패가 아니라 계측 오류였으며 숨기지 않고 기록한다.

프런트도 캐시에 준비된 Node를 PATH에 넣고 다음 명령을 실행해 모두 성공했다.

```sh
export PATH="$PWD/.cache/node-v22.15.0-darwin-arm64/bin:$PATH"
npm --prefix web run build
npm --prefix web run test:evaluation
npm --prefix web run test:thermal
npm --prefix web run test:receipt-print
```

## Render 연결에 필요한 설정과 향후 확인 절차

아래는 실행하지 않은 운영 연결 절차다. 배포/설정 변경은 별도 지시가 필요하다.

| 위치 | 설정 이름 | 용도 |
|---|---|---|
| Render | CLIP_MODE | 최초 연결은 observe. off는 기존 흐름, gate는 별도 보정 후 |
| Render·Mac | CLIP_WORKER_TOKEN | 동일한 충분히 긴 전용 비밀값, 브라우저/VITE 변수 사용 금지 |
| Render | CLIP_TIMEOUT_SECONDS | 기본 20, 폴링·대기·계산을 포함 |
| Render | CLIP_FAILURE_POLICY | 최초 fallback, 연결 의존성 리허설은 required |
| Render | CLIP_REJECT_BELOW | gate에만 필요. 이번 검증에서는 운영 기준값 미설정 |
| Mac | CLIP_API_BASE | 실제 Render API의 HTTPS 주소 |
| Mac | CLIP_CACHE_DIR | 로컬 가중치 캐시 경로 |
| Render | RATE_LIMIT_PER_MIN | 기존 제한. 수정된 claim/result/UI·인쇄 버킷을 확인 |

기존 Render의 GEMINI_API_KEY, GEMINI_IMAGE_MODEL, GEMINI_ROUTER_MODEL, SUPABASE_URL,
SUPABASE_SECRET_KEY는 기존 기능용으로 서버에 유지한다. Mac 워커로 복사하지 않는다.

1. 배포할 코드에 두 리뷰 수정이 포함됐는지, Render가 단일 인스턴스·`--workers 1`인지 확인한다.
2. Render에는 server/requirements.txt만 설치하며 torch/CLIP 의존성이 들어가지 않았는지 확인한다.
3. 승인된 설정 후 `/health`의 clip_mode를 확인한다. 이 값만으로 워커 연결 성공을 판단하지 않는다.
4. Mac 워커를 caffeinate로 시작하고 ready와 작업 완료 로그를 확인한다.
5. 유료 호출을 허용받기 전에는 운영 Try를 누르지 않는다. 허용 후 실제 챌린지를 평가해
   similarity.cosine/model/pipeline/hash/시간이 반환·저장되고 `unavailable`이 아닌지 확인한다.
6. 공유 NAT에서 브라우저·프린터·CLIP 폴링이 서로의 한도를 소모하지 않는지 확인한다.
7. 연결 성공 후 짧은 required 리허설에서 워커 종료·복구를 검증한다. fallback에서 서비스가
   성공한 것만으로 CLIP 연결 성공이라고 판정하지 않는다.
8. 실제 목표/생성 쌍을 라벨링해 오탈락률과 호출 절감률을 검증한 후에만 gate 기준값을 결정한다.

## 미검증 항목

- Render HTTPS·인증·프록시·cold start·무료 서비스 제한, 실제 서비스/공유 NAT 부하.
- 실제 Gemini·Supabase·영수증 출력의 운영 왕복. 이번 DB는 MemoryStore, Gemini는 스텁이다.
- 서비스 목표/생성 이미지의 사람 라벨, 임계값별 오탈락률·호출 절감률·정확도.
- 물리적 인터넷 단절·강제 절전·덮개·Mac 재부팅. 이번에는 워커 프로세스만 종료·재실행했다.
- 실제 서버 프로세스 재시작·작업 복구, 3건 이상 지속 부하·장시간 soak·LRU 128개 포화·추론 hang.
- 연속 메모리 압력/스왑 감시와 최대 해상도·파일크기의 decode 피크. 스냅샷·두 사진 결과로 일반화하지 않는다.
- 자동 워커 감독/재시작은 구현하지 않았다. 기존 메모리 큐의 다중 인스턴스 지원도 없다.

이 보고서는 로컬 검증 결과이며 운영 적용을 의미하지 않는다. 운영 env와 비밀값 파일을 수정하거나 출력하지 않았다.
