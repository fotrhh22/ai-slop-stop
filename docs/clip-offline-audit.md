# Mac에서 저장된 판정 데이터에 CLIP 점수 추가

`scripts/benchmark_clip_local.py`는 운영 워커와 별개인 일회성 오프라인 도구다.
Render·Supabase·RunPod에 연결하지 않고 env/키도 읽지 않는다.
최초 실행 때 공식 CLIP 가중치를 다운로드할 수 있다. 캐시가 있으면 네트워크 없이 계산한다.
새 이미지를 생성하거나 Qwen/Gemini를 재호출하지 않는다.

기존 판정 실험의 dataset.json, router-results.json, gemini-results.json과 이미지 파일을 읽는다.
이미지 SHA256을 검증하고 **판정에 사용했던 동일한 512px JPEG**를 기존 로컬
`clip_worker.worker.Encoder`의 ViT-B/32 전처리로 계산한다. CLIP 내부 입력은 224px center crop이다.
1024px 원본을 다시 사용하는 별도 실험과 점수를 혼합하지 않는다.

CLIP 의존성이 있는 Python에서 실행:

```bash
cd /Users/hunhokang/Downloads/esg_campaign/output/zeus-migration
python -m pip install -r clip_worker/requirements.txt
python scripts/benchmark_clip_local.py \
  benchmark-results/router-20261105T072819Z-e9e291de \
  --device auto
```

이 Mac에서는 기존 Python 환경을 변경하지 않도록 CLIP 코드만 `.cache/clip-python`에 분리했다.
다음 명령으로 다시 실행할 수 있다(새 시각의 결과 폴더 생성).

```bash
cd /Users/hunhokang/Downloads/esg_campaign/output/zeus-migration
PYTHONPATH="$PWD/.cache/clip-python" \
  /Users/hunhokang/Downloads/esg_campaign/.venv/bin/python \
  scripts/benchmark_clip_local.py \
  benchmark-results/router-20261105T072819Z-e9e291de --device auto
```

`.cache/clip-python`을 지웠다면 기존 Torch 환경은 유지한 채 다음으로 CLIP 코드만 다시 설치한다.

```bash
/Users/hunhokang/Downloads/esg_campaign/.venv/bin/python -m pip install \
  --target .cache/clip-python --no-deps \
  'clip @ git+https://github.com/openai/CLIP.git@d05afc436d78f1c48dc0dbf8e5980a9d471f35f6'
```

auto는 MPS가 실제 사용 가능하면 MPS, 아니면 CPU를 사용한다. 실제 장치를 메타데이터에 저장한다.
처음 만난 이미지는 인코딩하고 같은 이미지가 재등장하면 기존 벡터 캐시를 사용한다.
따라서 쌍별 elapsed_ms는 공정한 속도 비교 지표가 아니다.
모델 SHA256·입력 SHA256·Torch/CLIP 버전·장치·실행 시각을 함께 남긴다.

기존 결과 폴더 아래 새 `clip-local-<UTC시각>` 폴더에 다음 파일이 생성된다. 기존 결과는 덮어쓰지 않는다.

- results.csv: 10쌍 × 기존 판정 모드별 행. 코사인, 기준 라벨, 원래 응답/최종 판정, 근거, 오류 구분.
- results.json: 쌍별 점수, 기존 판정, 입력 출처, 재현 메타데이터.
- report.html: 이미지 쌍과 세 판정 결과. **정답→FAIL** 사례를 맨 위에 표시.
- summary.json: 각 판정의 정답→FAIL / 유효 정답 수, 오답→PASS / 유효 오답 수. 미응답은 별도 집계.
- threshold-audit.csv: CLIP 점수 이상이면 PASS라고 가정했을 때 표본 내 두 오류의 변화.
- images/: 실제 비교 입력 복사본. 이 폴더만 옮겨도 HTML을 볼 수 있다.

코사인은 정답 확률이나 백분율이 아니다. 정답→FAIL을 우선 줄이는 운영 방향은 정했지만,
정답 4쌍·오답 6쌍만으로 임계값을 확정하지 않는다. 이 도구는 판정을 자동 변경하지 않는다.
기존 체크리스트가 정한 오답 중 행사에서 허용할 항목이 있다면 별도 사람 검토 라벨을 추가한 뒤
비교해야 한다. 기존 라벨을 CLIP 점수에 맞춰 바꾸면 검증이 순환하므로 원래 라벨을 보존한다.
