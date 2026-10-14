#!/usr/bin/env bash
# Sestaví firmware pro webový instalátor add-onu ESPink Server – pro ESPink v3.6
# (ESP32-S3) i starší ESPink 2.x (ESP32); instalátor vybere podle připojeného čipu.
# Binárky jsou neutrální (bez IP adresy a hesla AP) – add-on při stažení přepíše
# blok ZOCFG1 (patch 05) hodnotami ze své stránky Instalace.
#   PIO=/cesta/k/pio ./build-addon.sh
set -euo pipefail
cd "$(dirname "$0")"
PIO="${PIO:-pio}"
ZO_HOST=homeassistant.local ZO_PORT=8099 ZO_AP_PASSWORD=zivyobraz ./prepare.sh
BOOT_APP0="$HOME/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin"
# prostředí PlatformIO -> podadresář v app/firmware (= chipFamily v manifestu)
declare -A CHIPS=([espink_v36_gdey075z08]=esp32s3 [espink_v2_gdey075z08]=esp32)
for env in "${!CHIPS[@]}"; do
  (cd zivyobraz-fw && "$PIO" run -e "$env")
done
for addon in ../espink_server ../espink_dev; do
  out="$addon/app/firmware"
  rm -f "$out"/*.bin
  for env in "${!CHIPS[@]}"; do
    B="zivyobraz-fw/.pio/build/$env"
    mkdir -p "$out/${CHIPS[$env]}"
    cp "$B/bootloader.bin" "$B/partitions.bin" "$B/firmware.bin" "$out/${CHIPS[$env]}/"
    cp "$BOOT_APP0" "$out/${CHIPS[$env]}/boot_app0.bin"
  done
  {
    echo "{"
    echo "  \"firmware\": \"$(grep -o 'firmware = "[^"]*"' zivyobraz-fw/src/main.cpp | cut -d'"' -f2)\","
    echo "  \"upstream\": \"$(git -C zivyobraz-fw rev-parse --short HEAD)\","
    echo "  \"patches\": [$(ls patches/*.patch | xargs -n1 basename | sed 's/.*/"&"/' | paste -sd,)],"
    echo "  \"boards\": [\"ESPink v3.6 (ESP32-S3)\", \"ESPink 2.x (ESP32)\"],"
    echo "  \"built\": \"$(date -Iseconds)\""
    echo "}"
  } > "$out/build.json"
done
echo "Firmware zkopírován do add-onů (app/firmware/esp32s3, app/firmware/esp32)"
