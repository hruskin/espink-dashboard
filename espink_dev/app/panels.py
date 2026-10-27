"""Panely (desky ESPink) podle MAC: název, rozvržení, rozvrh probouzení a senzor baterie.

Uloženo v /data/panels.json. Které desky smějí stahovat obrázek, určuje dál
allowed_devices v konfiguraci add-onu – panel bez povolení se jen zobrazí v editoru.
"""
import json
import os
import re
import unicodedata

import layout as layout_mod
from options import ENTITY_RE, check_rules, normalize_mac


def path() -> str:
    return layout_mod._sibling("PANELS_PATH", "panels.json")


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "panel"


def suggest_sensor(name: str) -> str:
    """Kuchyň -> sensor.espink_kuchyn_baterie"""
    return f"sensor.espink_{slug(name)}_baterie"


def default_name(mac: str, taken: set[str]) -> str:
    name = f"ESPink {mac.replace(':', '')[-4:]}"
    return name if name not in taken else mac


COLORS = ("bwr", "bw")

def sanitize(raw: dict, layouts: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("neplatný panel")
    mac = normalize_mac(raw.get("mac"))
    if not mac:
        raise ValueError(f"neplatná MAC adresa: {raw.get('mac')}")
    name = layout_mod.clean_name(raw.get("name"))
    if not name:
        raise ValueError("panel musí mít název")
    lid = raw.get("layout")
    if not isinstance(lid, str) or lid not in layouts:
        lid = next(iter(layouts))
    night = raw.get("night_layout")  # rozvržení pro poslední obraz před nocí; "" = beze změny
    if not isinstance(night, str) or night not in layouts:
        night = ""
    sensor = str(raw.get("battery_sensor") or "").strip()
    if sensor and not (ENTITY_RE.match(sensor) and sensor.startswith("sensor.")):
        raise ValueError(f"senzor baterie musí být entita sensor.…: {sensor}")
    rotate = raw.get("rotate")  # None = výchozí z konfigurace add-onu
    if rotate in ("", None):
        rotate = None
    elif rotate not in (90, 270, "90", "270"):  # rozvržení je na výšku, displej nativně na šířku
        raise ValueError(f"neplatné otočení: {rotate} (jen 90 nebo 270)")
    # barvy displeje: "bwr" = černá/bílá/červená (GDEY075Z08), "bw" = černobílý (GDEY075T7) – červená jako černá
    colors = raw.get("colors") or "bwr"
    if colors not in COLORS:
        raise ValueError(f"neplatné barvy displeje: {colors}")
    return {"mac": mac, "name": name, "layout": lid, "night_layout": night, "schedule": check_rules(raw.get("schedule")),
            "battery_sensor": sensor, "rotate": None if rotate is None else int(rotate), "colors": colors}


def load(opts: dict, layouts: dict) -> list[dict]:
    """Bez panels.json vzniknou panely z povolených zařízení; první dostane
    dosavadní senzor baterie, aby historie v HA pokračovala."""
    try:
        with open(path(), encoding="utf-8") as f:
            items = json.load(f).get("panels", [])
    except FileNotFoundError:
        items = [{"mac": mac, "name": "ESPink" if i == 0 else f"ESPink {i + 1}",
                  "battery_sensor": opts.get("battery_sensor", "") if i == 0 else ""}
                 for i, mac in enumerate(opts.get("allowed_devices") or [])]
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as e:
        print(f"[panels] {path()} je neplatný ({e}); panely se založí znovu podle povolených zařízení")
        items = []
    out = []
    for raw in items:
        try:
            p = sanitize(raw, layouts)
        except ValueError as e:
            print(f"[panels] vynechán panel: {e}")
            continue
        if all(o["mac"] != p["mac"] for o in out):
            out.append(p)
    return out


def save(panels: list[dict]) -> None:
    layout_mod._write(path(), {"panels": panels})


# ── Zabezpečení desek: klíč (X-API-Key) a jednorázové automatické povolení ──
# Firmware posílá v hlavičce X-API-Key náhodný 8místný klíč uložený v desce (nový po
# smazání flash). Server si ho při povolení desky zapamatuje a dál vyžaduje MAC i klíč.
# „tofu_done“: první deska se smí povolit sama jen jednou (ne po odebrání posledního panelu).
KEY_RE = re.compile(r"^[0-9]{4,12}$")


def stats_path() -> str:
    return layout_mod._sibling("STATS_PATH", "stats.json")


def load_stats() -> dict:
    """Počty probuzení a překreslení za den a čas posledního překreslení – {mac: {...}}."""
    try:
        with open(stats_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    out = {}
    for mac, s in (data.items() if isinstance(data, dict) else []):
        if normalize_mac(mac) == mac and isinstance(s, dict):
            text = lambda k: s.get(k) if isinstance(s.get(k), str) else None  # noqa: E731
            num = lambda k: int(s.get(k) or 0) if isinstance(s.get(k), (int, float)) else 0  # noqa: E731
            out[mac] = {"day": str(s.get("day") or ""), "wakes": num("wakes"), "redraws": num("redraws"),
                        "unchanged": num("unchanged"), "missed": num("missed"),
                        "last_redraw": text("last_redraw"), "last_missed": text("last_missed"),
                        # čekané překreslení z minulého probuzení (potvrdí ho až to další)
                        "expect": s.get("expect") if isinstance(s.get("expect"), bool) else None,
                        "expect_at": text("expect_at"), "last_reason": text("last_reason"),
                        "reasons": {str(k)[:80]: int(v) for k, v in (s.get("reasons") or {}).items()
                                    if isinstance(v, int)} if isinstance(s.get("reasons"), dict) else {}}
    return out


def save_stats(data: dict) -> None:
    tmp = stats_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, stats_path())


def security_path() -> str:
    return layout_mod._sibling("SECURITY_PATH", "security.json")


def load_security() -> dict:
    try:
        with open(security_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    out = {"tofu_done": bool(data.get("tofu_done")),
           "keys": {m: k for m, k in keys.items() if normalize_mac(m) == m and KEY_RE.match(str(k))}}
    if "learn_until" in data:   # chybí = soubor ze starší verze (main.py okno pro učení klíče otevře)
        try:
            out["learn_until"] = float(data.get("learn_until") or 0)
        except (TypeError, ValueError):
            out["learn_until"] = 0.0
    return out


def save_security(data: dict) -> None:
    """Zápis rovnou s právy 0600 (klíče desek), atomicky."""
    path = security_path()
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def clean_key(value) -> str:
    v = str(value or "").strip()
    return v if KEY_RE.match(v) else ""
