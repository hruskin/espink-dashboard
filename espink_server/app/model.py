"""Převod surových dat z HA na model zobrazení.

Model obsahuje jen hotové řetězce/příznaky, které se opravdu vykreslí. Jeho hash
proto přesně odpovídá tomu, zda se obrázek změnil (=> hlavička Timestamp).
"""
import functools
import hashlib
import math
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2.sandbox import SandboxedEnvironment, SecurityError
from markupsafe import Markup
from PIL import ImageFont

import emoji
import layout as layout_mod

DAYS = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
DAYS_SHORT = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
MONTHS_GEN = ["ledna", "února", "března", "dubna", "května", "června",
              "července", "srpna", "září", "října", "listopadu", "prosince"]

WEATHER_ICONS = {
    "clear-night": "weather-night",
    "cloudy": "weather-cloudy",
    "exceptional": "alert-circle-outline",
    "fog": "weather-fog",
    "hail": "weather-hail",
    "lightning": "weather-lightning",
    "lightning-rainy": "weather-lightning-rainy",
    "partlycloudy": "weather-partly-cloudy",
    "pouring": "weather-pouring",
    "rainy": "weather-rainy",
    "snowy": "weather-snowy",
    "snowy-rainy": "weather-snowy-rainy",
    "sunny": "weather-sunny",
    "windy": "weather-windy",
    "windy-variant": "weather-windy-variant",
}

# Vybíjecí křivka Li-Ion článku (napětí naprázdno -> %)
LIPO_CURVE = [(4.20, 100), (4.15, 95), (4.11, 90), (4.08, 85), (4.02, 80), (3.98, 75),
              (3.95, 70), (3.91, 65), (3.87, 60), (3.85, 55), (3.84, 50), (3.82, 45),
              (3.80, 40), (3.79, 35), (3.77, 30), (3.75, 25), (3.73, 20), (3.71, 15),
              (3.69, 10), (3.61, 5), (3.27, 0)]

# Výšky řádků agendy v px – musí sedět se šablonou dashboard.typ (výšky bloků viz layout.py)
EVENTS_PAD = 6
EVENT_ROW_H = 29
MORE_ROW_H = 22
SEP_H = 1  # tenká čára mezi dny v agendě
WRAP_MAX_LINES = 8  # „celý text“: nejvýš tolik řádků, delší název končí výpustkou
FONTS = Path(__file__).resolve().parent / "static" / "fonts" / "typst"
# Události se načítají až tak daleko dopředu; za hranicí event_days se přidávají
# jen celé dny, které se ještě vejdou (aby sekce nezůstala prázdná)
LOOKAHEAD_DAYS = 31


def battery_percent(volts: float | None) -> int | None:
    if volts is None or volts <= 0:
        return None
    if volts >= LIPO_CURVE[0][0]:
        return 100
    for (v_hi, p_hi), (v_lo, p_lo) in zip(LIPO_CURVE, LIPO_CURVE[1:]):
        if v_lo <= volts <= v_hi:
            return round(p_lo + (p_hi - p_lo) * (volts - v_lo) / (v_hi - v_lo))
    return 0


def num(value, decimals=1) -> str | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    s = f"{v:.{decimals}f}"
    if float(s) == 0:  # −0,3 zaokrouhlené na celé = „0“, ne „−0“
        s = s.lstrip("-")
    return s.replace(".", ",").replace("-", "−")


# Hystereze zaokrouhlených hodnot: zobrazená hodnota se změní, až se skutečná vzdálí
# o ¾ kroku (u celých stupňů: z 11 na 12 až od 11,75, zpět pod 11,25) – hodnota na
# hranici zaokrouhlení pak nepřeskakuje a nevynucuje překreslení. Paměť (co je na
# displeji) má každý panel svou; během build() je v _memory, náhled ji nepoužívá.
# Teplota s desetinami by se při každé desetině měnila (a panel překresloval) skoro při
# každém probuzení – nová hodnota se proto ukáže až při změně aspoň o 0,5 °C.
HYSTERESIS = 0.75
TEMP_MIN_CHANGE = 0.5
TEMP_UNITS = ("°C", "°F", "K")
_memory: dict | None = None


def hnum(key: str, value, decimals: int = 1, min_change: float = 0.0) -> str | None:
    v = fnum(value)
    if v is None:
        return None
    if _memory is not None and key:
        k = f"{key}|{decimals}"
        prev = _memory.get(k)
        limit = max(HYSTERESIS * 10 ** -decimals, min_change)
        v = prev if prev is not None and abs(v - prev) < limit else round(v, decimals)
        _memory[k] = v
    return num(v, decimals)


