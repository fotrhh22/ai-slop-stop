# 단기 RunPod 운영 기록: SSH, 주소, 저장 공간

결정: 2026-11-03. 현재 사용자는 Pod를 정지했다. 비용이 드는 Network Volume 이전은 보류한다.
필요할 때 임시 Pod/컨테이너를 재구축하고 필요한 설정·측정 결과·원장은 로컬에 백업한다.
이 기록을 갱신하거나 코드를 push하기 위해 Pod를 다시 켜지 않는다.
Supabase SQL 및 Render/Vercel 환경변수 적용은 사용자가 담당한다.

후속 실제 복구: 사용자가 RTX 4090 새 Pod `ios5pyvfgia32z`를 확보했다.
runpod_esg 키 접속, 비공개 백업의 API 키·원장 복원을 완료했다.
기존 두 모델 BF16 동시 로딩은 CUDA OOM으로 실패했다. 이미지 모델은 BF16 그대로 유지하고
Qwen만 `GPU_ROUTER_QUANTIZATION=int8`로 올리면 두 모델 워밍업 및 API 시작에 성공했다.
bitsandbytes 0.50.2, 상주 VRAM 약 21910/24564MiB였다. 별도 실제 생성/판정 시험 결과로
속도·전력을 기록해야 하며, 5090 BF16의 0.323Wh를 이 구성의 고정값으로 사용하지 않는다.
INT8 양자화는 판정 출력에 영향을 줄 수 있으므로 기준 이미지로 정확도 검증이 별도로 필요하다.
동일 이미지 비교 3회에서 생성·판정 모두 성공했다. Pod 내부 API 왕복 평균 8.2928초,
생성 평균 1.3726초/0.1590Wh, 판정 평균 6.7555초/0.1873Wh, 합계 평균 0.3463Wh였다.
이는 브라우저/Render/Supabase 지연을 제외한다. 인증된 외부 HTTPS health도 확인했다.
새 주소는 `https://ios5pyvfgia32z-8000.proxy.runpod.net`이며 Render GPU_API_URL 갱신이 필요하다.

웹 실연동 중 `GPU_RESULT_UNCONFIRMED`가 발생했고 Pod 로그에 외부 `/v1/generate` 403이 확인됐다.
해당 생성은 Pod 원장에 없었으므로 인증 단계에서 거절된 요청이다. Render 설정 수정은 사용자가 담당한다.
명시적인 GPU_AUTH_DENIED 응답은 앞으로 결과 미확인 대신 인증 오류로 처리한다.
일반 프록시 403, 타임아웃, 잘못된 응답은 실행 여부를 확정할 수 없어 기존 unknown 처리를 유지한다.
서버 간 INT8 판정 응답의 router_quantization 필드도 명시적으로 수용한다.
main의 CLIP 보조 유사도 UI와 GPU 전환 UI를 통합해 두 변경을 모두 유지한다.

## 1. SSH 키, 서버 지문, 모델 SHA는 서로 다르다

| 항목 | 의미 | 재생성 후 조치 |
|---|---|---|
| Mac `~/.ssh/runpod_esg` | 사용자의 SSH 개인키. 서버에는 `.pub`만 등록 | Mac 키는 재사용. 새 컨테이너에 공개키가 없으면 재등록 |
| SSH host fingerprint `SHA256:…` | 접속한 서버의 신원. known_hosts에 저장 | 컨테이너가 새 host key를 만들면 변경 가능. Pod 주소·재생성을 확인한 뒤 해당 항목만 갱신 |
| GPU 모델의 40자리 revision SHA | 다운로드하는 모델 가중치 버전 | 같은 모델 시험을 재현하려면 유지 |

기존 id_ed25519는 passphrase 문제가 있었으므로 이 프로젝트는 runpod_esg 키로 접속에 성공했다.
개인키나 passphrase는 채팅·GitHub·Pod에 복사하지 않는다. 공개키만 서버에 등록한다.

- `Connection refused`: TCP/SSH 서비스 단계 문제다. 공개키 재등록부터 할 문제가 아니다.
  최신 Connect 주소를 확인하고, 웹 터미널에서 `service ssh status`, `ss -lntp`를 확인한다.
- `Permission denied (publickey)`: SSH는 연결됐지만 키 인증에 실패했다. 사용 키·authorized_keys를 확인한다.
- host identification changed: 무조건 검증을 끄지 않는다. 현재 Pod와 새 host fingerprint를 확인한다.

실제 사고에서는 HTTP 8000 추가 후 컨테이너 재생성, SSH 중단, authorized_keys 유실이 발생했다.
웹 터미널에서 `ssh-keygen -A`, `service ssh start` 후 공개키를 재등록해 복구했다.
호스트 지문이 변경된 것은 API 키나 모델 SHA가 바뀌었다는 뜻이 아니다.

## 2. GPU_API_URL은 재접속할 때마다 무조건 바뀌는 값이 아니다

