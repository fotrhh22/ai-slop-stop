# 로컬 CLIP → Render → Gemini

브라우저 → Render 평가 작업 → 로컬 CLIP → Gemini 순서로 실행한다.
Mac이 Render에 HTTPS로 1초마다 작업을 요청하므로 공인 IP, 포트 개방, 터널이 필요 없다.
Mac에는 전용 워커 토큰만 두며 Gemini/Supabase 키가 필요 없다.
Render는 모델을 로드하지 않는다. `server/requirements.txt`에 torch/CLIP을 추가하지 않는다.

## 구현

- `server/clip_bridge.py`: 최대 두 작업의 메모리 큐, 토큰 인증, 작업별 lease, 시간 제한, 게이트.
- `clip_worker/worker.py`: 모델을 시작할 때 한 번 로드하고 한 작업씩 계산. MPS 자동 선택, CPU 수동 선택 가능.
- OpenAI 공식 `ViT-B/32`의 정규화한 512차원 이미지 벡터 사이 내적(코사인, -1~1)을 계산한다.
- 서버에서 검증한 이미지 두 장을 워커로 전달한다. 반환값은 점수와 실행 메타데이터다.
- 원본 SHA256, 모델명, 전처리 버전, 추론/왕복 시간을 `attempts.evaluation_result.similarity` JSON에 저장한다. DB migration 불필요.
- 워커는 이미지 파일을 저장하지 않는다. 임베딩 캐시는 최대 128개 CPU 벡터다.
- 프런트에는 점수를 아직 표시하지 않는다. 서버 평가 결과 JSON에서 확인한다.

## 로컬 실행

프로젝트 루트에서 실행한다. 이 컴퓨터에는 `.venv`와 `.cache/clip/ViT-B-32.pt`를 준비했다.
로컬 서버/워커의 `.env.local`도 동일한 임의 토큰과 localhost 주소로 생성했다(권한 600, Git 제외).
아래 토큰 생성/파일 작성은 새 환경에서 필요한 절차다. 이 컴퓨터에서 운영으로 전환할 때는
기존 토큰을 Render에 복사하고 워커의 API 주소를 바꾼다. Gemini/Supabase 운영 키는 복사하지 않았다.
다른 컴퓨터에서는 Python 3.11로 다음 설치를 진행한다.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r server/requirements.txt -r clip_worker/requirements.txt
```

공유 토큰을 생성한다. 아래 두 env 파일에 동일하게 넣는다.

```sh
.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

서버용 루트 `.env.local`(기존 Gemini/Supabase 설정에 추가):

```dotenv
CLIP_MODE=observe
CLIP_WORKER_TOKEN=생성한_토큰
CLIP_TIMEOUT_SECONDS=20
CLIP_FAILURE_POLICY=fallback
```

`clip_worker/.env.local`:

```dotenv
CLIP_API_BASE=http://127.0.0.1:8000
CLIP_WORKER_TOKEN=생성한_토큰
CLIP_CACHE_DIR=.cache/clip
```

두 터미널에서 각각 실행한다. 모델 준비 메시지 이후 Try를 누른다.

```sh
.venv/bin/python -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --workers 1
```

```sh
.venv/bin/python -m clip_worker.worker
```

이 컴퓨터에서는 `clip_worker/start.command`를 더블클릭해 워커를 실행할 수도 있다.
서버가 먼저 실행되어 있어야 하며 종료는 터미널에서 Ctrl+C다.

MPS 오류가 발생하면 `--device cpu`로 다시 시작한다.
이미지 파일끼리 직접 비교할 수도 있다(서버/토큰 불필요).

```sh
.venv/bin/python -m clip_worker.worker --compare /절대경로/목표.png /절대경로/생성.png
```

## Render 적용 순서

1. 이 변경을 검토하고 기존 저장소/Render 서비스에 배포한다. 로컬 변경만으로 운영 서버가 바뀌지는 않는다.
2. Render에 동일한 `CLIP_WORKER_TOKEN`, `CLIP_MODE=observe`, `CLIP_TIMEOUT_SECONDS=20`, `CLIP_FAILURE_POLICY=fallback` 환경변수를 추가한다.
3. Mac의 `clip_worker/.env.local`에서 `CLIP_API_BASE`를 실제 Render API HTTPS 주소로 바꾼다.
4. Mac에서 워커를 실행한다. 운영 시간에는 `caffeinate -i .venv/bin/python -m clip_worker.worker`로 유휴 절전을 막을 수 있다. 덮개를 닫거나 인터넷을 끊으면 워커가 응답하지 못한다.
5. `/health`의 `clip_mode` 확인 후 실제 Try로 `similarity.cosine`과 워커 작업 완료 로그를 확인한다. `similarity.status=unavailable`이면 연결 실패이며 추론 성공이 아니다.
6. 로컬 계산을 필수로 요구하려면 `CLIP_FAILURE_POLICY=required`로 바꾼다.