def fnum(value) -> float | None:
    """Číslo, nebo None – i pro NaN/inf (jinak by rozbily JSON pro UI a HA)."""
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def parse_dt(value: str, tz) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def short_day_label(d: date, today: date) -> str:
    """Popisek dne do sloupce agendy: „Dnes“, „Zítra“, jinak „So 18.“."""
    if d == today:
        return "Dnes"
    if d == today + timedelta(days=1):
        return "Zítra"
    return f"{DAYS_SHORT[d.weekday()]} {d.day}."


def day_label(d: date, today: date) -> str:
    if d == today:
        return "Dnes"
    if d == today + timedelta(days=1):
        return "Zítra"
    return DAYS[d.weekday()].capitalize()


# ---------------------------------------------------------------------------

EVENING_HOUR = 18  # od této hodiny ukazovat v hlavičce min/max na zítřek


def _weather(raw: dict, b: dict, now: datetime, tz) -> dict:
    """Aktuální počasí do záhlaví: vlastní meteostanice, jinak entita počasí."""
    today = now.date()
    minmax_day = today + timedelta(days=1) if now.hour >= EVENING_HOUR else today
    states = raw["states"]
    st = lambda key: states.get(b.get(key) or "", {}).get("state")  # noqa: E731
    w = states.get(b.get("weather") or "")
    cond = w["state"] if w else None

    temp = st("temperature")
    if fnum(temp) is None and w:
        temp = w["attributes"].get("temperature")
    hum = st("humidity")
    if fnum(hum) is None and w:
        hum = w["attributes"].get("humidity")
    rain = fnum(st("rain"))

    out = {
        "icon": WEATHER_ICONS.get(cond or "", "weather-cloudy-alert"),
        "temp": hnum(f"temp:{b.get('temperature') or b.get('weather')}", temp, b.get("temp_decimals", 1), TEMP_MIN_CHANGE),
        "humidity": hnum(f"hum:{b.get('humidity') or b.get('weather')}", hum, 0),
        "rain": num(rain) if rain and rain >= 0.1 else None,
        "pressure": hnum(f"press:{b.get('pressure')}", st("pressure"), 0),
        "indoor": hnum(f"indoor:{b.get('indoor')}", st("indoor"), b.get("temp_decimals", 1), TEMP_MIN_CHANGE),
        "min": None,
        "max": None,
        "minmax_label": "zítra" if minmax_day != today else "",
    }
    for f in raw["forecasts"].get(b.get("weather") or "", []):
        dt = parse_dt(f.get("datetime", ""), tz)
        if dt and dt.date() == minmax_day:
            out["min"] = num(f.get("templow"), 0)
            out["max"] = num(f.get("temperature"), 0)
    return out


def _forecast(raw: dict, b: dict, now: datetime, tz) -> list:
    today = now.date()
    days = []
    for f in raw["forecasts"].get(b.get("weather") or "", []):
        dt = parse_dt(f.get("datetime", ""), tz)
        if not dt:
            continue
        d = dt.date()
        if d <= today or len(days) >= b["days"]:
            continue
        precip = fnum(f.get("precipitation"))
        days.append({
            "label": DAYS_SHORT[d.weekday()],
            "date": f"{d.day}. {d.month}.",
            "icon": WEATHER_ICONS.get(f.get("condition", ""), "weather-cloudy-alert"),
            "max": num(f.get("temperature"), 0),
            "min": num(f.get("templow"), 0),
            "precip": (num(precip, 0 if precip >= 10 else 1) + " mm") if precip and precip >= 0.5 else None,
        })
    return days


def short_headsign(h: str) -> str:
    """„Praha,Budějovická“ -> „Budějovická“ (Praha je u většiny spojů zbytečná)."""
    h = h.strip()
    if h.startswith("Praha,") and len(h) > 6:
        h = h[6:]
    return h.replace(",", ", ")


