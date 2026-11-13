# 한국어 이미지 모델 내부 비교

사이트·Render·Supabase를 거치지 않고 준비된 Pod의 이미지 생성 API를 직접 호출한다.
현재 도구는 이미 실행 중인 worker의 모델을 시험한다. 모델 다운로드/교체/Pod 시작은 하지 않는다.
후보별 로더와 전용 실행 파일은 아래의 독립 후보 비교 경로에서 사용한다.
기본 API 테스트는 현재 실행 중인 worker 모델을 바꾸지 않는다.

## 실행 위치: Mac 터미널

RunPod 연결 및 prepare가 완료되어야 한다.

```bash
cd /Users/hunhokang/Downloads/esg_campaign/output/zeus-migration
./runpod benchmark --profile open-5090
```

4090에서는 프로필만 바꾼다.

```bash
./runpod benchmark --profile open-4090
```

API 키·주소·SSH 포트는 prepare가 저장한 프로필에서 읽는다. 별도 복사나 Render 변수 변경이 필요 없다.
기본 5개 한국어 프롬프트를 각 1회 생성한다. 현재 세션의 참가자 요청과 겹치지 않는 시간에 실행한다.
새 GPU 호출 및 대여 비용이 발생한다. 실패·응답 손실 시 생성 요청을 자동 재시도하지 않는다.
요청 ID를 먼저 저장하고 결과가 불확실하면 중단한다. 같은 명령을 다시 실행하면 별도 새 테스트가 된다.

## 결과

`benchmark-results/<UTC시각>-<프로필>-<실행ID>/` 아래에 저장한다.

- report.html: 생성 이미지, 한국어 원문, 확인 조건, 시간·전력·VRAM 표.
- results.json: worker 모델/revision/양자화/장비 정보, 요청 ID·시드·원본 계측값.
- summary.json: 성공 수, 평균 API 시간, 확인된 생성 mWh 평균.
- 요청 ID.png: 이미지 원본.

시간은 Mac→RunPod API 왕복이며 웹서비스 전체 시간이 아니다.
VRAM은 250ms NVML 표본 최대값이다. 순간 최대값·PyTorch allocator 최고값은 아니다.
전력은 이미지 생성 담당 GPU 구간만 포함한다. 판정·CLIP·다른 GPU의 대기 전력은 포함하지 않는다.
추론 시드는 현재 worker가 무작위 지정하므로 서로 다른 실행의 시드는 고정 비교가 아니다.
실패/미계측은 0으로 간주하지 않는다. 품질은 자동 채점하지 않는다.
보고서와 이미지로 한국어 조건 이행·구도·스타일·한글 표현을 검토해서 최종 평가한다.
어시스턴트에 결과 폴더 경로를 알려주면 사이트 접속 없이 이미지를 읽어 비교 보고할 수 있다.
기본 테스트의 한글 렌더링 항목은 부가 기능이며 모든 챌린지의 필수 조건을 의미하지 않는다.

프롬프트는 `benchmarks/korean-image-prompts.json`에서 수정한다.
최대 20개, 반복은 1~3회다. 예:

```bash
./runpod benchmark --profile open-5090 --repeat 2
```

이미 저장된 보고서를 다시 만들 때는 GPU 호출이 없다.

```bash
python3 scripts/benchmark_images.py --report /절대경로/results.json
```

현재 참가자 서비스 설정이나 DB 기록은 변경하지 않는다. 생성 결과는 Git에서 제외한다.


## 독립 후보 비교: 모델별 전용 스크립트

- `benchmarks/z_image_turbo.py`: Z-Image-Turbo BF16, 9 scheduler steps(공식 Turbo 설정).
- `benchmarks/qwen_image_lightning_nf4.py`: Qwen-Image-2512, transformer/text encoder NF4, Lightning 4-step BF16 LoRA, true CFG 1.
- `benchmarks/klein_9b_nf4.py`: Klein 9B distilled, transformer/text encoder NF4, 4 steps, guidance 1.

프롬프트는 세 후보에서 동일한 한국어 원문을 쓴다. 번역·확장·모델별 내용 추가를 하지 않는다.
모든 모델을 1024×1024, 시드 42+프롬프트 인덱스+반복 인덱스×100으로 실행한다.
모델별 다른 샘플링 설정을 기록하므로 동일 시드가 같은 이미지를 의미하지는 않는다.
단계 수·가중치 revision·양자화 구성·Lightning revision/file·offload 여부를 보고서에 기록한다.
Z-Image는 기본 BF16 비교이며 메모리를 줄여야 할 때 `--quantization nf4`를 명시할 수 있다.
NF4를 FP8 연산 가속으로 표시하지 않는다. NF4에서는 VAE를 양자화하지 않는다.

