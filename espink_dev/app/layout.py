"""Rozvržení dashboardu: bloky na mřížce (layout.json v /data).

Displej 480 × 800 px je rozdělen na 12 sloupců po 40 px; svisle se poloha a výška
zadávají v px po 4 px. Každý blok má obdélník (x, w ve sloupcích; y, h v px),
čáru nahoře a vlevo a vlastní parametry. Obsah se přizpůsobí svému obdélníku –
agenda spočítá, kolik událostí se vejde, odjezdy kolik spojů atd.
"""
import copy
import json
import os
import re
import time
import uuid

from options import ENTITY_RE

SCREEN_W, SCREEN_H = 480, 800
COLS, COL_W, ROW_STEP = 12, 40, 4
VERSION = 2
# entity zmíněné v šabloně v uvozovkách: states('sensor.x')
TEMPLATE_ENTITY_RE = re.compile(r"""['"]([a-z_]+\.[a-z0-9_]+)['"]""")
LINES = {"none": 0, "thin": 1, "thick": 2}

WEATHER_PARAMS = {
    "weather": ("", "entity"),
    "temperature": ("", "entity"),
    "humidity": ("", "entity"),
    "rain": ("", "entity"),
    "pressure": ("", "entity"),
    "indoor": ("", "entity"),
}

# Parametry: typ -> (výchozí hodnota, validátor)
#   "entity" = ID entity nebo prázdné, ("int", lo, hi), "bool", ("enum", …), "calendars",
#   "text" (krátký), "longtext", "number" (číslo nebo prázdné)
# size = výchozí velikost při přidání (sloupce, px), min = nejmenší rozumná velikost
BLOCK_TYPES: dict[str, dict] = {
    "header": {
        "title": "Záhlaví", "size": (12, 136), "min": (6, 96),
        "params": {
            "nameday_calendar": ("", "entity"),
            "holiday_calendar": ("", "entity"),
            "alert": (True, "bool"),          # červené kalendáře z agendy: dnes/zítra
            "show_weather": (True, "bool"),   # aktuální počasí vpravo v záhlaví
            **WEATHER_PARAMS,
        },
    },
    "weather_now": {
        "title": "Počasí teď", "size": (5, 116), "min": (3, 60),
        "params": dict(WEATHER_PARAMS),
    },
    "departures": {
        "title": "Odjezdy", "size": (12, 188), "min": (6, 68),
        "params": {
            "entity": ("", "entity"),
            "disruptions": ("", "entity"),
            "walk_minutes": (0, ("int", 0, 30)),
        },
    },
    "agenda": {
        "title": "Události", "size": (12, 240), "min": (5, 72),
        "params": {
            "calendars": ([], "calendars"),
            "holiday_calendar": ("", "entity"),  # státní svátky jako červený řádek
            "days": (7, ("int", 1, 31)),
        },
    },
    "forecast": {
        "title": "Předpověď", "size": (12, 104), "min": (3, 80),
        "params": {
            "weather": ("", "entity"),
            "days": (4, ("int", 1, 6)),
        },
    },
    "footer": {
        "title": "Patička", "size": (12, 24), "min": (4, 20),
        "params": {
            "updated": (True, "bool"),
            "next": (True, "bool"),
            "week": (True, "bool"),
            "battery": (True, "bool"),
            "items": ([], "items"),         # libovolné entity: [{entity, attribute, icon, label, side, decimals}]
        },
    },
    "value": {
        "title": "Hodnota entity", "size": (6, 56), "min": (2, 28),
        "params": {
            "entity": ("", "entity"),
            "show_label": (True, "bool"),
            "attribute": ("", "text"),      # prázdné = stav entity
            "label": ("", "text"),          # prázdné = název entity v HA
            "icon": ("", "text"),           # mdi:… ; prázdné = ikona entity v HA
            "decimals": (1, ("int", 0, 3)),
            "red_below": ("", "number"),    # červeně, když je hodnota pod/nad mezí
            "red_above": ("", "number"),
        },
    },
    "text": {
        "title": "Text", "size": (6, 40), "min": (1, 20),
        "params": {
            "text": ("", "longtext"),       # řádky oddělené Enterem
            "size": ("m", ("enum", "s", "m", "l")),
            "align": ("left", ("enum", "left", "center")),
            "bold": (False, "bool"),
            "red": (False, "bool"),
        },
    },
    "template": {
        "title": "Šablona", "size": (6, 48), "min": (1, 8),
        "params": {
            "code": ("", "longtext"),       # Jinja (sandbox) + HTML; states(), state_attr(), is_state()
        },
    },
}

# Rozměry obsahu v px – musí sedět s CSS v šabloně
HEADER_H = 136
DEPS_BASE_H, DEP_ROW_H = 36, 30
AGENDA_PAD = 6
TEXT_LINE_H = {"s": 24, "m": 32, "l": 52}
# v1 (bloky pod sebou) – jen pro převod starších rozvržení
_V1_H = {"header": 136, "forecast": 104, "footer": 23, "value": {"s": 40, "m": 56, "l": 88}}