def _departures(raw: dict, b: dict, now: datetime, tz, rows: int) -> dict:
    s = raw["states"].get(b["entity"])
    block = {"stop": "", "rows": [], "error": None, "disruptions": None}
    if not b["entity"]:
        block["error"] = "Není vybrána odjezdová tabule"
        return block
    if not s or s["state"] in ("unavailable", "unknown"):
        block["error"] = "Odjezdy nejsou dostupné"
        return block
    a = s["attributes"]
    block["stop"] = a.get("stop_name", "")

    dis = raw["states"].get(b.get("disruptions") or "")
    if dis and (fnum(dis["state"]) or 0) > 0:
        texts = dis["attributes"].get("infotexts") or []
        first = texts[0] if texts else ""
        if isinstance(first, dict):
            first = first.get("text") or first.get("display_text") or ""
        block["disruptions"] = {"count": int(fnum(dis["state"])), "text": str(first)[:160] or "Výluka"}
    # řádek s textem výluky zabere místo jednoho odjezdu
    count = max(1, rows - (1 if block["disruptions"] else 0))

    limit = (now + timedelta(minutes=b.get("walk_minutes", 0))).replace(second=0, microsecond=0)
    for d in a.get("departures", []):
        t = parse_dt(d.get("predicted") or d.get("scheduled") or "", tz)
        if not t or t < limit:
            continue
        delay = d.get("delay_min") or 0
        canceled = bool(d.get("canceled"))
        block["rows"].append({
            "time": t.strftime("%H:%M"),
            "delay": f"+{delay}" if d.get("delay_available") and delay > 0 and not canceled else None,
            "route": str(d.get("route", "")),
            "headsign": short_headsign(d.get("headsign") or ""),
            "platform": d.get("platform") or "",
            "canceled": canceled,
        })
        if len(block["rows"]) >= count:
            break
    if not block["rows"]:
        block["error"] = "Žádné další odjezdy"
    return block


def _normalize_event(ev: dict, tz) -> dict | None:
    s, e = ev.get("start", {}), ev.get("end", {})
    if "date" in s:
        start = date.fromisoformat(s["date"])
        end = date.fromisoformat(e.get("date", s["date"]))
        if end <= start:
            end = start + timedelta(days=1)
        return {"all_day": True, "start": start, "end": end, "summary": ev.get("summary", "")}
    st, en = parse_dt(s.get("dateTime", ""), tz), parse_dt(e.get("dateTime", ""), tz)
    if not st:
        return None
    return {"all_day": False, "start": st, "end": en or st, "summary": ev.get("summary", "")}


def _special_days(raw: dict, cal_id: str, tz) -> dict[date, str]:
    out: dict[date, str] = {}
    for ev in raw["events"].get(cal_id or "", []):
        n = _normalize_event(ev, tz)
        if not n:
            continue
        d = n["start"] if n["all_day"] else n["start"].date()
        out.setdefault(d, n["summary"].strip())
    return out


def calendar_icons(raw: dict, calendars: list) -> dict[str, str]:
    """Ikona kalendáře: vlastní z nastavení, jinak atribut icon entity v HA („mdi:soccer“ -> „soccer“)."""
    out = {}
    for cal in calendars:
        icon = cal.get("icon") or raw["states"].get(cal["entity"], {}).get("attributes", {}).get("icon") or ""
        if icon.startswith("mdi:"):
            out[cal["entity"]] = icon[4:]
    return out


def agenda_metrics(cw: int, icons: bool) -> dict:
    """Rozměry řádku události podle šířky obsahu – stejné prahy jako dashboard.typ."""
    if cw <= 280:
        dlw, tw, ss, gap, ci = 38, 44, 17, 4, False
    elif cw <= 330:
        dlw, tw, ss, gap, ci = 46, 44, 18, 5, icons
    else:
        dlw, tw, ss, gap, ci = 58, 50, 21, 8, icons
    return {"dlw": dlw, "tw": tw, "ss": ss, "gap": gap, "ci": ci, "tags": cw > 330, "line_h": round(ss * 1.15)}


@functools.lru_cache(maxsize=8)
def _font(name: str, size: int):
    return ImageFont.truetype(str(FONTS / name), size)


# neviditelné znaky v emoji (variační selektory, ZWJ, odstín pleti) – v Typstu nulová šířka
_ZERO_WIDTH = {"\ufe0e", "\ufe0f", "\u200d", "\u200b", "\u20e3", *map(chr, range(0x1F3FB, 0x1F400))}


@functools.lru_cache(maxsize=1)
def _emoji_chars() -> dict[str, str]:
    import render  # kódy ikon MDI z CSS (render nezávisí na modelu)
    return emoji.icon_chars(render.mdi_codes())


def _text_width(text: str, font, size: int) -> float:
    """Šířka textu v px jako v Typstu: emoji z tabulky jako ikona MDI, ostatní z Noto Emoji."""
    text = emoji.replace(text, _emoji_chars())
    missing = font.getlength("\U0010FFFD")
    total, run = 0.0, ""
    for ch in text:
        if ch in _ZERO_WIDTH:
            continue
        if ord(ch) >= 0xF0000:  # ikona MDI (soukromá oblast Unicode)
            other = _font("MaterialDesignIcons.ttf", size)
        elif ch.isascii() or font.getlength(ch) != missing:
            run += ch
            continue
        else:
            other = _font("NotoEmoji-400.ttf", size)
        total += font.getlength(run) + other.getlength(ch)
        run = ""
    return total + font.getlength(run)


