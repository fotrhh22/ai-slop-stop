# Pod 교체 및 컨테이너 재생성에 대비한 저장·복원

## 현재 선택: 유료 Network Volume 이전은 보류 (2026-11-03)

사용자는 단기 시험마다 임시 Pod를 구성하고 코드·설정·실측 결과를 로컬에 백업하는 방식을 선택했다.
Network Volume 생성 및 새 Pod로의 영구 볼륨 이전은 다음 필수 작업이 아니다. 아래 이전 절차는 향후 선택 사항으로 보관한다.
임시 디스크를 다시 추가하는 것만으로 사라진 파일이 복구되지는 않는다. 새 Pod/컨테이너에서는
로컬 백업 복원, 패키지 재설치, 캐시가 없으면 모델 재다운로드, 워밍업과 인증·계측 확인을 다시 진행한다.
HTTP 8888/8000과 SSH 설정은 설치 전에 완료하고, 실행 중 Edit/재생성 전에는 외부 백업한다.
`volume.py install/start`는 영구 마운트를 요구하므로 이 임시 운영 방식에서는 사용하지 않는다.
단기 시험용 복구 도구는 상위 output/recover-runpod.py이고, 이전 기록/키 복원이 필요하면
먼저 로컬 비공개 백업을 복원한다. 모델 재다운로드 및 환경 재설치 시간도 GPU 대여 시간에 포함된다.
저장소에 포함된 후속 운영 도구는 `python -m gpu_worker.ephemeral install/start --accept-ephemeral`이며,
자세한 매회 실행 절차와 SSH/URL 구분은 [단기 운영 기록](runpod-session-operations.md)을 따른다.

## 실제 적용 상태

2026-11-03 현재 기존 RTX 5090 Pod는 `/workspace`가 루트 overlay인 임시 저장 공간이다.
Network Volume은 아직 생성·연결하지 않았다. 아래 스크립트 준비와 현재 Pod 구조 백업은
영구 볼륨으로 이전이 완료됐다는 뜻이 아니다.

## 보관 대상과 제약

- **Network Volume**: `/workspace/huggingface` 모델 캐시, `/workspace/ai-slop-stop` 코드·비공개 worker.env,
  `/workspace/ai-slop/requests.sqlite3` 요청/계측 원장, 별도 측정 결과를 보관한다.
- **컨테이너**: CUDA·기본 PyTorch 및 `/opt/ai-slop-venv` 패키지는 새 컨테이너에서 재설치한다.
  가상환경을 다른 이미지로 통째로 재사용하지 않는다. 실제 시험한 직접 의존성 버전과 Diffusers 커밋은
  `gpu_worker/requirements.lock.txt`로 고정했다. 전이 의존성 전체를 해시까지 고정한 lock은 아니다.
- 네트워크 볼륨은 Pod와 별개로 유지되지만 같은 데이터센터에 있는 새 Pod 생성 시 연결해야 한다.
  기존 Pod에 나중에 연결할 수 없으므로 **현재 Pod의 설정을 수정해서 이전하지 않는다**.
- 다른 데이터센터로 가려면 그곳의 별도 볼륨으로 복사한다. 자동 동기화가 아니다.
- 원장을 공유하는 두 Pod를 동시에 실행하지 않는다. 이전 Pod에 신규 요청을 차단하고 작업 종료를
  확인한 뒤 최종 SQLite 백업을 복원한다. 앱의 파일 잠금만으로 여러 호스트의 동시 쓰기 안전성을 보장하지 않는다.
- Network Volume ID, 데이터센터, 마운트 경로를 콘솔에서 기록한다. `check`는 별도 영구 마운트를
  확인하지만 일반 Volume Disk와 Network Volume의 상품 구분까지 증명하지는 않는다.

공식 근거: https://www.runpod.io/blog/where-did-my-files-go-a-straight-guide-to-runpod-storage
데이터센터 이동: https://contact.runpod.io/hc/en-us/articles/38651285638547-Knowledge-Article-Moving-Data-Between-Data-Centers

## 1. 현재 Pod를 보존하고 외부 백업

현재 Pod에서 실행한다. 기본 백업은 코드, 키, SQLite 일관성 스냅샷을 포함한다.
모델은 immutable revision으로 다시 받을 수 있다. 다운로드를 생략하려면 `--with-cache`로
약 24GB의 모델 캐시까지 포함하거나, 새 Pod로 캐시 디렉터리를 복사한다.

```bash
python /workspace/ai-slop-stop/gpu_worker/volume.py backup --output /workspace/ai-slop-rescue.tar.gz
sha256sum /workspace/ai-slop-rescue.tar.gz
```

