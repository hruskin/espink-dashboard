"""Převod surových dat z HA na model zobrazení.

Model obsahuje jen hotové řetězce/příznaky, které se opravdu vykreslí. Jeho hash
proto přesně odpovídá tomu, zda se obrázek změnil (=> hlavička Timestamp).
"""
import hashlib
import json
from datetime import date, datetime, timedelta

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

# Výškový rozpočet sekce událostí v px (musí sedět s CSS v šabloně)
# Výšky sekcí v px – musí sedět s CSS v šabloně
HEADER_H, DEPS_H, FORECAST_H, FOOTER_H, EVENTS_PAD = 138, 188, 104, 24, 6
EVENTS_HEIGHT = 800 - HEADER_H - DEPS_H - FORECAST_H - FOOTER_H - EVENTS_PAD  # = 340
DAY_HEADER_H = 34
EVENT_ROW_H = 29
MORE_ROW_H = 22
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


def day_label(d: date, today: date) -> str:
    if d == today:
        return "Dnes"
    if d == today + timedelta(days=1):
        return "Zítra"
    return DAYS[d.weekday()].capitalize()


# ---------------------------------------------------------------------------

def _header(now: datetime, cal_today: dict) -> dict:
    # Červeně jen státní svátek – víkend by ředil význam červené
    return {
        "day": now.day,
        "weekday": DAYS[now.weekday()],
        "month": MONTHS_GEN[now.month - 1],
        "week": f"{now.isocalendar().week}. týden",
        "nameday": cal_today.get("nameday"),
        "holiday": cal_today.get("holiday"),
        "alert": cal_today.get("alert"),
    }


EVENING_HOUR = 18  # od této hodiny ukazovat v hlavičce min/max na zítřek


def _weather(raw: dict, opts: dict, now: datetime, tz) -> tuple[dict, list]:
    today = now.date()
    minmax_day = today + timedelta(days=1) if now.hour >= EVENING_HOUR else today
    states = raw["states"]
    st = lambda key: states.get(opts.get(key) or "", {}).get("state")  # noqa: E731
    w = states.get(opts.get("weather") or "")
    cond = w["state"] if w else None

    temp = st("meteo_temperature")
    if fnum(temp) is None and w:
        temp = w["attributes"].get("temperature")
    hum = st("meteo_humidity")
    if fnum(hum) is None and w:
        hum = w["attributes"].get("humidity")
    rain = fnum(st("meteo_rain_today"))

    now_block = {
        "icon": WEATHER_ICONS.get(cond or "", "weather-cloudy-alert"),
        "temp": num(temp),
        "humidity": num(hum, 0),
        "rain": num(rain) if rain and rain >= 0.1 else None,
        "pressure": num(st("meteo_pressure"), 0),
        "indoor": num(st("indoor_temperature")),
        "min": None,
        "max": None,
        "minmax_label": "zítra" if minmax_day != today else "",
    }

    days = []
    for f in raw.get("forecast", []):
        dt = parse_dt(f.get("datetime", ""), tz)
        if not dt:
            continue
        d = dt.date()
        if d == minmax_day:
            now_block["min"] = num(f.get("templow"), 0)
            now_block["max"] = num(f.get("temperature"), 0)
        if d == today:
            continue
        if d < today or len(days) >= 4:
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
    return now_block, days


def short_headsign(h: str) -> str:
    """„Praha,Budějovická“ -> „Budějovická“ (Praha je u většiny spojů zbytečná)."""
    h = h.strip()
    if h.startswith("Praha,") and len(h) > 6:
        h = h[6:]
    return h.replace(",", ", ")


def _departures(raw: dict, opts: dict, now: datetime, tz) -> dict | None:
    if not opts.get("departures") or opts.get("departures_count", 0) <= 0:
        return None
    s = raw["states"].get(opts["departures"])
    block = {"stop": "", "rows": [], "error": None, "disruptions": None}
    if not s or s["state"] in ("unavailable", "unknown"):
        block["error"] = "Odjezdy nejsou dostupné"
        return block
    a = s["attributes"]
    block["stop"] = a.get("stop_name", "")

    dis = raw["states"].get(opts.get("disruptions") or "")
    if dis and (fnum(dis["state"]) or 0) > 0:
        texts = dis["attributes"].get("infotexts") or []
        first = texts[0] if texts else ""
        if isinstance(first, dict):
            first = first.get("text") or first.get("display_text") or ""
        block["disruptions"] = {"count": int(fnum(dis["state"])), "text": str(first)[:160] or "Výluka"}
    # řádek s textem výluky zabere místo jednoho odjezdu
    count = max(1, opts["departures_count"] - (1 if block["disruptions"] else 0))

    limit = (now + timedelta(minutes=opts.get("walk_minutes", 0))).replace(second=0, microsecond=0)
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