최신 prepare로 공통 의존성(PEFT, FLA, causal-conv1d 포함)이 설치된 Pod를 사용한다.
실행 시 `/workspace/ai-slop-benchmarks/<실행ID>`에 커밋된 테스트 소스만 전송한다.
운영 worker 코드·설정·원장과 Render 등록은 바꾸지 않는다.
후보마다 별도 Python 프로세스를 순차 실행해서 모델 사이에 CUDA 메모리를 해제한다.
다운로드·모델 로딩·각 모델 1회 워밍업은 생성 시간/전력 통계에서 제외한다.
첫 GPU 실측 전까지 설정/로더 구현 상태이며, 품질·속도·GPU별 호환성을 검증한 것은 아니다.

### 사전 조건: 테스트할 GPU를 비워 둘 것

이미 운영 worker가 해당 GPU를 점유 중이면 `GPU_BUSY_STOP_SERVICE_BEFORE_BENCHMARK`로 실패한다.
prepare 완료 직후는 worker가 GPU를 점유하므로 그대로 후보 비교를 시작하지 않는다.
동일 Pod를 사용할 때는 진행 중 참가자 요청이 없는 것을 확인한 다음 Pod 터미널에서 알려진 worker만 정상 종료한다.

```bash
python - <<'PY_STOP'
import os, signal
from pathlib import Path
pid = int(Path('/workspace/ai-slop-stop/worker.pid').read_text())
command = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
if b'uvicorn' not in command or b'gpu_worker.app:app' not in command:
    raise RuntimeError('PID does not belong to the expected worker; nothing stopped')
os.kill(pid, signal.SIGTERM)
print('Worker graceful shutdown requested. Wait until nvidia-smi shows the GPU is free.')
PY_STOP
```

후보 비교가 끝난 뒤 서비스 재개는 Mac에서 기존 `./runpod prepare --profile open-5090`을 실행한다.
4090이면 open-4090으로 바꾼다. prepare는 운영 모델 설정을 유지하며 후보를 서비스에 등록하지 않는다.
이미 healthy인 worker에는 prepare가 새 의존성을 설치하지 않는다. 새 PEFT/커널 설치가 필요하면
먼저 worker를 종료한 뒤 prepare로 설치/시작하고, 비교 전에 다시 종료한다.
운영 프로세스를 자동 종료하지 않는다. 진행 중 챌린지가 없는 시간에 worker를 정상 종료하거나 별도 유휴 Pod에서 실행한다.
메모리가 부족하다고 서비스 모델을 자동 언로드하거나 CPU offload로 전환하지 않는다.
CPU offload가 필요하면 `--offload`를 명시하고 성능 비교에 그 차이를 반영한다.
NF4 로딩도 원본 가중치를 다운로드하므로 VRAM 절감이 디스크 사용량 절감을 뜻하지 않는다.
Qwen-Image 원본 가중치만 수십 GB이므로 세 후보를 저장할 디스크 여유가 필요하다.
각 모델의 로딩 전 디스크 여유를 results.json에 기록한다.
Klein 9B는 Hugging Face 모델 이용 조건 동의 및 해당 계정의 접근 권한이 필요할 수 있다.
토큰이 필요하면 Pod의 표준 Hugging Face 인증을 사용한다. API 키/SSH 키를 프롬프트나 보고서에 넣지 않는다.

### Mac에서 한 후보 실행

```bash
cd /Users/hunhokang/Downloads/esg_campaign/output/zeus-migration
./runpod benchmark --profile open-5090 --candidate z-image-turbo
./runpod benchmark --profile open-5090 --candidate qwen-image-lightning-nf4
./runpod benchmark --profile open-5090 --candidate klein-9b-nf4
```

세 후보를 한 번에 순서대로 실행하려면:

```bash
./runpod benchmark --profile open-5090 --candidate all
```

