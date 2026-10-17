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
    "schedule": ["06:00-08:00=300", "08:00-15:00=900", "15:00-18:00=300", "18:00-20:00=900", "20:00-06:00=3600"],
    "battery_sensor": "sensor.espink_baterie",
    "battery_notify": "",
    "allowed_devices": [],
}

# ID entity se skládá do URL API HA – nic jiného než „doména.objekt“ nesmí projít
# (jinak by např. „../services/homeassistant/restart“ zavolalo libovolnou službu)
ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
ENTITY_KEYS = {"meteo_temperature", "meteo_humidity", "meteo_rain_today", "meteo_pressure",
               "indoor_temperature", "weather", "departures", "disruptions",
               "nameday_calendar", "holiday_calendar", "battery_sensor", "battery_notify"}


def check_entity(value: str) -> str:
    if value and not ENTITY_RE.match(value):
        raise ValueError(f"neplatné ID entity: {value}")
    return value


def normalize_mac(value) -> str | None:
    mac = str(value or "").strip().upper().replace("-", ":")
    return mac if MAC_RE.match(mac) else None


def load(path: str | None = None) -> dict:
    path = path or os.environ.get("OPTIONS_PATH", "/data/options.json")
    opts = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            opts.update({k: v for k, v in json.load(f).items() if v is not None})
    except FileNotFoundError:
        pass
    opts["rotate"] = int(opts["rotate"])
    # MAC z Konfigurace v jednotném tvaru (AA:BB:…), jinak by se nikdy neshodovala
    opts["allowed_devices"] = [normalize_mac(m) or str(m) for m in opts.get("allowed_devices") or []]
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
                {k: c[k] for k in ("entity", "label", "icon", "red") if c.get(k)}
                for c in v if isinstance(c, dict) and str(c.get("entity", "")).strip()
            ]
            for c in v:
                c["entity"] = check_entity(str(c["entity"]).strip())
        elif key == "allowed_devices":
            macs = [normalize_mac(m) for m in v if str(m).strip()]
            if None in macs:
                raise ValueError("neplatná MAC adresa v povolených zařízeních")
            v = macs
        elif key == "schedule":
            v = check_rules(v)
        else:
            v = str(v).strip()
            if key in ENTITY_KEYS:
                check_entity(v)
        out[key] = v
    return out


# ---------------------------------------------------------------------------
# Rozvrh probouzení: "[D-D ]HH:MM-HH:MM=sekundy", dny 1=Po … 7=Ne.
# První vyhovující okno vyhrává; mimo všechna okna zařízení spí do začátku
# nejbližšího okna.
# ---------------------------------------------------------------------------
_RULE = re.compile(r"^\s*(?:(\d)(?:-(\d))?\s+)?(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\s*=\s*(\d+)\s*$")

MIN_SLEEP = 60


def _rule_ok(rule: str) -> bool:
    """Formát i rozsah: hodiny 0–24 (24:00 = půlnoc), minuty 0–59, dny 1–7, od ≤ do."""
    m = _RULE.match(rule)
    if not m:
        return False
    d1, d2, h1, m1, h2, m2, _ = m.groups()
    if d1 and not (1 <= int(d1) <= int(d2 or d1) <= 7):
        return False
    for h, mi in ((h1, m1), (h2, m2)):
        if not (0 <= int(h) <= 24 and 0 <= int(mi) <= 59) or (int(h) == 24 and int(mi)):
            return False
    return True


def check_rules(rules) -> list[str]:
    if not isinstance(rules, (list, tuple)):
        raise ValueError("rozvrh musí být seznam pravidel")
    v = [str(r).strip() for r in rules if str(r).strip()]
    bad = [r for r in v if not _rule_ok(r)]
    if bad:
        raise ValueError(f"neplatné pravidlo rozvrhu: {bad[0]}")
    return v
FALLBACK_SLEEP = 1800


@dataclass
class Window:
    days: set[int]
    start: time
    end: time
    interval: int

    @property
    def wraps(self) -> bool:
        """Okno přes půlnoc, např. 23:00-05:00."""
        return self.start >= self.end

    def contains(self, now: datetime) -> bool:
        t = now.time()
        if not self.wraps:
            return now.isoweekday() in self.days and self.start <= t < self.end
        if t >= self.start:  # večerní část – den začátku okna
            return now.isoweekday() in self.days
        if t < self.end:     # ranní část – okno začalo předchozí den
            return (now - timedelta(days=1)).isoweekday() in self.days
        return False

    def end_at(self, now: datetime) -> datetime:
        end = now.replace(hour=self.end.hour, minute=self.end.minute, second=0, microsecond=0)
        return end + timedelta(days=1) if end <= now else end


@dataclass
class Schedule:
    windows: list[Window] = field(default_factory=list)

    @classmethod
    def parse(cls, rules: list[str]) -> "Schedule":
        windows = []
        for rule in rules:
            m = _RULE.match(rule)
            if not m or not _rule_ok(rule):   # i z Konfigurace add-onu (Supervisor kontroluje jen text)
                print(f"[schedule] neplatné pravidlo ignorováno: {rule!r}")
                continue
            d1, d2, h1, m1, h2, m2, sec = m.groups()
            if d1:
                days = set(range(int(d1), int(d2 or d1) + 1))
            else:
                days = set(range(1, 8))
            windows.append(Window(days, time(int(h1) % 24, int(m1)), time(int(h2) % 24, int(m2)), max(MIN_SLEEP, int(sec))))
        return cls(windows)

    def sleep_seconds(self, now: datetime, _depth: int = 0) -> int:
        """Kolik sekund má zařízení spát, aby se probudilo podle rozvrhu."""
        if not self.windows:
            return FALLBACK_SLEEP
        for w in self.windows:
            if w.contains(now):
                end = w.end_at(now)
                until_end = (end - now).total_seconds()
                # Zarovnat na násobky intervalu od půlnoci (5 min = :00, :05, …; hodina = v :00),
                # aby se časy probuzení během dne neposouvaly. Probudí-li se deska o pár
                # sekund dřív, nejbližší hranice se přeskočí (méně než MIN_SLEEP).
                into_day = now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1e6
                wait = w.interval - into_day % w.interval
                if wait < MIN_SLEEP:
                    wait += w.interval
                # Probudit se nejpozději na konci okna, aby platil další režim. Je-li konec blíž
                # než MIN_SLEEP (deska vstala těsně před změnou), rovnou naplánovat podle dalšího
                # režimu – jinak by se probudila ještě jednou za minutu jen kvůli hranici.
                if 0 < until_end < wait:
                    if until_end < MIN_SLEEP and _depth < 3:
                        return int(round(until_end)) + self.sleep_seconds(end, _depth + 1)
                    wait = until_end
                return int(round(max(MIN_SLEEP, wait)))
        nxt = self.next_start(now)
        if nxt is None:
            return FALLBACK_SLEEP
        gap = (nxt - now).total_seconds()
        if gap < MIN_SLEEP and _depth < 3:
            return int(round(gap)) + self.sleep_seconds(nxt, _depth + 1)
        return int(max(MIN_SLEEP, gap))

    def interval_at(self, now: datetime) -> int | None:
        """Interval okna, které platí teď (None = mimo okna, deska spí do dalšího)."""
        return next((w.interval for w in self.windows if w.contains(now)), None)

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
