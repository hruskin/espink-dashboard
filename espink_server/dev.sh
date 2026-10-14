#!/usr/bin/env bash
# Lokální náhled dashboardu se skutečnými daty z Home Assistantu.
#   ./dev.sh            server s náhledem na http://localhost:8099 (šablona se překreslí hned po uložení)
#   ./dev.sh mock       totéž s ukázkovými daty (bez HA)
#   ./dev.sh once X.png jen vykreslit náhled do souboru
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/.venv"
ENV_FILE="$HERE/.env.dev"

if [ ! -x "$VENV/bin/python" ]; then
  echo "Připravuji Python prostředí ($VENV)…"
  python3 -m venv "$VENV"
  # připnuté verze (odpovídají tomu, s čím je vývoj ověřený)
  "$VENV/bin/pip" install -q "aiohttp==3.14.4" "jinja2==3.1.6" "pillow==12.3.0" "paho-mqtt==2.1.0"
fi

# Typst stejné verze jako v add-onu (Alpine 3.21); statická binárka do .venv, ověřená SHA-256
TYPST_VERSION=0.12.0
declare -A TYPST_SHA=(
  [x86_64]=605130a770ebd59a4a579673079cb913a13e75985231657a71d6239a57539ec3
  [aarch64]=e81ae98e6b12db5a36c2276e5a9890da48f7a339b92476dd22daf90de3699e11
)
if [ -z "${TYPST:-}" ]; then
  if [ ! -x "$VENV/bin/typst" ]; then
    echo "Stahuji Typst $TYPST_VERSION…"
    arch="$(uname -m)"; tmp="$(mktemp)"
    curl -fsSL -o "$tmp" "https://github.com/typst/typst/releases/download/v$TYPST_VERSION/typst-$arch-unknown-linux-musl.tar.xz"
    echo "${TYPST_SHA[$arch]:-chybí}  $tmp" | sha256sum -c --quiet || { echo "Typst: nesouhlasí kontrolní součet" >&2; rm -f "$tmp"; exit 1; }
    tar -xJf "$tmp" -C "$VENV/bin" --strip-components=1 "typst-$arch-unknown-linux-musl/typst"
    rm -f "$tmp"
  fi
  export TYPST="$VENV/bin/typst"
fi

if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'CFG'
# Lokální náhled – NEcommitovat (je v .gitignore)
# Token: HA → Profil → Zabezpečení → Dlouhodobé přístupové tokeny
HA_URL=https://192.168.0.73:8123
HA_TOKEN=
# certifikát HA je na doménu, ne na IP
HA_VERIFY_SSL=0
# MQTT (nepovinné): bez MQTT_HOST se stav panelů zapisuje přes REST API
# MQTT_HOST=192.168.0.73
# MQTT_USER=
# MQTT_PASSWORD=
CFG
  chmod 600 "$ENV_FILE"
  echo "Vytvořen $ENV_FILE – doplň HA_TOKEN a spusť znovu."
  exit 1
fi

chmod 600 "$ENV_FILE"  # obsahuje token k HA
set -a; source "$ENV_FILE"; set +a
export DEV=1
# vlastní kopie konfigurace (stejný formát jako konfigurace add-onu)
export OPTIONS_PATH="${OPTIONS_PATH:-$HERE/dev-options.json}"
[ -f "$OPTIONS_PATH" ] || cp "$HERE/app/options.example.json" "$OPTIONS_PATH"
# rozvržení z editoru (dokud neexistuje, odvodí se z konfigurace)
export LAYOUT_PATH="${LAYOUT_PATH:-$HERE/dev-layout.json}"
# rozvržení a panely (dev-layout.json se při prvním spuštění převezme jako „Hlavní“)
export LAYOUTS_PATH="${LAYOUTS_PATH:-$HERE/dev-layouts.json}"
export PANELS_PATH="${PANELS_PATH:-$HERE/dev-panels.json}"

cd "$HERE/app"
case "${1:-}" in
  mock) export MOCK=1; exec "$VENV/bin/python" -u main.py --host 127.0.0.1 --port "${PORT:-8099}" ;;
  once) exec "$VENV/bin/python" -u main.py --once "${2:-nahled.png}" ;;
  *)    [ -n "${HA_TOKEN:-}" ] || { echo "Chybí HA_TOKEN v $ENV_FILE"; exit 1; }
        echo "Náhled: http://localhost:${PORT:-8099}/   (konfigurace: $OPTIONS_PATH)"
        exec "$VENV/bin/python" -u main.py --host 127.0.0.1 --port "${PORT:-8099}" ;;
esac