def wrap_lines(text: str, width: float, size: int, bold: bool) -> int:
    """Kolik řádků zabere text při zalomení po slovech (jako Typst, linebreaks: simple)."""
    font = _font(f"RobotoCondensed-{700 if bold else 500}.ttf", size)
    width -= 0.5  # měření Pillow odpovídá Typstu (ověřeno); jen zaokrouhlení
    lines, cur = 1, 0.0
    space = font.getlength(" ")
    for word in text.split():
        w = _text_width(word, font, size)
        if cur and cur + space + w > width:
            lines += 1
            cur = w
        else:
            cur = cur + space + w if cur else w
    return lines


def _row_height(it: dict, m: dict, cw: int) -> int:
    """Výška řádku události; u kalendáře „celý text“ podle počtu řádků názvu."""
    if not it.get("wrap"):
        it["lines"] = 1
        return EVENT_ROW_H
    width = cw - m["dlw"] - m["tw"] - m["gap"] * 2 - ((18 + m["gap"]) if m["ci"] else 0)
    if m["tags"] and it.get("label"):
        tag = _text_width(it["label"], _font("Inter-700.ttf", 15), 15) + 12  # inset 2 × 6 px (rámeček nezabírá místo)
        width -= tag + m["gap"]
    bold = bool(it.get("red") or it.get("allday"))
    it["lines"] = min(WRAP_MAX_LINES, wrap_lines(it["summary"], width, m["ss"], bold))
    return EVENT_ROW_H + (it["lines"] - 1) * m["line_h"]


def _events(raw: dict, b: dict, now: datetime, tz, budget: int, cw: int) -> dict:
    EVENTS_HEIGHT = budget  # noqa: N806 – lokální rozpočet
    today = now.date()
    holidays = _special_days(raw, b.get("holiday_calendar"), tz)
    guaranteed = today + timedelta(days=b["days"] - 1)
    last = today + timedelta(days=max(b["days"], LOOKAHEAD_DAYS) - 1)
    per_day: dict[date, list] = {}
    icons = calendar_icons(raw, b["calendars"])
    for cal in b["calendars"]:
        for ev in raw["events"].get(cal["entity"], []):
            n = _normalize_event(ev, tz)
            if not n:
                continue
            base = {"summary": n["summary"].strip() or "(bez názvu)", "label": cal.get("label") or "",
                    "red": bool(cal.get("red")), "icon": icons.get(cal["entity"]), "wrap": bool(cal.get("wrap"))}
            if n["all_day"]:
                d = max(n["start"], today)
                while d < n["end"] and d <= last:
                    per_day.setdefault(d, []).append({**base, "time": "", "allday": True, "sort": ""})
                    d += timedelta(days=1)
                continue
            if n["end"] <= now:
                continue  # už proběhlo
            st = n["start"]
            if st.date() < today:  # začalo dříve a stále trvá
                per_day.setdefault(today, []).append({**base, "time": "do " + n["end"].strftime("%H:%M"), "sort": "00"})
            elif st.date() <= last:
                per_day.setdefault(st.date(), []).append({**base, "time": st.strftime("%H:%M"), "sort": st.strftime("%H:%M")})

    m = agenda_metrics(cw, bool(icons))
    days, used, hidden = [], 0, 0
    full = False  # první událost, která se nevešla – pozdější už se nepřeskakují dopředu
    for d in sorted(per_day):
        items = sorted(per_day[d], key=lambda x: (x["sort"], x["summary"]))
        # deduplikace (stejná událost ve více kalendářích)
        seen, uniq = set(), []
        for it in items:
            key = (it["time"], it["summary"])
            if key not in seen:
                seen.add(key)
                uniq.append({k: v for k, v in it.items() if k != "sort"})
        is_today = d == today
        if d in holidays and not is_today:  # dnešní svátek je v hlavičce
            # stejný svátek může být i v některém z běžných kalendářů
            uniq = [it for it in uniq if not it.get("allday")
                    or it["summary"].casefold() != holidays[d].casefold()]
            uniq.insert(0, {"summary": holidays[d], "label": "", "red": True, "icon": None,
                            "time": "", "allday": True})
        if not uniq:
            continue
        if full:
            hidden += len(uniq)
            continue
        heights = [_row_height(it, m, cw) for it in uniq]
        # Agenda: dny jsou jen řádky oddělené tenkou čárou (i Dnes, popisek je ve sloupci dne)
        # čára jen mezi dvěma bloky řádků (stejně jako CSS .aday + .aday)
        head = SEP_H if days and days[-1]["rows"] else 0
        if d > guaranteed:
            # navíc jen celé dny, které se vejdou (i s případným „+N dalších“)
            if used + head + sum(heights) + (MORE_ROW_H if hidden else 0) > EVENTS_HEIGHT:
                break
        elif not is_today and used + head + heights[0] > EVENTS_HEIGHT:
            hidden += len(uniq)
            full = True
            continue
        used += head
        shown = []
        for it, h in zip(uniq, heights):  # události v pořadí, dokud se vejdou
            if used + h > EVENTS_HEIGHT:
                break
            shown.append(it)
            used += h
        hidden += len(uniq) - len(shown)
        full = len(shown) < len(uniq)
        days.append({
            "label": short_day_label(d, today),
            "date": f"{d.day}. {d.month}.",
            "today": is_today,
            "holiday_day": d in holidays,
            "rows": shown,
        })
    # „+N dalších“ potřebuje vlastní řádek – uvolnit místo poslední události, je-li třeba
    if hidden and used + MORE_ROW_H > EVENTS_HEIGHT and days and days[-1]["rows"]:
        days[-1]["rows"].pop()  # každý řádek má aspoň EVENT_ROW_H > MORE_ROW_H
        hidden += 1
        if not days[-1]["rows"]:
            days.pop()
    empty = not days
    return {"days": days, "hidden": hidden, "range": b["days"], "icons": bool(icons), "empty": empty}


