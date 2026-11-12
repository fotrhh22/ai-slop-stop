# PT210L BLE 실물 검증 — 2026-11-12

보유 프린터의 BLE 연결과 실제 종이 출력이 확인됐다. Classic SPP는 검색·Mac 시리얼 포트 생성까지만 확인했으며 실물 출력은 확인되지 않았다. 사용자 요청으로 SPP 시험은 중단하고 BLE를 선택했다.

## 확인한 BLE 구성

- 프린터 이름: PT210L 계열. Mac별 UUID는 비공개 `ble.local`에 저장.
- 실제 출력 확인 서비스: `000018f0-0000-1000-8000-00805f9b34fb`
- 실제 출력 확인 write: `00002af1-0000-1000-8000-00805f9b34fb`
- 지원 속성: write / write-without-response.
- CoreBluetooth가 보고한 without-response 최대값: 237 bytes.
- 레퍼런스의 `49535343-*` 서비스도 열거됐지만 첫 짧은 시험은 종이에 나오지 않았다. 이를 인쇄용으로 채택하지 않았다. 초기 버전은 마지막 write 직후 연결을 닫았으므로 특성 문제와 큐 종료 타이밍의 원인은 분리 검증되지 않았다.
- 마지막 write 후 대기를 추가해 OS 큐가 처리될 시간을 준다. 이 대기는 물리 완료 ACK가 아니다.

## 속도·품질 비교

아래 시간은 `send_seconds`로, 기기 검색/접속 이후 write 구간과 마지막 연결 유지 시간을 포함한다. 실제 종이 첫 이동/마지막 이동을 센서로 측정한 값이 아니다. 사용자에게 실제 종이 출력 결과를 별도로 확인했다.

| 이미지 방식 | 쓰기 | chunk | 추가 간격 | 기록 시간 | 종이 결과 |
|---|---|---:|---:|---:|---|
| 기존 ESC * 전체 71,809B | response | 182B | 40ms | 30.695초 | 사진·한글·QR 끝까지 정상, 느림 |
| 기존 ESC * 전체 | response | 237B | 0ms | 11.251초 | 빨라졌으나 끊기는 듯한 출력 보고 |
| 기존 ESC * 전체 | without-response | 237B | 20ms | 8.504초 | 추가 속도 개선 요청. 엄격한 최종 품질 확인은 미완료 |
| 기존 ESC * 전체 | without-response | 237B | 10ms | 5.451초 | 마지막에 이상한 텍스트 발생 → **실패·제외** |
| GS v 0 상단 192행 | response | 237B | 0ms | 3.194초 | 짧은 이미지 정상 |
| GS v 0 전체 71,384B / 128행 strips | without-response | 237B | 15ms | 6.947초 | **사용자 확인: 끝까지 정상이고 더 부드러움** |

검증한 마지막 구성을 이 Mac의 `ble.local` 기본 시험값으로 저장했다. `./ble-test --send`는 해당 설정과 `samples/example-receipt-gsv0.escpos`를 사용한다. 다른 프린터·펌웨어·거리에서도 무조건 같은 결과를 보장하는 값은 아니다. 특히 실패한 10ms 값은 기본값으로 적용하지 않는다.

GS v 0 파일은 기존 PNG의 픽셀을 그대로 다른 ESC/POS 명령으로 인코딩한 것이다. 사진·글꼴·QR 모양을 바꾸거나 저해상도로 줄이지 않았다. 단위 테스트가 각 strip을 역해석해 원본 1-bit pixel bytes와 일치하는지 검증한다.

## 신호 세기 측정

같은 프린터를 대상으로 5초 실제 광고 RSSI 측정 성공. 14개 표본에서 -55~-50dBm, 마지막 rolling median -52dBm이었다. 실제 거리는 입력하지 않았으므로 이 결과는 특정 거리나 최대 연결범위를 의미하지 않는다.

`ble-signal`은 선택 프린터의 광고 RSSI만 기록한다. 연결 중 프린터가 광고를 중단하면 값이 안 잡힐 수 있다. `no_recent_advertisement`는 연결 끊김 판정이 아니다. RSSI를 m로 환산하지 않는다.

```sh
# 프린터 가까이에서 신호가 잡히는지 확인
./ble-signal --duration 30

# 물리적으로 3m를 측정해 배치한 뒤 해당 거리의 신호 기록
./ble-signal --duration 30 --distance-m 3

# 신호 기록을 마친 뒤, 같은 거리에서 예시 영수증 한 장 출력
./ble-test --send --distance-m 3
```

1·3·5·10m에서 반복하고 신호 CSV + 출력 run JSON + 실제 종이 성공/깨짐을 함께 판단한다. CLI 성공만으로 종이 정상 출력이나 최대 거리를 확정하지 않는다. 이 실험에서는 거리별 시험은 아직 수행하지 않았다.

## 참고한 구현 근거

- [PT-210 SDK raster](https://goojprt-pt210-sdk.readthedocs.io/en/latest/api/raster.html): GS v 0 / strips 방식 참고.
- [PT-210 SDK transport](https://goojprt-pt210-sdk.readthedocs.io/en/latest/api/transport.html): chunk·pacing·단일 writer 구조 참고. 예제 값을 그대로 최적값으로 단정하지 않고 실제 시험으로 조정.
- [Epson GS v 0 명령](https://download4.epson.biz/sec_pubs/pos/reference_en/escpos/gs_lv_0.html): 픽셀 인코딩 형식. Epson은 obsolete로 분류하므로 장래 모든 모델 지원을 가정하지 않는다. 이 PT210L에서는 실물 시험으로 확인.
- [Bleak scanner](https://bleak.readthedocs.io/en/latest/api/scanner.html): advertisement callback의 RSSI 수집.
