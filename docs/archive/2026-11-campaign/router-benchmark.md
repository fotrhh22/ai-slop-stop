# 판정 비교와 GPU 계측

Mac에서 SDK가 설치된 Python으로 실행한다. 저장된 Pod 연결 정보와 기존 로컬 Gemini 환경변수를 사용한다.

```bash
./runpod benchmark-router --profile open-5090
```

현재 구현은 5090 BF16 기준이다. 로컬에 google-genai·Pillow·서버 의존성이 설치되어 있어야 한다.
Pod에는 최신 공통 GPU 의존성이 설치되어 있고 GPU가 비어 있어야 한다. 운영 worker를 자동 종료하지 않는다.

기존 생성 이미지에서 10쌍을 만들고, 호출 전에 4 PASS/6 FAIL 라벨과 근거를 고정한다.
TARGET는 Klein 9B 결과, GENERATED는 기존 네 모델의 저장된 결과다. 실제 배포 사이트 전체 목표 이미지의 대표 표본은 아니다.
사과·악어 폭격기·도형·오리 사례를 사용하고, 한글 렌더링 사례는 제외한다.
512px JPEG 입력을 한 번 생성해 Qwen/Gemini에 동일한 바이트로 전달한다.
원본 1024px 이미지의 출처와 변환된 입력의 SHA256을 기록한다. 추가 이미지를 생성하지 않는다.

- Qwen3.5-4B BF16 + 기존 Gemini 공통 evaluate.md/기존 Qwen JSON 지시.
- 같은 Qwen BF16 + 기존 지시/목표별 체크리스트/필수 category 검사.
- 기존 Gemini router 모델 + 기존 evaluate.md/운영 route_verdict 함수.

개선안의 최종 verdict는 필수 category가 MISMATCH일 때 PASS를 FAIL로 바꿀 수 있다.
모델 자체 raw verdict와 최종 verdict를 모두 저장한다. 체크리스트 제공과 서버 집계 효과가 결합되어 있어 순수한 모델/프롬프트만의 비교는 아니다.
허용 기준 자체가 기존 포괄 프롬프트보다 구체적이므로 비교 결과는 이 고정 라벨과 운영 목표 기준의 일치도를 뜻한다.

Qwen은 기존 Runner를 재사용하며 FLA/causal-conv1d를 확인한다. 이미지당 최대 262144 pixels, 최대 새 토큰 256, thinking off, greedy decoding이다.
CPU threads=4, OMP/MKL=4, Inductor compile threads=2를 명시한다.
실측 시간·mWh·50ms NVML 최대 표본·PyTorch peak allocated/reserved·input/output tokens를 요청별 기록한다.
모델 초기화 probe 1회와 각 모드 512px 워밍업 1회씩은 요약에서 제외한다.
따라서 본 판정은 최대 30회, Qwen 워밍업은 별도 3회다. 이미지 생성은 0회다.
Gemini는 실제 GPU 전력/VRAM을 조회할 수 없어 null/미계측으로 표시한다. 토큰으로 임의 추정하지 않는다.
Gemini API 시간과 Qwen 직접 실행 시간은 서로 다른 범위이며, 웹 전체 응답 시간은 아니다.

기존 SDK 및 함수의 retry attempts=1을 사용한다. 요청 ID를 먼저 저장하며 응답 미확인 요청을 자동 재실행하지 않는다.
Gemini 오류가 발생하면 해당 모드는 중단하고 미완료로 표시한다. 미완료 결과를 정상 정확도 0/100%로 표시하지 않는다.
서버 DB·운영 판정 프롬프트·참가자 데이터는 수정하지 않는다. Gemini 키는 Pod에 전송하지 않는다.

결과는 benchmark-results/router-<실행ID>/report.html, summary.json, dataset.json, router-results.json, gemini-results.json에 남는다.
개별 이미지 쌍·예상 판정·원래 모델 응답·최종 verdict·시간·전력·VRAM을 확인할 수 있다.
중단 후 새 호출 없이 GPU 결과만 수집하려면 같은 workspace에서:

```bash
./runpod benchmark-router --profile open-5090 --collect 실행ID
```