def _alert(raw: dict, calendars: list, now: datetime, tz) -> str | None:
    """Nejbližší „červená“ událost dnes/zítra (svoz, narozeniny) jako upozornění do hlavičky."""
    today = now.date()
    for when, d in (("Dnes", today), ("Zítra", today + timedelta(days=1))):
        for cal in calendars:
            if not cal.get("red"):
                continue
            for ev in raw["events"].get(cal["entity"], []):
                n = _normalize_event(ev, tz)
                if not n:
                    continue
                start = n["start"] if n["all_day"] else n["start"].date()
                end = n["end"] if n["all_day"] else n["end"].date() + timedelta(days=1)
                if start <= d < end and n["summary"].strip():
                    return f"{when}: {n['summary'].strip()}"
    return None


def _device(device: dict) -> dict:
    volts = device.get("voltage")
    pct = battery_percent(volts)
    rssi = device.get("rssi")
    if rssi is None:
        wifi = "wifi-strength-off-outline"
    elif rssi >= -55:
        wifi = "wifi-strength-4"
    elif rssi >= -67:
        wifi = "wifi-strength-3"
    elif rssi >= -78:
        wifi = "wifi-strength-2"
    else:
        wifi = "wifi-strength-1"
    # zaokrouhleno na 5 %, aby šum měření nevyvolával překreslení
    rounded = None if pct is None else int(round(pct / 5) * 5)
    if rounded is None:
        bat_icon = "battery-unknown"
    elif rounded < 10:
        bat_icon = "battery-alert-variant-outline"
    elif rounded >= 95:
        bat_icon = "battery"
    else:
        bat_icon = f"battery-{rounded // 10 * 10}"
    return {
        "battery": rounded,
        "battery_icon": bat_icon,
        "battery_low": rounded is not None and rounded < 20,
        "wifi_icon": wifi if rssi is not None and rssi < -80 else None,
    }


STATE_TEXT = {"on": "zapnuto", "off": "vypnuto", "open": "otevřeno", "closed": "zavřeno",
              "opening": "otevírá se", "closing": "zavírá se", "home": "doma", "not_home": "pryč",
              "locked": "zamčeno", "unlocked": "odemčeno", "playing": "hraje", "paused": "pozastaveno",
              "idle": "nečinný", "standby": "pohotovost", "cleaning": "uklízí", "docked": "v základně",
              "returning": "vrací se", "charging": "nabíjí", "discharging": "vybíjí", "full": "nabito",
              "heat": "topí", "cool": "chladí", "auto": "automaticky", "armed_away": "střeženo",
              "armed_home": "střeženo doma", "disarmed": "nestřeženo", "triggered": "poplach",
              "above_horizon": "nad obzorem", "below_horizon": "pod obzorem", "true": "ano", "false": "ne"}
# binární senzory: význam on/off podle device_class
BINARY_TEXT = {
    "door": ("otevřeno", "zavřeno"), "window": ("otevřeno", "zavřeno"), "opening": ("otevřeno", "zavřeno"),
    "garage_door": ("otevřeno", "zavřeno"), "lock": ("odemčeno", "zamčeno"),
    "motion": ("pohyb", "klid"), "occupancy": ("obsazeno", "volno"), "presence": ("doma", "pryč"),
    "moisture": ("mokro", "sucho"), "battery": ("slabá", "OK"), "battery_charging": ("nabíjí", "nenabíjí"),
    "connectivity": ("připojeno", "odpojeno"), "plug": ("zapojeno", "odpojeno"), "power": ("zapnuto", "vypnuto"),
    "problem": ("problém", "OK"), "safety": ("nebezpečí", "OK"), "smoke": ("kouř", "OK"), "gas": ("plyn", "OK"),
    "running": ("běží", "neběží"), "update": ("aktualizace", "aktuální"), "vibration": ("vibrace", "klid"),
}


