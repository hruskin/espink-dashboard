"""Rozvržení dashboardu: svislý seznam bloků (layout.json v /data).

Každý blok má typ, vlastní parametry a čáru nad sebou. Výšky bloků jsou pevné
(nebo spočítané z parametrů), jediný blok „agenda“ vyplní zbývající místo –
model podle toho spočítá, kolik událostí se vejde.
"""
import copy
import json
import os
import uuid

from options import ENTITY_RE

SCREEN_H = 800
LINES = {"none": 0, "thin": 1, "thick": 2}

# Parametry: typ -> (výchozí hodnota, validátor)
#   "entity" = ID entity nebo prázdné, ("int", lo, hi), "bool", ("enum", …), "calendars"
BLOCK_TYPES: dict[str, dict] = {
    "header": {
        "title": "Záhlaví",
        "params": {
            "nameday_calendar": ("", "entity"),
            "holiday_calendar": ("", "entity"),
            "alert": (True, "bool"),          # červené kalendáře z agendy: dnes/zítra
            "weather": ("", "entity"),
            "temperature": ("", "entity"),
            "humidity": ("", "entity"),
            "rain": ("", "entity"),
            "pressure": ("", "entity"),
            "indoor": ("", "entity"),
        },
    },
    "departures": {
        "title": "Odjezdy",
        "params": {
            "entity": ("", "entity"),
            "disruptions": ("", "entity"),
            "count": (5, ("int", 1, 10)),
            "walk_minutes": (0, ("int", 0, 30)),
        },
    },
    "agenda": {
        "title": "Události",
        "params": {
            "calendars": ([], "calendars"),
            "holiday_calendar": ("", "entity"),  # státní svátky jako červený řádek
            "days": (7, ("int", 1, 31)),
        },
    },
    "forecast": {
        "title": "Předpověď",
        "params": {
            "weather": ("", "entity"),
            "days": (4, ("int", 2, 6)),
        },
    },
    "footer": {
        "title": "Patička",
        "params": {
            "updated": (True, "bool"),
            "next": (True, "bool"),
            "week": (True, "bool"),
            "battery": (True, "bool"),
        },
    },
}

# Výšky obsahu bloků v px (bez čáry) – musí sedět s CSS v šabloně
HEADER_H, HEADER_COMPACT_H = 136, 114
FORECAST_H, FOOTER_H = 104, 23
DEPS_BASE_H, DEP_ROW_H = 36, 30


def fixed_height(block: dict) -> int | None:
    """Nejvyšší možná výška obsahu bloku; None = vyplňuje zbytek (agenda)."""
    t = block["type"]
    if t == "header":
        return HEADER_H
    if t == "departures":
        return DEPS_BASE_H + block["count"] * DEP_ROW_H
    if t == "forecast":
        return FORECAST_H
    if t == "footer":
        return FOOTER_H
    return None


def line_px(block: dict, index: int) -> int:
    return 0 if index == 0 else LINES[block.get("line", "none")]


def _check(value, kind, default):
    if kind == "entity":
        v = str(value or "").strip()
        if v and not ENTITY_RE.match(v):
            raise ValueError(f"neplatné ID entity: {v}")
        return v
    if kind == "bool":
        return bool(value)
    if kind == "calendars":
        out = []
        for c in value or []:
            if not isinstance(c, dict) or not str(c.get("entity", "")).strip():
                continue
            ent = _check(c["entity"], "entity", "")
            item = {"entity": ent}
            for k in ("label", "icon"):
                if str(c.get(k) or "").strip():
                    item[k] = str(c[k]).strip()[:40]
            if c.get("red"):
                item["red"] = True
            out.append(item)
        return out
    if isinstance(kind, tuple) and kind[0] == "int":
        v = int(value)
        if not kind[1] <= v <= kind[2]:
            raise ValueError(f"hodnota {v} mimo rozsah {kind[1]}–{kind[2]}")
        return v
    return default


