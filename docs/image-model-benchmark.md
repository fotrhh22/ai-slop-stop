# 한국어 이미지 모델 내부 비교

사이트·Render·Supabase를 거치지 않고 준비된 Pod의 이미지 생성 API를 직접 호출한다.
현재 도구는 이미 실행 중인 worker의 모델을 시험한다. 모델 다운로드/교체/Pod 시작은 하지 않는다.
Z-Image/Klein 9B/Qwen-Image의 모델별 로더는 아직 구현되지 않았으며 이 도구가 대신 설치하지 않는다.
이후 worker에 모델별 로더를 추가하면 같은 테스트 파일과 보고서 형식을 재사용한다.

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
