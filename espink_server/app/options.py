"""Načtení konfigurace add-onu (/data/options.json) s výchozími hodnotami."""
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

DEFAULTS = {
    "rotate": 90,
    "image_format": "z2",
    "render_interval": 60,
    "meteo_temperature": "",
    "meteo_humidity": "",
    "meteo_rain_today": "",
    "meteo_pressure": "",
    "indoor_temperature": "",
    "weather": "",
    "departures": "",
    "disruptions": "",
    "departures_count": 5,
    "walk_minutes": 0,
    "calendars": [],
    "nameday_calendar": "",
    "holiday_calendar": "",
    "event_days": 7,
    "schedule": ["06:00-22:30=1200"],
    "battery_sensor": "sensor.espink_baterie",
}


def load(path: str | None = None) -> dict:
    path = path or os.environ.get("OPTIONS_PATH", "/data/options.json")
    opts = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            opts.update({k: v for k, v in json.load(f).items() if v is not None})
    except FileNotFoundError:
        pass
    opts["rotate"] = int(opts["rotate"])
    return opts


INT_KEYS = {"rotate", "render_interval", "departures_count", "walk_minutes", "event_days"}


def sanitize(new: dict, current: dict) -> dict:
    """Převezme z formuláře jen známé klíče se správnými typy (finální validaci dělá Supervisor)."""
    out = dict(current)
    for key, default in DEFAULTS.items():
        if key not in new:
            continue
        v = new[key]
        if key in INT_KEYS:
            v = int(v)
        elif key == "calendars":
            v = [
                {k: c[k] for k in ("entity", "label", "red") if c.get(k)}
                for c in v if isinstance(c, dict) and str(c.get("entity", "")).strip()
            ]
        elif key == "schedule":
            v = [str(r).strip() for r in v if str(r).strip()]
            bad = [r for r in v if not _RULE.match(r)]
            if bad:
                raise ValueError(f"neplatné pravidlo rozvrhu: {bad[0]}")
        else:
            v = str(v).strip()
        out[key] = v
    return out


# ---------------------------------------------------------------------------
# Rozvrh probouzení: "[D-D ]HH:MM-HH:MM=sekundy", dny 1=Po … 7=Ne.
# První vyhovující okno vyhrává; mimo všechna okna zařízení spí do začátku
# nejbližšího okna.
# ---------------------------------------------------------------------------
_RULE = re.compile(r"^\s*(?:(\d)(?:-(\d))?\s+)?(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\s*=\s*(\d+)\s*$")

MIN_SLEEP = 60
FALLBACK_SLEEP = 1800


@dataclass
class Window:
    days: set[int]
    start: time
    end: time
    interval: int

    def contains(self, now: datetime) -> bool:
        if now.isoweekday() not in self.days:
            return False
        t = now.time()
        return self.start <= t < self.end

    def end_at(self, now: datetime) -> datetime:
        return now.replace(hour=self.end.hour, minute=self.end.minute, second=0, microsecond=0)


@dataclass
class Schedule:
    windows: list[Window] = field(default_factory=list)

    @classmethod
    def parse(cls, rules: list[str]) -> "Schedule":
        windows = []
        for rule in rules:
            m = _RULE.match(rule)
            if not m:
                print(f"[schedule] neplatné pravidlo ignorováno: {rule!r}")
                continue
            d1, d2, h1, m1, h2, m2, sec = m.groups()
            if d1:
                days = set(range(int(d1), int(d2 or d1) + 1))
            else:
                days = set(range(1, 8))
            windows.append(Window(days, time(int(h1), int(m1)), time(int(h2) % 24, int(m2)), max(MIN_SLEEP, int(sec))))
        return cls(windows)

    def sleep_seconds(self, now: datetime) -> int:
        """Kolik sekund má zařízení spát, aby se probudilo podle rozvrhu."""
        if not self.windows:
            return FALLBACK_SLEEP
        for w in self.windows:
            if w.contains(now):
                until_end = (w.end_at(now) - now).total_seconds()
                # Probudit se nejpozději na konci okna, aby platil další režim
                return int(max(MIN_SLEEP, min(w.interval, until_end if until_end > 0 else w.interval)))
        nxt = self.next_start(now)
        if nxt is None:
            return FALLBACK_SLEEP
        return int(max(MIN_SLEEP, (nxt - now).total_seconds()))

    def next_start(self, now: datetime) -> datetime | None:
        best = None
        for offset in range(0, 8):
            day = now + timedelta(days=offset)
            for w in self.windows:
                if day.isoweekday() not in w.days:
                    continue
                cand = day.replace(hour=w.start.hour, minute=w.start.minute, second=0, microsecond=0)
                if cand > now and (best is None or cand < best):
                    best = cand
            if best is not None:
                return best
        return best