HTTP proxy 형식은 `https://<POD_ID>-8000.proxy.runpod.net`이다.
같은 Pod ID와 내부 포트 8000이면 URL 문자열은 동일하다. 새 Pod를 만들고 ID가 바뀌면 URL도 바뀐다.
Pod를 정지하면 주소가 같더라도 서비스가 응답하지 않는다. 재시작 후 모델 로딩까지 완료해야 한다.

직접 SSH의 외부 IP/포트 매핑은 별도다. 실제로 같은 Pod에서 19885 → 13768로 바뀌었다.
SSH 포트 변경 자체가 HTTP proxy URL의 8000을 바꿔야 한다는 뜻은 아니다. 매회 Connect 화면을 기준으로 확인한다.

GPU_API_KEY는 인증 비밀이다. 백업 worker.env를 복원하면 유지되고, 파일 없이 새 설치를 하면
새 키가 생기므로 Render도 갱신해야 한다. URL 변경과 키 변경은 독립적으로 확인한다.

공식 proxy 형식: https://github.com/runpod/docs/blob/main/integrations/overview.mdx

## 3. 임시 저장 공간 운영과 이번 유실

실제 `findmnt -T /workspace`는 TARGET=/, FSTYPE=overlay였다. `/workspace`라는 이름만으로
영구 저장소인 것이 아니었다. 컨테이너 재생성 후 코드·패키지·모델 캐시가 모두 사라졌다.

임시 디스크를 매회 할당하는 것은 실행 공간 확보다. 볼륨을 추가하는 것만으로 이전 파일이 돌아오지는 않는다.
따라서 매회 필요한 작업은 **새 환경 확보 + 백업 복원/설치 + 모델 다운로드 + 워밍업 + 계측 확인**이다.
Network Volume은 이 재구축/재다운로드를 줄이기 위한 선택지이며 현재는 구매하지 않는다.
일반 Volume Disk와 Network Volume도 다르며, 새 Pod 간 재사용이 필요하면 Network Volume이 해당한다.

공식 저장 공간 설명: https://www.runpod.io/blog/where-did-my-files-go-a-straight-guide-to-runpod-storage

## 4. 다음 회차 체크리스트

1. Connect의 현재 Pod ID/IP/SSH 포트를 확인한다. 검증한 템플릿은
   `runpod/pytorch:1.5.0-cu1281-torch280-ubuntu2404`, Python 3.12/PyTorch 2.8.0+cu128이다.
2. HTTP `8888,8000` 및 SSH 설정을 **설치 전에** 마친다. Mac의 runpod_esg 공개키를 재사용한다.
3. 이 브랜치 코드를 `/workspace/ai-slop-stop`에 준비한다. 이전 기록을 이어갈 경우 비공개 로컬 백업의
   worker.env 및 ai-slop/requests.sqlite3도 먼저 복원한다. 백업은 원본 SHA256을 확인하고 비공개로 취급한다.
4. 프로젝트 루트에서 임시 저장 공간용 명령을 실행한다.

```bash
python -m gpu_worker.ephemeral install --accept-ephemeral
python -m gpu_worker.ephemeral start --accept-ephemeral
```

start는 포그라운드 프로세스다. SSH 종료와 독립적인 실행에는 tmux/감독 프로세스 등을 사용한다.
새 컨테이너에서 /opt의 가상환경은 다시 만들고 모델은 /workspace/huggingface에 받는다.
기존 볼륨용 volume.py install/start는 임시 저장 공간을 거부하는 별도 경로이므로 혼용하지 않는다.

5. 인증 /health와 생성+판정+Zeus 계측 성공을 확인한다.
6. Pod ID가 달라졌으면 Render GPU_API_URL 갱신, worker.env의 키가 달라졌으면 GPU_API_KEY 갱신.
   같은 Pod ID·키를 재사용했으면 해당 값은 바꿀 필요 없다.
7. SQL/환경변수/웹 연결은 `render-runpod-connection.md`를 따른다. 실제 전체 웹 지연도 확인한다.
8. 종료나 Edit 전에 아래 백업을 만들고 Pod 외부에 내려받아 SHA256을 확인한다.

```bash
python -m gpu_worker.volume backup --output /workspace/ai-slop-rescue-YYYYMMDD.tar.gz
sha256sum /workspace/ai-slop-rescue-YYYYMMDD.tar.gz
```

백업은 키와 원장 속 이미지를 포함할 수 있다. GitHub에 올리지 않고 로컬 권한을 600으로 유지한다.
기본 백업에는 모델 캐시가 없으므로 다음에는 다시 다운로드한다. 캐시도 필요하면 --with-cache로 포함한다.
최종 백업 전 신규 요청을 차단하고 진행 중 요청이 끝난 것을 확인한다.

현재 로컬 비공개 백업 ai-slop-rescue-20261103.tar.gz와 실측 JSON은 Git push 대상이 아니다.
새 Pod 재구축 시간도 GPU 대여 시간에 포함된다. Pod 정지 중에는 사이트의 GPU 생성 기능을 사용할 수 없다.