백업 파일을 **현재 Pod 외부**로 내려받고 SHA256을 확인한다. 백업 파일에는 API 키와 원장 속 이미지가
포함될 수 있으므로 개인 파일로 취급하고 로컬 권한을 600으로 유지한다. GitHub에 올리지 않는다.
운영 중 백업 시 진행 중 요청은 원장에 running으로 남을 수 있다. 복원 후 앱은 이를 unknown으로 바꿔
중복 생성을 막는다. 요청을 자동 재실행하지 않는다. 최종 이전에는 작업을 끝내고 백업한다.

## 2. Network Volume과 새 Pod 생성 (사용자)

1. RunPod Storage에서 사용할 GPU가 있는 데이터센터에 **Network Volume**을 만든다.
   현재 캐시 약 24GB를 기준으로 환경·원장·결과 여유를 포함해 60GB 이상을 계획한다.
   Pod를 삭제해도 볼륨 저장 요금은 남으므로 생성 화면의 현재 요금을 확인한다.
2. 그 볼륨을 선택한 상태로 **새 Pod를 생성**한다. `/workspace`에 연결되었는지 확인한다.
3. 기존 검증 템플릿 `runpod/pytorch:1.5.0-cu1281-torch280-ubuntu2404`
   (Python 3.12, PyTorch 2.8.0+cu128), 단일 5090 32GB를 기준으로 한다.
   다른 GPU·이미지는 별도 워밍업/계측/지연 검증을 거친다.
4. HTTP `8888,8000`, SSH 공개키를 **설치 전** 설정한다. 기존 Pod는 아직 삭제하지 않는다.

## 3. 새 Pod에 복원 및 설치

새 Pod에 비공개 백업을 `/tmp/ai-slop-rescue.tar.gz`로 업로드하고 로컬에서 기록한 SHA256과 비교한다.
아래 tar 명령은 직접 만든 신뢰할 수 있는 백업에만 사용한다. 기존 데이터가 없는 새 볼륨에서 복원한다.

```bash
findmnt -T /workspace
sha256sum /tmp/ai-slop-rescue.tar.gz
tar -xzf /tmp/ai-slop-rescue.tar.gz -C /workspace
chmod 600 /workspace/ai-slop-stop/worker.env
python /workspace/ai-slop-stop/gpu_worker/volume.py check
python /workspace/ai-slop-stop/gpu_worker/volume.py install
python /workspace/ai-slop-stop/gpu_worker/volume.py start
```

`check`에서 root overlay 또는 임시 메모리 파일시스템으로 나오면 중단한다. 실행 옵션으로 우회하지 않는다.
`start`는 포그라운드 프로세스이며 모델 로딩·워밍업 후 8000을 연다. API 키는 그대로 유지하되 경로는
현재 볼륨 기준으로 계산한다. 새 Pod의 HTTPS 주소는 달라지므로 연결하는 백엔드 URL도 갱신한다.

## 4. 재시작 자동 실행 설정

처음 설치 전 RunPod의 사용자 시작 스크립트/템플릿 시작 설정에 아래 명령을 등록한다.
정확한 입력 위치는 사용 중인 템플릿 설정 화면에서 확인한다. 기본 Jupyter/SSH 시작 명령을 대체하지 않는다.

```bash
python /workspace/ai-slop-stop/gpu_worker/volume.py install && exec python /workspace/ai-slop-stop/gpu_worker/volume.py start
```

이 스크립트는 해당 실행 경로를 준비한 뒤 다음 재시작부터 사용한다. 새 컨테이너마다 설치를 다시 확인하지만
모델 캐시는 같은 볼륨에서 읽는다. 자동 시작 설정이 없는 Pod는 수동으로 같은 명령을 실행한다.

## 5. 전환 완료 기준

- 새 Pod의 인증된 `/health`가 `ok=true`, `router_ready=true`를 반환한다.
- 생성·판정 1회 이상 성공하고 Zeus J/Wh가 유한한 양수다. 실제 기준 이미지로 판정 품질도 확인한다.
- 기존 요청 ID 조회가 같은 결과를 반환하며 중복 생성되지 않는다.
- 백엔드의 URL을 새 Pod로 전환하고 웹 전체 왕복을 확인한다. API 키를 바꿨다면 백엔드도 함께 갱신한다.
- 결과를 외부 백업한 다음에만 기존 Pod를 종료한다. 볼륨 자체는 삭제하지 않는다.
- Network Volume 연결 Pod의 실제 컨테이너 재생성/다른 Pod 재연결 시험까지 해야 내구성 검증 완료다.
  현재는 유료 볼륨과 새 Pod를 만들지 않았으므로 이 실물 이전 시험은 아직 수행하지 않았다.
