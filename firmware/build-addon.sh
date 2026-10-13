#!/usr/bin/env bash
# Sestaví firmware pro webový instalátor add-onu ESPink Server.
# Binárka je neutrální (bez IP adresy a hesla AP) – add-on při stažení přepíše
# blok ZOCFG1 (patch 05) hodnotami ze své stránky Instalace.
#   PIO=/cesta/k/pio ./build-addon.sh
set -euo pipefail
cd "$(dirname "$0")"
PIO="${PIO:-pio}"
ENV_NAME="espink_v36_gdey075z08"
ZO_HOST=homeassistant.local ZO_PORT=8099 ZO_AP_PASSWORD=zivyobraz ./prepare.sh
(cd zivyobraz-fw && "$PIO" run -e "$ENV_NAME")
B="zivyobraz-fw/.pio/build/$ENV_NAME"
BOOT_APP0="$HOME/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin"
for addon in ../espink_server ../espink_dev; do
  out="$addon/app/firmware"
  mkdir -p "$out"
  cp "$B/bootloader.bin" "$B/partitions.bin" "$B/firmware.bin" "$out/"
  cp "$BOOT_APP0" "$out/boot_app0.bin"
  {
    echo "{"
    echo "  \"firmware\": \"$(grep -o 'firmware = "[^"]*"' zivyobraz-fw/src/main.cpp | cut -d'"' -f2)\","
    echo "  \"upstream\": \"$(git -C zivyobraz-fw rev-parse --short HEAD)\","
    echo "  \"patches\": [$(ls patches/*.patch | xargs -n1 basename | sed 's/.*/"&"/' | paste -sd,)],"
    echo "  \"built\": \"$(date -Iseconds)\""
    echo "}"
  } > "$out/build.json"
done
echo "Firmware zkopírován do add-onů (app/firmware)"