def format_state(s: dict | None, attribute: str = "", decimals: int = 1, maxlen: int = 60) -> dict:
    """Stav (nebo atribut) entity pro displej: {value, unit, number, text}.
    Čísla česky s jednotkou, časy jako „9. 10. 18:40“, známé stavy přeloženě, jinak text."""
    out = {"value": "—", "unit": "", "number": None, "text": False}
    if not s:
        return out
    a = s.get("attributes", {})
    raw = a.get(attribute) if attribute else s.get("state")
    if raw is None or raw in ("unavailable", "unknown", ""):
        return out
    if isinstance(raw, (list, tuple)):
        raw = ", ".join(str(x) for x in raw)
    elif isinstance(raw, dict):
        raw = ", ".join(f"{k}: {v}" for k, v in raw.items())
    v = fnum(raw) if not isinstance(raw, bool) else None
    if v is not None:
        if isinstance(raw, int):  # celočíselný atribut (jas, počet…) bez desetinných míst
            decimals = 0
        unit = "" if attribute else (a.get("unit_of_measurement") or "")
        temp = unit in TEMP_UNITS or a.get("device_class") == "temperature"
        out.update(value=hnum(f"{s.get('entity_id', '')}:{attribute}", v, decimals, TEMP_MIN_CHANGE if temp else 0.0),
                   number=v, unit=unit)
        return out
    text = str(raw)
    domain = s.get("entity_id", "").split(".")[0]
    dc = a.get("device_class")
    if domain == "binary_sensor" and text in ("on", "off") and dc in BINARY_TEXT:
        text = BINARY_TEXT[dc][0 if text == "on" else 1]
    elif isinstance(raw, bool):
        text = "ano" if raw else "ne"
    elif text in STATE_TEXT:
        text = STATE_TEXT[text]
    else:
        try:  # časový údaj (device_class timestamp, last_triggered…)
            if len(text) >= 16 and text[4] == "-" and "T" in text:
                dt = datetime.fromisoformat(text).astimezone()
                text = f"{dt.day}. {dt.month}. {dt:%H:%M}"
            elif len(text) == 10 and text[4] == "-" and text[7] == "-":
                d = date.fromisoformat(text)
                text = f"{d.day}. {d.month}. {d.year}"
        except ValueError:
            pass
    out.update(value=text[:maxlen] + ("…" if len(text) > maxlen else ""), text=True)
    return out


def _icon(b: dict, s: dict | None) -> str | None:
    icon = b.get("icon") or (s or {}).get("attributes", {}).get("icon") or ""
    return icon[4:] if icon.startswith("mdi:") else (icon or None)


def _value(raw: dict, b: dict) -> dict:
    """Jedna hodnota entity (stav nebo atribut): číslo s jednotkou, nebo text."""
    s = raw["states"].get(b["entity"]) if b["entity"] else None
    a = (s or {}).get("attributes", {})
    f = format_state(s, b["attribute"], b["decimals"])
    lo, hi = fnum(b["red_below"]), fnum(b["red_above"])
    v = f["number"]
    return {"label": (b["label"] or a.get("friendly_name") or b["entity"] or "Hodnota") if b["show_label"] else "",
            "icon": _icon(b, s), "value": f["value"], "unit": f["unit"], "text": f["text"],
            "red": v is not None and ((lo is not None and v < lo) or (hi is not None and v > hi))}


def _footer_items(raw: dict, items: list) -> list:
    out = []
    for it in items:
        s = raw["states"].get(it["entity"])
        f = format_state(s, it.get("attribute", ""), it.get("decimals", 1), maxlen=40)
        out.append({"icon": _icon(it, s), "label": it.get("label", ""), "value": f["value"],
                    "unit": f["unit"], "side": it.get("side", "left")})
    return out


def _text(b: dict) -> dict:
    lines = b["text"].split("\n")[:30]
    return {"lines": lines, "size": b["size"], "align": b["align"], "bold": b["bold"], "red": b["red"]}


# Šablona běží v sandboxu Jinja: žádný přístup k Pythonu ani souborům; výstup je HTML
# (vykresluje se v Chromiu s CSP bez skriptů a sítě), hodnoty entit se escapují.
TPL_MAX_LEN = 10000   # nejdelší řetězec/seznam, který šablona smí vyrobit
TPL_MAX_RANGE = 1000  # nejdelší range()