def _check(value, kind, default):
    if kind == "entity":
        v = str(value or "").strip()
        if v and not ENTITY_RE.match(v):
            raise ValueError(f"neplatné ID entity: {v}")
        return v
    if kind == "bool":
        return bool(value)
    if kind == "text":
        return str(value or "").strip()[:60]
    if kind == "longtext":
        v = str(value or "")
        if len(v) > 4000:
            raise ValueError("text je delší než 4000 znaků")
        return v.replace("\r\n", "\n")
    if kind == "number":
        v = str(value if value is not None else "").strip().replace(",", ".")
        if v:
            float(v)  # ValueError = neplatné číslo
        return v
    if kind == "calendars":
        out = []
        for c in value or []:
            if not isinstance(c, dict) or not str(c.get("entity", "")).strip():
                continue
            item = {"entity": _check(c["entity"], "entity", "")}
            for k in ("label", "icon"):
                if str(c.get(k) or "").strip():
                    item[k] = str(c[k]).strip()[:40]
            if c.get("red"):
                item["red"] = True
            out.append(item)
        return out
    if kind == "items":
        out = []
        for it in (value or [])[:12]:
            if not isinstance(it, dict) or not str(it.get("entity", "")).strip():
                continue
            item = {"entity": _check(it["entity"], "entity", ""), "side": "right" if it.get("side") == "right" else "left",
                    "decimals": _check(it.get("decimals", 1), ("int", 0, 3), 1)}
            for k in ("attribute", "icon", "label"):
                if str(it.get(k) or "").strip():
                    item[k] = str(it[k]).strip()[:40]
            out.append(item)
        return out
    if isinstance(kind, tuple) and kind[0] == "enum":
        if value not in kind[1:]:
            raise ValueError(f"neplatná volba: {value}")
        return value
    if isinstance(kind, tuple) and kind[0] == "int":
        v = int(value)
        if not kind[1] <= v <= kind[2]:
            raise ValueError(f"hodnota {v} mimo rozsah {kind[1]}–{kind[2]}")
        return v
    return default


def _snap(v, step) -> int:
    return int(round(int(v) / step) * step)


def rect_px(b: dict) -> tuple[int, int, int, int]:
    """(left, top, width, height) v px."""
    return b["x"] * COL_W, b["y"], b["w"] * COL_W, b["h"]