4090도 `--profile open-4090`만 바꾼다. GPU 두 장 중 비어 있는 두 번째 장을 사용할 때는 `--device 1`을 지정한다.
초기 모델 설치는 다운로드를 포함해 오래 걸릴 수 있다. 각 후보 실행은 최대 1시간이며 시간 초과 시 해당 프로세스가 종료된다.
인터넷/SSH가 끊겨도 이미 시작한 원격 실행은 계속될 수 있다. 출력된 Run ID로 재수집한다.
원격 GPU 부하가 없다고 판단해서 자동으로 새 생성을 반복하지 않는다.

```bash
./runpod benchmark --profile open-5090 --collect 출력된_RUN_ID
# 같은 프로필에서 가장 최근에 시작한 실행:
./runpod benchmark --profile open-5090 --collect latest
```

결과는 `benchmark-results/candidates-<실행ID>/comparison.html`에서 나란히 확인한다.
모델별 report.html/results.json/summary.json/PNG 및 실패 로그도 함께 수집한다.
일부 모델 실패는 전체 성공으로 표시하지 않는다. 수집 명령은 추론을 재실행하지 않는다.
성공한 모델을 다음에 다시 실행하면 새로운 테스트이므로, 중복 생성을 피하려면 collect를 사용한다.
생성 GPU에 대해 50ms NVML 표본 최대값과 PyTorch peak allocated/reserved를 기록한다.
전력은 담당 GPU의 생성 구간만 포함한다. 유휴 GPU·CPU·다운로드 전력은 포함하지 않는다.

### 판정 경로의 별도 최적화

위 후보들은 이미지 생성 모델이다. FLA/causal-conv1d는 현재 Qwen3.5 판정 모델의 fast path에 적용된다.
이미 공통 설치 경로에서 두 커널을 설치/검사한다. 후보 이미지 생성에 두 커널이 자동 적용된다고 주장하지 않는다.
Qwen 판정 processor는 `GPU_ROUTER_MAX_PIXELS`(기본 262144 = 512×512)로 이미지당 입력 면적을 제한한다.
전처리 후 실제 image_grid_thw를 확인해 각 이미지의 시각 토큰 상한을 초과하면 GPU 전송 전에 중단한다.
허용 설정 범위는 65536~1048576 pixels이며 기본값 사용 시 추가 환경변수는 필요 없다.
후보 품질 비교에서는 Qwen/CLIP 판정을 호출하지 않는다. 이미지 생성 자체의 품질과 비용을 분리해서 보고한다.
기존 운영 사이트의 이미지 모델은 이 테스트로 자동 교체되지 않는다. 후보 선정 후 별도로 전환한다.


### 50GB 컨테이너에서 비교

후보마다 실행 전용 Hugging Face 캐시를 만들고, 프로세스 종료 후 그 캐시만 삭제한다.
공유 캐시나 인증 파일은 삭제하지 않는다. 다음 비교에서는 다시 다운로드한다.
디스크 여유가 70GiB 미만이면 Qwen-Image transformer를 먼저 NF4로 로딩한 후 전용 다운로드 캐시를
삭제하고 text encoder를 읽는다. VRAM 양자화·생성 조건은 유지하고 로딩 방식의 차이를 기록한다.
공개 모델 두 개만 비교하려면 `--candidate public`을 사용한다. Klein 9B 인증 요청은 발생하지 않는다.


Qwen-Image Lightning는 LoRA/4스텝만 적용해서는 충분하지 않다.
배포자 예제의 FlowMatchEulerDiscreteScheduler(shift=3에 해당하는 base/max_shift=log(3), shift_terminal=None)를 함께 적용한다.
스케줄러 설정은 health 메타데이터에 기록한다. 초기 스케줄러 누락 실행은 최종 후보 비교에서 제외한다.

RunPod Web Terminal에서 로그인한 HF 인증은 /workspace/.cache/huggingface/token 또는 /workspace/huggingface/token 경로도 확인한다. 인증값은 자식 프로세스 환경에만 전달하고 보고서·명령 인자·로그에 출력하지 않는다.


### 기존 Klein 4B 재비교

수정된 공통 한국어 5개를 기존 서비스와 같은 BF16, 4 steps, guidance 1, 고정 model revision으로 시험한다.
해상도·시드·워밍업 규칙은 다른 후보와 동일하다. 다른 후보는 다시 생성하지 않는다.

```bash
./runpod benchmark --profile open-5090 --candidate klein-4b-bf16
```

`--candidate all`은 기존 의미대로 교체 후보 3개만 실행하며 4B 기준 모델은 위 옵션으로 명시한다.