class _LimitedSandbox(SandboxedEnvironment):
    """Sandbox šablony bez obřích výsledků (běží při každém vykreslení, i po restartu):
    'a' * 10**9, 9 ** 999999, 'x'.ljust(10**8), zdvojování přes + a ~ ani obří range."""
    intercepted_binops = frozenset({"*", "**", "+", "~"})

    def call_binop(self, context, operator, left, right):
        if operator == "**" and isinstance(right, (int, float)) and abs(right) > 64:
            raise SecurityError("příliš velká mocnina")
        if operator == "*":
            seq, n = (left, right) if isinstance(left, (str, list, tuple)) else (right, left)
            if isinstance(seq, (str, list, tuple)) and isinstance(n, int) and n * len(seq) > TPL_MAX_LEN:
                raise SecurityError("příliš dlouhý výsledek")
        out = super().call_binop(context, operator, left, right)
        if isinstance(out, (str, list, tuple)) and len(out) > TPL_MAX_LEN:
            raise SecurityError("příliš dlouhý výsledek")
        return out

    def call(self, __context, __obj, *args, **kwargs):  # noqa: N807 – podpis z Jinja
        # metody řetězců (ljust, center, zfill, format…) s obří šířkou nebo délkou
        if any(isinstance(a, int) and not isinstance(a, bool) and abs(a) > TPL_MAX_LEN for a in (*args, *kwargs.values())):
            raise SecurityError("příliš velký argument")
        out = super().call(__context, __obj, *args, **kwargs)
        if isinstance(out, (str, list, tuple)) and len(out) > TPL_MAX_LEN:
            raise SecurityError("příliš dlouhý výsledek")
        return out


def _safe_range(*args):
    r = range(*args)
    if len(r) > TPL_MAX_RANGE:
        raise SecurityError(f"range delší než {TPL_MAX_RANGE}")
    return r


def _limited_filter(fn):
    """Filtr s kontrolou velikosti výsledku a číselných argumentů (center, format, indent…)."""
    def wrapper(*args, **kwargs):
        if any(isinstance(a, int) and not isinstance(a, bool) and abs(a) > TPL_MAX_LEN for a in (*args[1:], *kwargs.values())):
            raise SecurityError("příliš velký argument")
        out = fn(*args, **kwargs)
        if isinstance(out, str) and len(out) > TPL_MAX_LEN:
            raise SecurityError("příliš dlouhý výsledek")
        return out
    return wrapper


_tpl_env = _LimitedSandbox(autoescape=True)
_tpl_env.globals["range"] = _safe_range
for _f in ("center", "format", "indent", "wordwrap", "truncate", "join", "replace"):
    _tpl_env.filters[_f] = _limited_filter(_tpl_env.filters[_f])
_tpl_env.filters["num"] = lambda v, decimals=1: num(v, decimals) or "—"  # „11,6“ jako zbytek displeje


def _template(raw: dict, b: dict, now: datetime) -> dict:
    states = raw["states"]

    def state(eid):
        return (states.get(eid) or {}).get("state", "unknown")

    def attr(eid, name):
        return (states.get(eid) or {}).get("attributes", {}).get(name)

    if not b["code"].strip():
        return {"html": "", "error": "Prázdná šablona"}

    def render() -> dict:
        try:
            html = _tpl_env.from_string(b["code"]).render(
                states=state, state_attr=attr, is_state=lambda e, v: state(e) == v, now=now)
            return {"html": html[:8000], "error": None}
        except Exception as e:  # noqa: BLE001 – chyba šablony nesmí shodit dashboard
            return {"html": "", "error": f"Chyba šablony: {type(e).__name__}: {e}"[:200]}

    out = _isolated(render)
    return {"html": Markup(out["html"]), "error": out["error"]}


TPL_TIMEOUT, TPL_MEMORY = 2.0, 256 * 1024 * 1024


def _isolated(fn) -> dict:
    """Šablona běží v odděleném procesu s časovým a paměťovým limitem: sandbox Jinja
    nezachytí vše (např. zdvojování textu přes ~ v cyklu) a smyčka serveru musí běžet dál."""
    import multiprocessing as mp
    try:
        ctx = mp.get_context("fork")
    except ValueError:      # bez fork (jiný systém) – aspoň sandbox
        return fn()
    recv, send = ctx.Pipe(duplex=False)

    def child():
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (TPL_MEMORY, TPL_MEMORY))
        except (ImportError, ValueError, OSError):
            pass
        try:
            send.send(fn())
        except MemoryError:
            send.send({"html": "", "error": "Chyba šablony: příliš velký výsledek"})

    proc = ctx.Process(target=child, daemon=True)
    proc.start()
    send.close()
    try:
        if recv.poll(TPL_TIMEOUT):
            return recv.recv()
        return {"html": "", "error": f"Chyba šablony: běží déle než {TPL_TIMEOUT:.0f} s"}
    except EOFError:        # proces spadl (paměť)
        return {"html": "", "error": "Chyba šablony: příliš náročná (paměť)"}
    finally:
        recv.close()
        if proc.is_alive():
            proc.kill()
        proc.join(1)


