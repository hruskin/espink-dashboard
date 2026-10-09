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
  "$VENV/bin/pip" install -q aiohttp jinja2 pillow
fi

if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'CFG'
# Lokální náhled – NEcommitovat (je v .gitignore)
# Token: HA → Profil → Zabezpečení → Dlouhodobé přístupové tokeny
HA_URL=https://192.168.0.73:8123
HA_TOKEN=
# certifikát HA je na doménu, ne na IP
HA_VERIFY_SSL=0
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

cd "$HERE/app"
case "${1:-}" in
  mock) export MOCK=1; exec "$VENV/bin/python" -u main.py --host 127.0.0.1 --port "${PORT:-8098}" ;;
  once) exec "$VENV/bin/python" -u main.py --once "${2:-nahled.png}" ;;
  *)    [ -n "${HA_TOKEN:-}" ] || { echo "Chybí HA_TOKEN v $ENV_FILE"; exit 1; }
        echo "Náhled: http://localhost:${PORT:-8098}/   (konfigurace: $OPTIONS_PATH)"
        exec "$VENV/bin/python" -u main.py --host 127.0.0.1 --port "${PORT:-8098}" ;;
esac
