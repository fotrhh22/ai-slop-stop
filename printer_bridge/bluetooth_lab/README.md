# PT-210 독립 Bluetooth 영수증 테스트 2종

DB·Render·Supabase·CUPS 없이 **준비된 예시 영수증 파일 → Bluetooth → 프린터**를 시험하는 Mac 전용 환경이다. 기존 USB 자동 출력 워커의 환경·설정·프로세스는 변경하지 않는다.

BLE와 Classic SPP를 각각 구현했다. **보유 프린터가 두 방식을 모두 지원한다고 확인된 것은 아니다.** 초기 구축은 프린터 없이 진행했고, 이후 BLE 실물 출력과 신호 수집까지 확인했다. SPP 실물 출력 성공은 미확인이다.

## 실물 시험 후 현재 권장 실행법

보유 PT210L의 **BLE 실제 출력이 확인됐다.** SPP는 사용자 요청으로 시험을 중단했다. 결과와 채택/제외 설정은 [HARDWARE_RESULTS.md](HARDWARE_RESULTS.md)에 기록했다.

이 Mac의 `ble.local`에는 확인된 BLE UUID·18F0/2AF1 인쇄 특성과 237B / 15ms / without-response / 종료 전 2초 대기가 저장돼 있다. 기본 파일은 같은 이미지의 `GS v 0` 버전이며, 전체가 정상이고 더 부드럽다는 사용자 확인을 받았다.

```sh
./ble-test --send
./ble-signal --duration 30
./ble-signal --duration 30 --distance-m 3
./ble-test --send --distance-m 3
```

`--distance-m`는 직접 잰 거리의 기록용이다. RSSI를 m로 환산하지 않는다. 신호 CSV와 인쇄 실행 JSON은 `runs/`에 저장한다. 연결 중 광고가 안 나오는 경우가 있으므로 신호 측정은 인쇄 전후에 별도로 실행한다.

## 준비된 예시 영수증

- 미리보기: `samples/example-receipt.png`
- 공통 인쇄 파일: `samples/example-receipt.escpos` (71,809 bytes)
- 구성: 기존 사진·한글·사용량·발전 체험 결과·QR이 포함된 예시. 실제 참가자 기록이 아님.
- 초기 BLE/SPP 비교 파일은 같은 SHA-256이다. BLE 실물 튜닝 후 추가한 GS v 0 파일은 인코딩만 다르고 같은 PNG 픽셀을 사용한다.
- 이미지 조정·한글 렌더링·디더링은 이미 완료되어 출력 명령에서는 수행하지 않음.

## 현재 설치 상태와 재구축

현재 이 Mac에는 `.venv`(Python 3.11), Bleak 3.1.0, pyserial 3.5, Apple IOBluetooth native SPP helper가 준비돼 있다. `build/`, `.venv/`, 개인 기기 설정과 출력 로그는 Git에 올리지 않는다.

```sh
cd /Users/hunhokang/Downloads/esg_campaign/output/zeus-migration/printer_bridge/bluetooth_lab

# 현재 환경에서는 바로 가능: 프린터/인터넷/DB 접근 없음
./ble-test --dry-run
./spp-test --dry-run

# 새 Mac/새 checkout에서 최초 한 번
./setup.command
```

기본 Python 경로는 `/opt/homebrew/bin/python3.11`이다. 다른 Python 3.11 경로는 `PT210_PYTHON=/절대경로/python3.11 ./setup.command`로 지정한다. native 빌드는 Xcode Command Line Tools가 필요하다. macOS 전용 의존성 버전은 `requirements-macos.lock.txt`에 고정했다.

## 테스트 1: BLE

프린터를 사용할 수 있을 때 전원을 켜고, 다른 휴대폰 인쇄 앱의 연결을 해제한다. Mac Bluetooth 사용 권한을 요청받으면 실행 앱에 허용한다. BLE 검색은 아래와 같다.

```sh
./ble-test --scan
./ble-test --inspect '이-Mac에서-확인한-BLE-UUID'
```

프린터 후보가 기본 이름 필터에서 보이지 않으면 기존 probe로 이름 없는 장치까지 로컬 확인할 수 있다.

```sh
# repository root에서 실행
printer_bridge/bluetooth_lab/.venv/bin/python -m printer_bridge.bluetooth.probe --scan --all
```

서비스를 열거한 뒤 올바른 인쇄 characteristic UUID와 지원되는 write 타입을 확인한다. 인터넷 예시 UUID나 첫 번째 writable 항목을 검증 없이 선택하지 않는다. macOS BLE UUID는 Classic MAC 주소와 다르다.

```sh
cp ble.example.json ble.local
# ble.local의 device / characteristic을 실제 값으로 변경
# response=no는 write-without-response, yes는 write 속성을 요구

# 준비 파일 한 장을 실제로 전송
./ble-test --send
```

설정 파일 없이 한 번만 지정하려면:

```sh
./ble-test --send --device '실제-BLE-UUID' --characteristic '실제-write-UUID' --response no
```

쓰기 크기는 `--chunk-bytes`와 기기가 보고하는 한도 중 작은 값으로 제한한다. 한도가 20B면 182B를 강제하지 않는다. 초기 간격은 40ms이며 실물 검증 후에만 조정한다. 지원 속성이 맞지 않으면 출력 전에 실패한다.

## 테스트 2: Classic SPP — macOS native RFCOMM

