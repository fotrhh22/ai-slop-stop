# AI SLOP STOP CHALLENGE

목표 이미지를 프롬프트로 재현하고, 생성·판정의 자원 소비를 자전거 발전 체험과 영수증으로 연결한 오프라인 캠페인입니다.

**실제 캠페인 진행 완료 · 2026-11-13 운영자 확인**

[챌린지 웹](https://ai-slop-stop.vercel.app/) · [자전거 계측 저장소](https://github.com/fotrhh22/ai-slop-bike-calculation)

## 체험 흐름

목표 관찰 → 프롬프트 입력 → 생성·판정 → 사용량/에너지 기록 → 참가자 코드 → 자전거 발전 체험 → 최종 영수증 출력.

Google의 [Say What You See](https://artsandculture.google.com/experiment/say-what-you-see/jwG3m7wQShZngw)를 프롬프트 학습 형식의 레퍼런스로 삼고 자원 계측·오프라인 체험을 추가했습니다.

## 구성·사용 서비스

| 구성 | 기술·서비스 |
| --- | --- |
| 웹·API | React·TypeScript·Vite / FastAPI / Vercel·Render |
| 데이터 | Supabase PostgreSQL·Storage |
| 생성·판정 | Gemini API 또는 RunPod의 Klein 9B NF4·Qwen3.5-4B |
| 보조 유사도 | 선택적 Mac CLIP 워커 |
| 계측·현장 연계 | Zeus·NVML / 별도 자전거 측정소 |
| 인쇄 | Pillow·Atkinson·ESC/POS·CUPS·PT-210 |

GPU 실행 에너지, Gemini 토큰, 자전거 실측/환산값은 구분해 기록합니다. 미계측 값을 0으로 취급하거나 서비스 전체 에너지로 확대하지 않습니다.

## 문서

- [실행·모델·계측·운영 기준](docs/current/OPERATIONS.md)
- [Atkinson 선택 과정](docs/current/PRINT_DITHERING_DECISION.md)
- [실제 캠페인 결과](docs/campaign-20261112/README.md)
- [프린터 운영](printer_bridge/README.md) · [DB 설정](supabase/README.md)
- [문서 목록·과거 기록](docs/README.md)

이 저장소는 캠페인용이며 앱인토스 배포 앱 AIMI와 별개입니다. 캠페인의 Vercel 웹은 종료된 AIMI 웹 데모와 다른 서비스입니다.
