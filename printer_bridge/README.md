# PT-210 행사 자동 출력

Atkinson / 384dots / 한글 이미지 인쇄. Render와 Vercel은 그대로 사용하고,
행사장 Mac이 HTTPS로 인쇄 작업을 가져와 로컬 CUPS에 전송합니다.
외부에서 맥북으로 접속하는 서버나 브라우저 localhost 접근은 사용하지 않습니다.

## 활성화 순서

1. 사용자가 Supabase SQL Editor에서 `supabase/migrations/202611010001_print_jobs.sql` 실행.
   자동 적용하지 않습니다. 이전 3개 마이그레이션이 적용된 프로젝트에 실행합니다.
2. 사용자가 Render에 `PRINT_STATION_ID`, `PRINT_PAIRING_TOKEN`, `PRINT_WORKER_TOKEN` 추가 후 백엔드 배포.
   이 컴퓨터에는 생성한 값을 `printer_bridge/render-env.local`에 비공개로 준비했습니다.
   이 파일 내용만 Render 환경변수에 복사합니다. Git/Vercel VITE 변수에는 넣지 않습니다.
3. 프런트엔드 배포. Vercel의 기존 `VITE_API_BASE`, `VITE_USE_MOCK=false`를 유지합니다.
4. 프린터를 USB로 연결하고 전원을 켠 뒤 프로젝트에서 아래 명령 또는 `start.command`를 실행합니다.

```sh
.venv/bin/python -m printer_bridge.worker --run
```

5. 배포 사이트 사이드바의 **행사 프린터 OFF**를 펼치고 `PRINT_PAIRING_TOKEN` 값을 입력해 연결합니다.
   코드 입력은 운영자가 하며 참가자에게 공개하지 않습니다. 현재 탭의 sessionStorage에만 저장합니다.
6. 연결 후 **새 챌린지**를 시작합니다. 이전에 만든 세션은 소급 출력하지 않습니다.
   AI PASS와 STAFF PASS 모두 성공 저장 시 DB 트리거가 인쇄 작업을 한 번 생성합니다.

프린터를 끄거나 노트북을 닫으면 자동 출력할 수 없습니다. Mac이 인터넷에 연결되어 있어야 합니다.
화면의 행사 모드 OFF는 새로 만드는 세션에만 적용하며 기존 대기 작업은 취소하지 않습니다.

## 출력 디자인

성공 이미지(Atkinson), 크게 강조한 총 토큰, 생성/판정 호출 수, 평균 프롬프트 글자수,
모델별 토큰, 성공 프롬프트, AI 승인 시 통과 이유를 표시합니다.
텍스트는 디더링하지 않습니다. 58mm 용지에서 실제 48mm/384dots만 사용하고 수동 절취 여백을 둡니다.
테스트 문구와 내부 DB 검증 항목은 인쇄하지 않습니다.
미확인 사용량은 미확인으로 표시하고 에너지 추정치나 개인 QR을 임의로 만들지 않습니다.

## 상태와 장애 대응

- PENDING: 작업 대기.
- RENDERING: 저장된 영수증/이미지 검증 및 렌더링. 5분 임대 만료 시 다른 워커가 안전하게 재준비할 수 있음.
- SENDING: 전송 시작. 이 단계에서는 자동 재시도하지 않음.
- SENT: CUPS가 작업을 접수함. 실물 출력 완료를 의미하지 않음.
- FAILED: 전송 전 렌더링 실패. 운영자가 원인 확인 후 재출력.
- UNKNOWN: 전송 결과 불확실. 종이와 macOS 인쇄 큐를 확인한 후에만 명시적으로 재출력.

상태 확인:

```sh
.venv/bin/python -m printer_bridge.worker --status
lpstat -p -o
```

실물 및 CUPS 큐를 확인한 뒤 추가 사본을 요청할 때만 실행:

```sh
.venv/bin/python -m printer_bridge.worker --reprint PRINT_JOB_UUID
```

이 명령은 별도 재출력 작업을 만들므로 반복 실행하면 사본이 추가됩니다.
워커가 실행 중이어야 큐에 등록된 사본을 출력합니다.
`UNKNOWN`의 원래 CUPS 작업이 남아 있으면 나중에 출력될 수 있으므로 먼저 확인해야 합니다.

## 로컬 파일과 의존성

- `printer_bridge/.env.local`: worker 전용 비밀키. Supabase 서비스 키가 아님.
- 루트 `.env.local`: 로컬 API용 출력 설정. 기존 Gemini/Supabase 키는 변경하지 않음.
- `~/Library/Application Support/AI Slop Print`: 비공개 저널, 영수증 PNG 미리보기, RAW 파일.
- 한 워커 프로세스만 실행하도록 파일 잠금 사용. 전송 전후 기록은 fsync 후 저장.
- 행사 종료 후 PNG/RAW에는 참가자 프롬프트가 있으므로 보관 정책에 따라 삭제.
  실행/재시작 중에는 저널을 삭제하지 말 것.
- 필요한 패키지는 `printer_bridge/requirements.txt`의 Pillow. 이미지 렌더링에 API 모델을 호출하지 않음.
- 한글 폰트 기본값은 macOS AppleGothic. 다른 환경은 `PRINT_FONT_PATH`로 TTF 지정.
- 자동 워커는 `PT210_USB_BRIDGE`에 직접 비트맵을 보내므로 Poppler가 필요하지 않음.
- 일반 앱의 `PT210_IPP` PDF 인쇄는 앞서 설치한 Poppler 경로에 의존함. 독립 배포 시 별도 설치 경로로 변경 필요.
- 현재 RAW 전송 어댑터는 macOS용. Windows 어댑터는 이번 구현에 포함하지 않음.

## 개발 검증

```sh
.venv/bin/python -m unittest server.tests.test_printing printer_bridge.test_worker
cd web && npm run build && npm run test:thermal && npm run test:receipt-print && npm run test:evaluation
```

Supabase migration/실서비스 자동 출력은 사용자의 SQL 적용 및 배포 후 행사 모드에서 별도 검증해야 합니다.
실제 모델 API를 테스트 목적으로 호출하지 않습니다.

별도의 새 검증 환경에서 서버 37개·워커 7개 테스트를 확인했습니다.
기존 로컬 .venv의 Gemini SDK는 구버전으로 기존 생성·판정 테스트 일부와 호환되지 않지만, 이번 작업에서 변경하지 않았습니다.
출력 워커는 Gemini SDK를 사용하지 않습니다.

## 출력 대기 및 진단

작업 확인 주기는 기본 1초이며 로컬 `PRINT_POLL_SECONDS`로 0.5~60초 사이에서 조정할 수 있습니다. Render 환경변수는 변경하지 않습니다. 영수증 다운로드 중 일시적인 네트워크/서버 오류는 전송 전에만 최대 두 번 재시도합니다. 프린터 전송이 시작된 작업은 자동 재출력하지 않습니다. 워커 로그에 payload, render, encode, authorize_send, cups_submit 단계별 소요 시간이 기록됩니다. `PAYLOAD_FAILED`는 서버 데이터 수신 실패이며 `RENDER_FAILED`와 구분됩니다.

PostgreSQL 시각의 소수초는 1~6자리 모두 처리합니다. Python 3.9 워커에서도 소수초가 5자리인 영수증이 렌더링 중 실패하지 않도록 정규화합니다.