def calendar_icons(raw: dict, opts: dict) -> dict[str, str]:
    """Ikona kalendáře: vlastní z nastavení, jinak atribut icon entity v HA („mdi:soccer“ -> „soccer“)."""
    out = {}
    for cal in opts["calendars"]:
        icon = cal.get("icon") or raw["states"].get(cal["entity"], {}).get("attributes", {}).get("icon") or ""
        if icon.startswith("mdi:"):
            out[cal["entity"]] = icon[4:]
    return out


def _events(raw: dict, opts: dict, now: datetime, tz, holidays: dict[date, str],
            budget: int = EVENTS_HEIGHT) -> dict:
    EVENTS_HEIGHT = budget  # noqa: N806 – lokální rozpočet
    today = now.date()
    guaranteed = today + timedelta(days=opts["event_days"] - 1)
    last = today + timedelta(days=max(opts["event_days"], LOOKAHEAD_DAYS) - 1)
    per_day: dict[date, list] = {today: []}  # „Dnes“ vždy jako kotva přehledu
    icons = calendar_icons(raw, opts)
    for cal in opts["calendars"]:
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
        if d > guaranteed:
            # navíc jen celé dny, které se vejdou (i s případným „+N dalších“)
            if used + DAY_HEADER_H + len(uniq) * EVENT_ROW_H + (MORE_ROW_H if hidden else 0) > EVENTS_HEIGHT:
                break
        elif d != today and used + DAY_HEADER_H + EVENT_ROW_H > EVENTS_HEIGHT:
            hidden += len(uniq)
            continue
        used += DAY_HEADER_H
        room = (EVENTS_HEIGHT - used) // EVENT_ROW_H
        shown = uniq[:room]
        used += len(shown) * EVENT_ROW_H
        hidden += len(uniq) - len(shown)
        days.append({
            "label": day_label(d, today),
            "date": f"{d.day}. {d.month}.",
            "today": d == today,
            "holiday_day": d in holidays,
            "holiday": holidays.get(d) if d != today else None,
            "rows": shown,
        })
    # „+N dalších“ potřebuje vlastní řádek – uvolnit místo poslední události, je-li třeba
    if hidden and used + MORE_ROW_H > EVENTS_HEIGHT and days and days[-1]["rows"]:
        days[-1]["rows"].pop()
        hidden += 1
        if not days[-1]["rows"] and not days[-1]["today"]:
            days.pop()
    empty = len(days) == 1 and not days[0]["rows"]
    return {"days": days, "hidden": hidden, "range": opts["event_days"], "icons": bool(icons), "empty": empty}


def _alert(raw: dict, opts: dict, now: datetime, tz) -> str | None:
    """Nejbližší „červená“ událost dnes/zítra (svoz, narozeniny) jako upozornění do hlavičky."""
    today = now.date()
    for when, d in (("Dnes", today), ("Zítra", today + timedelta(days=1))):
        for cal in opts["calendars"]:
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


def build(raw: dict, opts: dict, device: dict, now: datetime) -> dict:
    tz = now.tzinfo
    today = now.date()
    names = _special_days(raw, opts.get("nameday_calendar"), tz)
    holidays = _special_days(raw, opts.get("holiday_calendar"), tz)
    weather_now, forecast = _weather(raw, opts, now, tz)
    departures = _departures(raw, opts, now, tz)
    budget = EVENTS_HEIGHT + (0 if departures else DEPS_H) + (0 if forecast else FORECAST_H)
    events = _events(raw, opts, now, tz, holidays, budget)
    return {
        "header": _header(now, {"nameday": names.get(today), "holiday": holidays.get(today),
                                "alert": _alert(raw, opts, now, tz)}),
        "weather": weather_now,
        "forecast": forecast,
        "departures": departures,
        "events": events,
        "device": _device(device),
    }


def digest(view: dict) -> str:
    return hashlib.sha1(json.dumps(view, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
