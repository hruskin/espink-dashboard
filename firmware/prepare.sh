#!/usr/bin/env bash
# Připraví firmware Živý obraz pro ESPink v3.6 + GDEY075Z08 s lokálním serverem.
#  1. naklonuje / aktualizuje upstream zivyobraz-fw na připnutý commit
#  2. aplikuje patche z ./patches (konfigurovatelný host/port serveru)
#  3. přidá PlatformIO prostředí espink_v36_gdey075z08 s hodnotami z config.env
#
# Hodnoty z config.env lze přebít proměnnými prostředí:
#   ZO_HOST=192.168.1.20 ./prepare.sh
set -euo pipefail

cd "$(dirname "$0")"

UPSTREAM_URL="https://github.com/MultiTricker/zivyobraz-fw.git"
# Připnutý upstream commit (FW 3.2, 2026-08-14). Při aktualizaci změň a ověř, že patche sedí.
UPSTREAM_COMMIT="3d7a5a518df8deec32d5f53bd784c099857deeb8"
SRC_DIR="zivyobraz-fw"
ENV_NAME="espink_v36_gdey075z08"

# Konfigurace: config.env, ale proměnné prostředí mají přednost
_env_host="${ZO_HOST:-}"
_env_port="${ZO_PORT:-}"
# shellcheck source=config.env
source ./config.env
ZO_HOST="${_env_host:-$ZO_HOST}"
ZO_PORT="${_env_port:-$ZO_PORT}"

if [[ -z "$ZO_HOST" || ! "$ZO_PORT" =~ ^[0-9]+$ ]]; then
  echo "Chybí nebo je neplatné ZO_HOST/ZO_PORT (config.env)" >&2
  exit 1
fi

if [[ ! -d "$SRC_DIR/.git" ]]; then
  git clone --quiet "$UPSTREAM_URL" "$SRC_DIR"
fi

# Vždy začni z čistého připnutého stavu (zahodí lokální úpravy ve $SRC_DIR!)
git -C "$SRC_DIR" fetch --quiet origin "$UPSTREAM_COMMIT" 2>/dev/null || git -C "$SRC_DIR" fetch --quiet origin
git -C "$SRC_DIR" checkout --quiet --force "$UPSTREAM_COMMIT"
git -C "$SRC_DIR" clean --quiet -fd -e .pio

for p in patches/*.patch; do
  git -C "$SRC_DIR" apply --whitespace=nowarn "../$p"
  echo "Patch: $p"
done

cat >> "$SRC_DIR/platformio.ini" <<INI

# ---------------------------------------------------------------------------
# Přidáno ../prepare.sh – LaskaKit ESPink v3.6 (ESP32-S3-WROOM-1-N16R8)
# + Good Display GDEY075Z08 (7.5" 800x480 černá/bílá/červená), lokální server
# ---------------------------------------------------------------------------
[env:${ENV_NAME}]
board = esp32-s3-devkitc-1
board_upload.flash_size = 16MB
board_build.partitions = default_16MB.csv
board_build.arduino.memory_type = qio_opi
build_flags =
    -D BOARD_TYPE=ESPink_V35
    -D DISPLAY_TYPE=GDEY075Z08
    -D COLOR_TYPE=3C
    -D USE_CLIENT_HTTP
    -D ZO_HOST=\\"${ZO_HOST}\\"
    -D ZO_PORT=${ZO_PORT}
    -D BOARD_HAS_PSRAM
    -D ARDUINO_USB_MODE=1
    -D ARDUINO_USB_CDC_ON_BOOT=1
lib_deps =
    \${common.lib_deps_builtin}
    \${common.lib_deps}
lib_ignore = \${common.lib_ignore}
INI

echo "Hotovo: server http://${ZO_HOST}:${ZO_PORT}, prostředí ${ENV_NAME}"
echo "Sestavení:  cd ${SRC_DIR} && pio run -e ${ENV_NAME}"
echo "Nahrání:    cd ${SRC_DIR} && pio run -e ${ENV_NAME} -t upload"