토큰은 `VITE_*`나 GitHub에 넣지 않는다. `.env.local`은 gitignore 대상이다.
워커의 폴링은 잠든 Render 서비스를 깨울 수 있다. 무료 서비스의 가용성/사용량 정책은 별도 확인한다.

## 모드와 기준값

| 설정 | 동작 |
|---|---|
| `CLIP_MODE=off` | 기존 Gemini 직접 비교 |
| `CLIP_MODE=observe` | CLIP 계산 후 점수 기록, Gemini가 최종 판정 |
| `CLIP_MODE=gate` + `CLIP_REJECT_BELOW` | 기준 미만은 CLIP FAIL, 나머지만 Gemini 호출 |
| `CLIP_FAILURE_POLICY=fallback` | 워커 실패/시간 초과 시 Gemini로 진행, 실패 원인 기록 |
| `CLIP_FAILURE_POLICY=required` | 워커 실패 시 오류 종료. FAIL이나 유료 호출로 바꾸지 않음 |

게이트는 자동 PASS를 하지 않는다. 경계값 이상은 Gemini로 간다.
CLIP 탈락에는 Gemini 항목별 평가를 지어내지 않는다. `router_called=false`,
`router_model=null`, `evaluation_source=clip_gate`이며 유료 호출 원장도 늘리지 않는다.
관측 모드에서는 점수를 Gemini 프롬프트에 주입하지 않는다. 게이트를 켰을 때만
LLM 호출 여부에 영향을 주므로 기존 판정과 비교할 자료를 모을 수 있다.

**0.8은 정답률 80%가 아니다.** 의미가 비슷하지만 구도·개수가 틀려도 점수가 높을 수 있다.
목표별 실제 PASS/FAIL 샘플을 사람이 라벨링하고 별도 검증 데이터에서 정답 오탈락률과
호출 절감률을 확인한 후 기준값을 정한다. 현재 임계값은 미설정이다.
모델/전처리 버전을 유지하고 변경 시 기준값을 다시 검증한다.

## 운영 한계

- 기존 Render `--workers 1` 전용이다. 여러 프로세스/인스턴스는 메모리 큐를 공유하지 않는다.
  확장/재시작 복구가 필요하면 Supabase 작업 테이블과 원자적 claim/lease RPC로 옮긴다.
- 서버 재시작 시 진행 중 작업은 사라진다. 기존 평가 작업처럼 UI가 재시도를 안내한다.
- 워커 중단 시 lease를 재할당하지 않고 제한 시간 후 종료한다. 다음 Try로 재계산할 수 있다.
- 같은 결과의 중복 제출은 대기 중일 때만 허용한다. 만료/완료된 작업의 늦은 결과는 409로 버린다.
- 기본 20초는 폴링/대기/추론을 모두 포함한다. 한 Mac이 순차 처리하므로 실제 이미지로 부하를 측정한다.
- 가중치 약 338 MiB 외에 PyTorch/실행 버퍼 메모리가 필요하다. 과거 Render 장애의 OOM 여부는 로그로 확인해야 한다.
- MPS 실행 중 오류는 자동 CPU 폴백하지 않는다. 오류를 보고하며 `--device cpu`로 재시작할 수 있다.

## 검증

이 Mac에서 확인한 값: MPS 추론 성공, 같은 이미지 코사인 1.0,
실제 모델 + API 계약 스모크 테스트 왕복 약 81 ms(모델 준비/일부 캐시 이후),
해당 테스트 프로세스의 최대 RSS 약 1,717 MiB. 별도 첫 비교 추론은 약 771 ms였다.
이는 합성 이미지 단일 테스트 수치이며 운영 지연/정확도/최대 메모리를 보장하지 않는다.
서버 테스트는 기존 회귀 테스트를 포함해 46개 통과했다.

```sh
.venv/bin/python -m unittest discover -s server/tests -v
.venv/bin/python -m clip_worker.smoke_test
```

스모크 테스트는 실제 CLIP 모델, 서버 API 계약, 모델 실행 전 LLM 미호출과 결과 전달 후 LLM 호출을 확인한다.
Gemini만 스텁이므로 비용이 발생하지 않는다. 합성 이미지 테스트이며 실제 서비스 정확도,
인터넷 왕복, 운영 Render 연결을 검증한 것은 아니다.

공식 근거: [OpenAI CLIP](https://github.com/openai/CLIP),
[CLIP 소개와 한계](https://openai.com/index/clip/),
[PyTorch MPS](https://docs.pytorch.org/docs/stable/notes/mps.html).
