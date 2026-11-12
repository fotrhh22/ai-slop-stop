#!/bin/sh
set -eu
LAB=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=${PT210_PYTHON:-/opt/homebrew/bin/python3.11}
[ -x "$PYTHON" ] || { echo 'Set PT210_PYTHON to Python 3.11+' >&2; exit 1; }
"$PYTHON" -m venv "$LAB/.venv"
"$LAB/.venv/bin/python" -m pip install -r "$LAB/requirements-macos.lock.txt"
APP="$LAB/build/PT210SPP.app"
mkdir -p "$APP/Contents/MacOS"
cp "$LAB/native/Info.plist" "$APP/Contents/Info.plist"
xcrun clang -fobjc-arc -framework Foundation -framework IOBluetooth \
  -Wl,-sectcreate,__TEXT,__info_plist,"$LAB/native/Info.plist" \
  "$LAB/native/spp.m" -o "$APP/Contents/MacOS/pt210-spp"
codesign --force --sign - "$APP"
printf '\nReady. Offline tests:\n  ./ble-test --dry-run\n  ./spp-test --dry-run\n'
