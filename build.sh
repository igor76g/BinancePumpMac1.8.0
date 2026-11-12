#!/bin/bash
# Local build on a Mac. Requires Python 3.10+ with tkinter (the installer from python.org includes it).
# Usage:  bash build.sh
set -e
cd "$(dirname "$0")"

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

pyinstaller --noconfirm --windowed --name BinancePumpAlerts \
  --osx-bundle-identifier com.example.binancepumpalerts run.py

codesign --force --deep --sign - dist/BinancePumpAlerts.app

rm -rf dmg_src BinancePumpAlerts.dmg
mkdir dmg_src
cp -R dist/BinancePumpAlerts.app dmg_src/
ln -s /Applications dmg_src/Applications
hdiutil create -volname "Binance Pump Alerts" -srcfolder dmg_src -ov -format UDZO BinancePumpAlerts.dmg
rm -rf dmg_src

echo
echo "Done: BinancePumpAlerts.dmg (and dist/BinancePumpAlerts.app)"