def sanitize(layout: dict) -> dict:
    """Převezme jen známé bloky a parametry se správnými typy."""
    if not isinstance(layout, dict) or not isinstance(layout.get("blocks"), list):
        raise ValueError("rozvržení musí obsahovat seznam bloků")
    blocks = []
    for raw in layout["blocks"]:
        if not isinstance(raw, dict) or raw.get("type") not in BLOCK_TYPES:
            raise ValueError(f"neznámý typ bloku: {raw.get('type') if isinstance(raw, dict) else raw!r}")
        spec = BLOCK_TYPES[raw["type"]]
        b = {"type": raw["type"], "id": str(raw.get("id") or uuid.uuid4().hex[:8])[:16],
             "line": raw.get("line") if raw.get("line") in LINES else "none"}
        for key, (default, kind) in spec["params"].items():
            b[key] = _check(raw[key], kind, default) if key in raw else copy.deepcopy(default)
        blocks.append(b)
    if sum(1 for b in blocks if b["type"] == "agenda") > 1:
        raise ValueError("blok Události může být jen jeden (vyplňuje zbývající místo)")
    used = sum((fixed_height(b) or 0) + line_px(b, i) for i, b in enumerate(blocks))
    if used > SCREEN_H:
        raise ValueError(f"bloky se nevejdou na displej ({used} px z {SCREEN_H} px)")
    return {"version": 1, "blocks": blocks}


def from_options(opts: dict) -> dict:
    """Výchozí rozvržení z dosavadní konfigurace add-onu (vypadá stejně jako dřív)."""
    blocks = [
        {"type": "header", "nameday_calendar": opts.get("nameday_calendar", ""),
         "holiday_calendar": opts.get("holiday_calendar", ""), "alert": True,
         "weather": opts.get("weather", ""), "temperature": opts.get("meteo_temperature", ""),
         "humidity": opts.get("meteo_humidity", ""), "rain": opts.get("meteo_rain_today", ""),
         "pressure": opts.get("meteo_pressure", ""), "indoor": opts.get("indoor_temperature", "")},
    ]
    if opts.get("departures") and opts.get("departures_count", 0) > 0:
        blocks.append({"type": "departures", "line": "thick", "entity": opts["departures"],
                       "disruptions": opts.get("disruptions", ""), "count": opts["departures_count"],
                       "walk_minutes": opts.get("walk_minutes", 0)})
    blocks.append({"type": "agenda", "line": "thick", "calendars": opts.get("calendars", []),
                   "holiday_calendar": opts.get("holiday_calendar", ""), "days": opts.get("event_days", 7)})
    if opts.get("weather"):
        blocks.append({"type": "forecast", "weather": opts["weather"]})
    blocks.append({"type": "footer", "line": "thin"})
    for i, b in enumerate(blocks):
        b["id"] = f"{b['type']}{i}"
    return sanitize({"blocks": blocks})


def path() -> str:
    return os.environ.get("LAYOUT_PATH", "/data/layout.json")


def load(opts: dict) -> tuple[dict, bool]:
    """Vrátí (rozvržení, uložené?). Bez layout.json se odvodí z konfigurace."""
    try:
        with open(path(), encoding="utf-8") as f:
            return sanitize(json.load(f)), True
    except FileNotFoundError:
        pass
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[layout] {path()} je neplatný ({e}); použito výchozí rozvržení")
    return from_options(opts), False


def save(layout: dict) -> None:
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path())


def needs(layout: dict) -> dict:
    """Co je potřeba stáhnout z HA: stavy entit, kalendáře, předpovědi."""
    states, cals, forecasts = [], [], []

    def add(lst, v):
        if v and v not in lst:
            lst.append(v)

    for b in layout["blocks"]:
        t = b["type"]
        if t == "header":
            for k in ("weather", "temperature", "humidity", "rain", "pressure", "indoor"):
                add(states, b[k])
            add(forecasts, b["weather"])
            add(cals, b["nameday_calendar"])
            add(cals, b["holiday_calendar"])
        elif t == "departures":
            add(states, b["entity"])
            add(states, b["disruptions"])
        elif t == "agenda":
            for c in b["calendars"]:
                add(states, c["entity"])  # kvůli atributu icon
                add(cals, c["entity"])
            add(cals, b["holiday_calendar"])
        elif t == "forecast":
            add(forecasts, b["weather"])
    if any(b["type"] == "header" and b["alert"] for b in layout["blocks"]):
        for b in layout["blocks"]:
            if b["type"] == "agenda":
                for c in b["calendars"]:
                    if c.get("red"):
                        add(cals, c["entity"])
    return {"states": states, "calendars": cals, "forecasts": forecasts}


def schema() -> dict:
    """Popis typů bloků pro editor."""
    return {t: {"title": s["title"], "fill": t == "agenda",
                "params": {k: {"default": d, "kind": list(kind) if isinstance(kind, tuple) else kind}
                           for k, (d, kind) in s["params"].items()}}
            for t, s in BLOCK_TYPES.items()}
