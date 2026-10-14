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
    if lid not in layouts:
        lid = next(iter(layouts))
    sensor = str(raw.get("battery_sensor") or "").strip()
    if sensor and not ENTITY_RE.match(sensor):
        raise ValueError(f"neplatné ID entity: {sensor}")
    rotate = raw.get("rotate")  # None = výchozí z konfigurace add-onu
    if rotate in ("", None):
        rotate = None
    elif int(rotate) not in (90, 270):  # rozvržení je na výšku, displej nativně na šířku
        raise ValueError(f"neplatné otočení: {rotate} (jen 90 nebo 270)")
    return {"mac": mac, "name": name, "layout": lid, "schedule": check_rules(raw.get("schedule")),
            "battery_sensor": sensor, "rotate": None if rotate is None else int(rotate)}


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