def _header(raw: dict, b: dict, layout: dict, now: datetime, tz) -> dict:
    today = now.date()
    # upozornění bere červené kalendáře z bloku Události (i skrytého)
    red_cals = [c for x in layout["blocks"] if x["type"] == "agenda" for c in x["calendars"]] if b["alert"] else []
    holiday = _special_days(raw, b.get("holiday_calendar"), tz).get(today)
    # Červeně jen státní svátek – víkend by ředil význam červené
    return {
        "day": now.day,
        "weekday": DAYS[now.weekday()],
        "month": MONTHS_GEN[now.month - 1],
        "nameday": _special_days(raw, b.get("nameday_calendar"), tz).get(today),
        "holiday": holiday,
        "alert": _alert(raw, red_cals, now, tz),
        "weather": _weather(raw, b, now, tz) if b["show_weather"] else None,
    }


def build(raw: dict, layout: dict, device: dict, now: datetime, worst: bool = False,
          memory: dict | None = None) -> dict:
    """memory: hodnoty zobrazené na panelu (hystereze zaokrouhlení); None = bez hystereze."""
    global _memory
    _memory = memory
    try:
        return _build(raw, layout, device, now, worst)
    finally:
        _memory = None


def _build(raw: dict, layout: dict, device: dict, now: datetime, worst: bool = False) -> dict:
    """Model zobrazení: bloky s hotovými daty a obdélníkem v px; obsah se přizpůsobí obdélníku.
    worst=True (jen náhled v editoru) vynutí upozornění v záhlaví a výluku v odjezdech."""
    tz = now.tzinfo
    blocks = []
    for b in layout_mod.visible(layout):
        t = b["type"]
        left, top, width, height = layout_mod.rect_px(b)
        lt, ll = layout_mod.LINES[b["line_top"]], layout_mod.LINES[b["line_left"]]
        inner_h = height - lt
        vb = {"type": t, "id": b["id"], "left": left, "top": top, "width": width, "height": height,
              "line_top": lt, "line_left": ll, "inner_w": width - ll, "inner_h": inner_h}
        if t == "header":
            vb.update(_header(raw, b, layout, now, tz))
            if worst and not vb["alert"]:
                vb["alert"] = "Zítra: Ukázkové upozornění"
            # bez spodního řádku (upozornění/svátek) obsah svisle vycentrovat (106 px = datum + jmeniny)
            vb["pad_top"] = 8 if vb["alert"] or vb["holiday"] else max(8, (inner_h - 106) // 2)
        elif t == "weather_now":
            vb["weather"] = _weather(raw, b, now, tz)
        elif t == "departures":
            rows = max(0, (inner_h - layout_mod.DEPS_BASE_H) // layout_mod.DEP_ROW_H)
            vb.update(_departures(raw, b, now, tz, rows))
            if worst and not vb["disruptions"] and rows:
                vb["disruptions"] = {"count": 1, "text": "Ukázková výluka"}
                vb["rows"] = vb["rows"][:max(1, rows - 1)]
        elif t == "forecast":
            # dní jen kolik se vejde (aspoň 60 px na den); pod 100 px na den max a min pod sebou
            fit = max(1, min(b["days"], (width - ll) // 60))
            vb["days"] = _forecast(raw, dict(b, days=fit), now, tz)
            vb["stack"] = bool(vb["days"]) and (width - ll) / len(vb["days"]) < 100
        elif t == "footer":
            vb.update({k: b[k] for k in ("updated", "next", "week", "battery")},
                      week_label=f"{now.isocalendar().week}. týden", items=_footer_items(raw, b["items"]))
        elif t == "text":
            vb.update(_text(b))
        elif t == "template":
            vb.update(_template(raw, b, now))
        elif t == "value":
            vb.update(_value(raw, b))
        elif t == "agenda":
            vb.update(_events(raw, b, now, tz, inner_h - EVENTS_PAD, width - ll - 32))
        blocks.append(vb)
    return {"blocks": blocks, "device": _device(device)}


def digest(view: dict) -> str:
    return hashlib.sha1(json.dumps(view, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8", "surrogatepass")).hexdigest()
