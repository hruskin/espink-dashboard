"""Převod surových dat z HA na model zobrazení.

Model obsahuje jen hotové řetězce/příznaky, které se opravdu vykreslí. Jeho hash
proto přesně odpovídá tomu, zda se obrázek změnil (=> hlavička Timestamp).
"""
import hashlib
import json
from datetime import date, datetime, timedelta

from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup

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

# Výšky řádků agendy v px – musí sedět s CSS v šabloně (výšky bloků viz layout.py)
EVENTS_PAD = 6
DAY_HEADER_H = 34
EVENT_ROW_H = 29
MORE_ROW_H = 22
SEP_H = 1  # tenká čára mezi dny v agendě
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
    return f"{v:.{decimals}f}".replace(".", ",").replace("-", "−")


def fnum(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_dt(value: str, tz) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def short_day_label(d: date, today: date) -> str:
    """Popisek dne do sloupce agendy: „Zítra“, jinak „So 18.“."""
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
        "temp": num(temp),
        "humidity": num(hum, 0),
        "rain": num(rain) if rain and rain >= 0.1 else None,
        "pressure": num(st("pressure"), 0),
        "indoor": num(st("indoor")),
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


def _events(raw: dict, b: dict, now: datetime, tz, budget: int) -> dict:
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
                    "red": bool(cal.get("red")), "icon": icons.get(cal["entity"])}
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

    days, used, hidden = [], 0, 0
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
        # Agenda: Dnes má pruh, ostatní dny jen řádky oddělené tenkou čárou
        # čára jen mezi dvěma bloky řádků (stejně jako CSS .aday + .aday)
        head = DAY_HEADER_H if is_today else (SEP_H if days and days[-1]["rows"] else 0)
        if d > guaranteed:
            # navíc jen celé dny, které se vejdou (i s případným „+N dalších“)
            if used + head + len(uniq) * EVENT_ROW_H + (MORE_ROW_H if hidden else 0) > EVENTS_HEIGHT:
                break
        elif not is_today and used + head + EVENT_ROW_H > EVENTS_HEIGHT:
            hidden += len(uniq)
            continue
        used += head
        room = (EVENTS_HEIGHT - used) // EVENT_ROW_H
        shown = uniq[:room]
        used += len(shown) * EVENT_ROW_H
        hidden += len(uniq) - len(shown)
        days.append({
            "label": short_day_label(d, today),
            "date": f"{d.day}. {d.month}.",
            "today": is_today,
            "holiday_day": d in holidays,
            "rows": shown,
        })
    # „+N dalších“ potřebuje vlastní řádek – uvolnit místo poslední události, je-li třeba
    if hidden and used + MORE_ROW_H > EVENTS_HEIGHT and days and days[-1]["rows"]:
        days[-1]["rows"].pop()
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
              "home": "doma", "not_home": "pryč", "locked": "zamčeno", "unlocked": "odemčeno"}


def _value(raw: dict, b: dict) -> dict:
    """Jedna hodnota entity: číslo s jednotkou, nebo přeložený textový stav."""
    s = raw["states"].get(b["entity"]) if b["entity"] else None
    a = (s or {}).get("attributes", {})
    icon = b["icon"] or a.get("icon") or ""
    out = {"label": b["label"] or a.get("friendly_name") or b["entity"] or "Hodnota",
           "icon": icon[4:] if icon.startswith("mdi:") else (icon or None),
           "value": "—", "unit": "", "red": False}
    if not s or s["state"] in ("unavailable", "unknown"):
        return out
    v = fnum(s["state"])
    if v is None:
        out["value"] = STATE_TEXT.get(s["state"], s["state"])[:30]
        return out
    out["value"] = num(v, b["decimals"])
    out["unit"] = a.get("unit_of_measurement") or ""
    lo, hi = fnum(b["red_below"]), fnum(b["red_above"])
    out["red"] = (lo is not None and v < lo) or (hi is not None and v > hi)
    return out


def _text(b: dict) -> dict:
    lines = b["text"].split("\n")[:30]
    return {"lines": lines, "size": b["size"], "align": b["align"], "bold": b["bold"], "red": b["red"]}


# Šablona běží v sandboxu Jinja: žádný přístup k Pythonu ani souborům; výstup je HTML
# (vykresluje se v Chromiu s CSP bez skriptů a sítě), hodnoty entit se escapují.
_tpl_env = SandboxedEnvironment(autoescape=True)
_tpl_env.filters["num"] = lambda v, decimals=1: num(v, decimals) or "—"  # „11,6“ jako zbytek displeje


def _template(raw: dict, b: dict, now: datetime) -> dict:
    states = raw["states"]

    def state(eid):
        return (states.get(eid) or {}).get("state", "unknown")

    def attr(eid, name):
        return (states.get(eid) or {}).get("attributes", {}).get(name)

    if not b["code"].strip():
        return {"html": "", "error": "Prázdná šablona"}
    try:
        html = _tpl_env.from_string(b["code"]).render(
            states=state, state_attr=attr, is_state=lambda e, v: state(e) == v, now=now)
        return {"html": Markup(html[:8000]), "error": None}
    except Exception as e:  # noqa: BLE001 – chyba šablony nesmí shodit dashboard
        return {"html": "", "error": f"Chyba šablony: {type(e).__name__}: {e}"[:200]}


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


def build(raw: dict, layout: dict, device: dict, now: datetime, worst: bool = False) -> dict:
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
            vb["days"] = _forecast(raw, b, now, tz)
        elif t == "footer":
            vb.update({k: b[k] for k in ("updated", "next", "week", "battery")},
                      week_label=f"{now.isocalendar().week}. týden")
        elif t == "text":
            vb.update(_text(b))
        elif t == "template":
            vb.update(_template(raw, b, now))
        elif t == "value":
            vb.update(_value(raw, b))
        elif t == "agenda":
            vb.update(_events(raw, b, now, tz, inner_h - EVENTS_PAD))
        blocks.append(vb)
    return {"blocks": blocks, "device": _device(device)}


def digest(view: dict) -> str:
    return hashlib.sha1(json.dumps(view, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