def overlaps(a: dict, b: dict) -> bool:
    return (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
            and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


def _migrate_v1(layout: dict) -> dict:
    """Bloky pod sebou (v1) -> mřížka: plná šířka, agenda dostane zbylé místo."""
    blocks = [dict(b) for b in layout["blocks"]]
    shown = [b for b in blocks if not b.get("hidden")]
    heights = {}
    for i, b in enumerate(shown):
        line = 0 if i == 0 else LINES.get(b.get("line", "none"), 0)
        t = b.get("type")
        if t == "departures":
            h = DEPS_BASE_H + int(b.get("count", 5)) * DEP_ROW_H
        elif t == "value":
            h = _V1_H["value"].get(b.get("size", "m"), 56)
        elif t == "text":
            h = 8 + int(b.get("lines", 1)) * TEXT_LINE_H.get(b.get("size", "m"), 32)
        elif t == "template":
            h = int(b.get("height", 48))
        else:
            h = _V1_H.get(t)
        heights[id(b)] = (h, line)
    fixed = sum((h or 0) + line for h, line in heights.values())
    y = 0
    for b in blocks:
        h, line = heights.get(id(b), (None, 0))
        if h is None and id(b) in heights:  # agenda
            h = max(72, SCREEN_H - fixed)  # fixed už obsahuje i čáru agendy
        if id(b) not in heights:  # skrytý blok – kamkoli, nevykresluje se
            h = BLOCK_TYPES.get(b.get("type"), {}).get("size", (12, 40))[1]
        total = _snap(h + line, ROW_STEP)
        b.update(x=0, w=COLS, y=min(y, SCREEN_H - total), h=total, line_top=b.get("line", "none"), line_left="none")
        if id(b) in heights:
            y += total
    return {"blocks": blocks}


def sanitize(layout: dict) -> dict:
    """Převezme jen známé bloky a parametry se správnými typy; hlídá mřížku a překryvy."""
    if not isinstance(layout, dict) or not isinstance(layout.get("blocks"), list):
        raise ValueError("rozvržení musí obsahovat seznam bloků")
    rev = int(layout.get("rev") or 0)
    if any(isinstance(b, dict) and "h" not in b for b in layout["blocks"]):
        layout = _migrate_v1(layout)
    blocks = []
    for raw in layout["blocks"]:
        if not isinstance(raw, dict) or raw.get("type") not in BLOCK_TYPES:
            raise ValueError(f"neznámý typ bloku: {raw.get('type') if isinstance(raw, dict) else raw!r}")
        spec = BLOCK_TYPES[raw["type"]]
        b = {"type": raw["type"], "id": str(raw.get("id") or uuid.uuid4().hex[:8])[:16],
             "hidden": bool(raw.get("hidden"))}
        try:
            x, w = int(raw.get("x", 0)), int(raw.get("w", spec["size"][0]))
            y, h = _snap(raw.get("y", 0), ROW_STEP), _snap(raw.get("h", spec["size"][1]), ROW_STEP)
        except (TypeError, ValueError):
            raise ValueError(f"blok {spec['title']}: neplatná poloha nebo velikost")
        if not (0 <= x < COLS and 1 <= w <= COLS - x and 0 <= y < SCREEN_H and ROW_STEP <= h <= SCREEN_H - y):
            raise ValueError(f"blok {spec['title']} je mimo displej")
        b.update(x=x, w=w, y=y, h=h)
        for side in ("line_top", "line_left"):
            b[side] = raw.get(side) if raw.get(side) in LINES else "none"
        for key, (default, kind) in spec["params"].items():
            b[key] = _check(raw[key], kind, default) if key in raw else copy.deepcopy(default)
        blocks.append(b)
    if sum(1 for b in blocks if b["type"] == "agenda") > 1:
        raise ValueError("blok Události může být jen jeden")
    shown = [b for b in blocks if not b["hidden"]]
    for i, a in enumerate(shown):
        for b in shown[i + 1:]:
            if overlaps(a, b):
                raise ValueError(f"bloky {BLOCK_TYPES[a['type']]['title']} a {BLOCK_TYPES[b['type']]['title']} se překrývají")
    return {"version": VERSION, "rev": rev, "blocks": blocks}


def visible(layout: dict) -> list[dict]:
    return [b for b in layout["blocks"] if not b.get("hidden")]


def from_options(opts: dict) -> dict:
    """Výchozí rozvržení z dosavadní konfigurace add-onu (jako stabilní verze)."""
    blocks = [
        {"type": "header", "nameday_calendar": opts.get("nameday_calendar", ""),
         "holiday_calendar": opts.get("holiday_calendar", ""), "alert": True, "show_weather": True,
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
    return sanitize({"blocks": blocks})  # v1 tvar -> převod na mřížku


def path() -> str:
    return os.environ.get("LAYOUT_PATH", "/data/layout.json")


def load(opts: dict) -> tuple[dict, bool, str]:
    """Vrátí (rozvržení, uložené?, chyba). Bez layout.json se odvodí z konfigurace."""
    try:
        with open(path(), encoding="utf-8") as f:
            return sanitize(json.load(f)), True, ""
    except FileNotFoundError:
        pass
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        print(f"[layout] {path()} je neplatný ({e}); použito výchozí rozvržení")
        return from_options(opts), False, str(e)
    return from_options(opts), False, ""


HISTORY_MAX = 15


def history_path() -> str:
    return os.path.join(os.path.dirname(path()) or ".", "layout-history.json")


def history() -> list[dict]:
    try:
        with open(history_path(), encoding="utf-8") as f:
            items = json.load(f)
        return items if isinstance(items, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write(file: str, data) -> None:
    tmp = file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, file)


def save(layout: dict, previous: dict | None) -> dict:
    """Uloží rozvržení s novou revizí; předchozí uloženou verzi přidá do historie."""
    new = dict(layout, rev=int(time.time() * 1000))
    if previous:
        items = [{"rev": previous.get("rev", 0), "layout": previous}] + history()
        _write(history_path(), items[:HISTORY_MAX])
    _write(path(), new)
    return new


def needs(layout: dict) -> dict:
    """Co je potřeba stáhnout z HA: stavy entit, kalendáře, předpovědi."""
    states, cals, forecasts = [], [], []

    def add(lst, v):
        if v and v not in lst:
            lst.append(v)

    shown = visible(layout)
    for b in shown:
        t = b["type"]
        if t in ("header", "weather_now"):
            if t == "weather_now" or b["show_weather"]:
                for k in WEATHER_PARAMS:
                    add(states, b[k])
                add(forecasts, b["weather"])
            if t == "header":
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
        elif t == "value":
            add(states, b["entity"])
        elif t == "footer":
            for it in b["items"]:
                add(states, it["entity"])
        elif t == "template":
            for e in TEMPLATE_ENTITY_RE.findall(b["code"]):
                add(states, e)
    if any(b["type"] == "header" and b["alert"] for b in shown):
        for b in layout["blocks"]:  # upozornění bere červené kalendáře i ze skryté agendy
            if b["type"] == "agenda":
                for c in b["calendars"]:
                    if c.get("red"):
                        add(cals, c["entity"])
    return {"states": states, "calendars": cals, "forecasts": forecasts}


def schema() -> dict:
    """Popis typů bloků a mřížky pro editor (aby JS nekopíroval konstanty)."""
    return {
        "types": {t: {"title": s["title"], "size": s["size"], "min": s["min"],
                      "params": {k: {"default": d, "kind": list(kind) if isinstance(kind, tuple) else kind}
                                 for k, (d, kind) in s["params"].items()}}
                  for t, s in BLOCK_TYPES.items()},
        "grid": {"w": SCREEN_W, "h": SCREEN_H, "cols": COLS, "col_w": COL_W, "step": ROW_STEP, "lines": LINES,
                 "text_line": TEXT_LINE_H, "text_pad": 8},
    }
