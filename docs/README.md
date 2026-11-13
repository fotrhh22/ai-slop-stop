# 캠페인 문서

최신 구조·모델·실측 범위·재실행은 [OPERATIONS](current/OPERATIONS.md)가 기준이다. 실제 캠페인 진행 완료와 과거 시험의 미확인 항목을 구분한다.

## 현재 운영

- [고정 목표 이미지 선택 흐름](current/CURATED_CHALLENGE_TARGETS.md)
- [캠페인 구조·계측·운영](current/OPERATIONS.md)
- [PT-210 · Atkinson 선택 기록](current/PRINT_DITHERING_DECISION.md)
- [미통과 세션 자동 삭제](current/abandoned-session-cleanup.md)
- [Phase 0–5 적용 안내](current/campaign-bike-rollout.md)
- [Bike 대기 참가자 삭제](current/delete-waiting-participant.md)
- [Direct AI evaluation](current/direct-ai-evaluation.md)
- [Gemini 자전거 고정 비교 목표 (2026-11-04 최신 결정)](current/gemini-reference-bike-target.md)
- [Klein 9B NF4와 공통 키 — 2026-11-06](current/klein9b-shared-credentials.md)
- [현재 RunPod를 기존 Render/Vercel에 연결](current/render-runpod-connection.md)
- [매회 Pod 준비를 한 명령으로 실행](current/runpod-automation.md)
- [FLUX.2 Klein 4B + Qwen3.5-4B 전환 실행 계획](current/runpod-zeus-migration.md)

## 결과·기록

- [실제 캠페인 결과](campaign-20261112/README.md)
- [과거 모델 검증](archive/2026-11-campaign/model-validation-results.md)
- [과거 문서 목록](archive/README.md)

`runpod-operator.env.example`은 운영 도구가 참조하는 예시 경로로 유지한다. 실제 키는 포함하지 않는다.

## 로컬 작업 폴더

상위 폴더의 AIMI·생성기·자전거·토큰 도구는 별도 프로젝트다. 루트의 PDF/Word 5개는 `docs/local-reference/`로 옮겨 로컬 보관하며 Git에서 제외했다. 프린터 비교 출력은 `output/pt210-test/`에 유지했다.

원격 저장소 1a306e7에는 로컬보다 최신 GPU·CLIP·계측 코드가 있다. 이번 작업은 최신 원격 문서를 기준으로 정리하고 로컬의 기존 실행 소스는 덮어쓰지 않았다. 로컬과 원격 코드를 동일 버전이라고 가정하지 않는다.
