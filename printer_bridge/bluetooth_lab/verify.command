#!/bin/sh
set -eu
LAB=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$LAB/../.." && pwd)
cd "$ROOT"
"$LAB/.venv/bin/python" -m unittest printer_bridge.bluetooth_lab.test_lab printer_bridge.bluetooth.test_probe
"$LAB/ble-test" --dry-run
"$LAB/spp-test" --dry-run
"$LAB/build/PT210SPP.app/Contents/MacOS/pt210-spp" --dry-run --file "$LAB/samples/example-receipt.escpos"