Linux `AF_BLUETOOTH`가 아닌 **Apple IOBluetooth** 구현이다. 먼저 Classic 검색 및 SPP 서비스 확인을 수행한다. 아래 명령은 인쇄하지 않는다.

```sh
./spp-test --scan
./spp-test --scan --address 'AA:BB:CC:DD:EE:FF'
```

두 번째 명령은 선택 기기의 SDP를 조회해 Serial Port Profile `0x1101`과 실제 RFCOMM 채널을 확인한다. 이름을 못 찾았거나 SDP에 SPP가 없으면 지원 여부를 단정하지 말고 기기 전원·연결 모드를 확인한다.

실제 전송 전 macOS Bluetooth 설정에서 해당 프린터를 pairing한다. 이 도구는 임의의 PIN으로 자동 pairing하지 않는다.

```sh
cp spp.example.json spp.local
# spp.local의 address를 실제 Classic MAC 주소로 변경
# channel=auto는 SDP에서 실제 채널을 조회함

# 준비 파일 한 장을 실제로 전송
./spp-test --send
```

검사한 RFCOMM 채널을 명시하려면:

```sh
./spp-test --send --address 'AA:BB:CC:DD:EE:FF' --channel 1
```

`1`은 예시이며 실제 SDP 결과가 우선이다. native helper는 RFCOMM MTU·flow control을 확인하고 동기 쓰기로 전송한다. Python 호출자는 전체 timeout을 제한하고, 중단 뒤 helper를 자동 재실행하지 않는다.

### SPP 보조 경로: 실제 시리얼 포트가 생성되는 경우

pairing 뒤 `ls /dev/cu.*`에 프린터 전용 포트가 생기면 pyserial 경로도 사용할 수 있다.

```sh
./spp-test --send --port '/dev/cu.실제로생성된프린터포트'
```

일반 `Bluetooth-Incoming-Port`, `debug-console`은 거부한다. 이 경로는 실제 프린터 전용 포트가 생성되는 경우에만 유효하다. `--baud`는 가상 시리얼 설정이며 Bluetooth 무선 속도를 의미하지 않는다.

## 공통 실행 옵션과 결과

- `--send` 없으면 기본은 오프라인 dry-run. scan/inspect는 별도 명시 시에만 무선 접근.
- 다른 미리 준비한 파일은 `--file /절대경로/receipt.escpos`로 지정.
- `--chunk-bytes`, `--interval-ms`, `--settle-seconds`: `ble.local`에서 검증값을 저장할 수 있다. 이 Mac은 237B/15ms/2초를 채택했고, 프로파일 없는 기본은 182B/40ms/1초다.
- `--connect-timeout 15`, `--write-timeout 10`, `--total-timeout 180`: timeout 제어. total-timeout은 native 및 serial SPP helper 상한.
- `ble.local`, `spp.local`: 기기 정보만 저장하며 Git ignore 대상. 명령행 인자가 우선.
- 실제 전송은 `runs/`에 실행별 기록. BLE/SPP 간 로컬 잠금으로 동시 쓰기 방지.
- `TRANSPORT_WRITES_COMPLETED`는 쓰기 호출 완료이며 종이 출력 확인이 아님.
- 전송 중 오류·timeout·사용자 중단은 불확실 상태로 기록. **자동 재출력·다른 방식으로 자동 우회 없음.** 종이를 확인한 뒤에만 사람이 명령을 다시 실행.

이 lab은 기존 자동 출력 워커와 별도 도구이므로, 나중에 같은 프린터로 실제 시험할 때 기존 워커에 대기 작업이 없는지 확인하고 한 경로만 사용한다. 이 도구가 기존 작업을 취소·복제하거나 USB 큐를 재개하지 않는다.

현재 버전은 명령 한 번에 연결→파일 전송→연결 해제다. 영수증 사전 변환으로 준비 비용은 제거했다. 여러 장에서 Bluetooth 연결 유지 시간을 비교하는 상주 모드는 이번 2종 단독 연결 시험에 포함하지 않았다.

## 프린터 없는 검증 / 완료 범위

```sh
./verify.command
```

확인 항목: 동일 영수증 SHA, BLE 협상 한도 준수, write 속성 불일치 차단, timeout/끊김 시 무재시도, SPP 부분 쓰기 바이트 보존, Mac native helper 빌드 및 무선 접근 없는 dry-run. 최초 15개 테스트에 이어 실물 보완과 신호·픽셀 보존 검증을 추가했다. 최신 결과는 OFFLINE_VALIDATION.json 참조.

현재 BLE 연결·한글/사진/QR 전체 출력·신호 수집은 실물 확인됐다. 미검증: 거리별 안정 범위, 장기 연속/절전 복구, SPP 실물 출력. SPP 시험은 중단했다.

## 레퍼런스

- [PT-210 SDK 고정 커밋](https://github.com/filipsedivy/goojprt-pt210-sdk/tree/f901d209a3bfcd3bff812648c836ce2ebba8d55f): BLE/SPP 역할 분리와 분할 전송 참고. SDK 코드 전체를 복제/실행하지 않음.
- [Classic SPP 구현 사례](https://github.com/dvbit/esc-pos-Bluetooth-printer/tree/9f8a8a2b4499de470155086cf2720749994893df): Linux 방식 비교 참고.
- [Bleak 공식 API](https://bleak.readthedocs.io/en/latest/api/client.html): GATT 쓰기 속성·payload 한도.
- [Apple RFCOMM API](https://developer.apple.com/documentation/iobluetooth/iobluetoothrfcommchannel): macOS native 채널.
