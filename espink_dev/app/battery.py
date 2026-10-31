"""Odhad výdrže baterie z vývoje napětí za posledních pár dní.

Napětí se při každém probuzení uloží (nejvýš jednou za 30 min, 7 dní zpět) do battery.json.
Procenta podle vybíjecí křivky (model.battery_percent) proložená přímkou dají pokles za den
a z něj počet dní do vybití. Skok napětí o víc než CHARGE_JUMP (nabití, výměna baterie)
potvrzený dalším měřením historii smaže – odhad se pak počítá znovu až z nových dat.
Jednorázový výkyv (šum ADC, ESPink 2.x skáče o ~0,06 V) se zahodí; napětí nad USB_VOLTS
(deska na USB kabelu nebo se nabíjí) se do historie nepočítá vůbec.
"""
import json
import os
import statistics

import layout as layout_mod
from model import battery_percent

SAMPLE_GAP = 1800          # s – nejvýš jeden vzorek za 30 min
KEEP = 7 * 86400           # s – jak daleko zpět se počítá
MIN_SPAN = 86400           # s – odhad až z dat aspoň za den (šum ±0,02 V ≈ ±3 %)
MIN_SAMPLES = 8
CHARGE_JUMP = 0.1          # V – skok proti mediánu posledních 3 h = nabití / výměna (potvrdí ho další měření)
USB_VOLTS = 4.25           # V – víc baterie nemá: deska běží z USB / nabíjí se
MAX_DAYS = 365
LOW_PERCENT = 15           # upozornění pod tolik procent …
LOW_DAYS = 7               # … nebo pod tolik dní výdrže


def path() -> str:
    return layout_mod._sibling("BATTERY_PATH", "battery.json")


def load() -> dict:
    """{mac: {"samples": [[unix_s, volty], …], "notified": bool}}"""
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    out = {}
    for mac, h in (data.items() if isinstance(data, dict) else []):
        if not isinstance(h, dict):
            continue
        samples = [[float(t), float(v)] for t, v in (h.get("samples") or [])
                   if isinstance(t, (int, float)) and isinstance(v, (int, float))]
        out[mac] = {"samples": samples, "notified": bool(h.get("notified"))}
        p = h.get("pending")
        if isinstance(p, list) and len(p) == 2 and all(isinstance(x, (int, float)) for x in p):
            out[mac]["pending"] = [float(p[0]), float(p[1])]
    return out


def save(data: dict) -> None:
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path())


def add(h: dict, ts: float, volts: float | None) -> bool:
    """Přidá vzorek; True = historie se změnila (uložit). Při potvrzeném skoku napětí začne znovu."""
    if not volts or volts <= 0 or volts > USB_VOLTS:
        return False
    s = h.setdefault("samples", [])
    sample = [round(ts), round(volts, 4)]
    recent = [v for t, v in s if ts - t < 3 * 3600] or [v for _, v in s[-1:]]
    if recent and abs(volts - statistics.median(recent)) > CHARGE_JUMP:
        pending = h.get("pending")
        if not (pending and abs(volts - pending[1]) <= CHARGE_JUMP / 2):
            h["pending"] = sample          # zatím jen výkyv – počkat na další měření
            return True
        s[:] = [pending]                   # nová úroveň potvrzená: nabito / jiná baterie
        h["notified"] = False
        h.pop("pending", None)
        changed = True
    else:
        changed = h.pop("pending", None) is not None
    if s and ts - s[-1][0] < SAMPLE_GAP:
        return changed
    s.append(sample)
    s[:] = [x for x in s if ts - x[0] <= KEEP]
    return True


def estimate(h: dict, now: float) -> float | None:
    """Dny do vybití (0 %) podle přímky proložené procenty; None = málo dat nebo baterie neklesá."""
    pts = [(t, battery_percent(v)) for t, v in h.get("samples") or [] if now - t <= KEEP]
    pts = [(t, p) for t, p in pts if p is not None]
    if len(pts) < MIN_SAMPLES or pts[-1][0] - pts[0][0] < MIN_SPAN:
        return None
    n = len(pts)
    mt = sum(t for t, _ in pts) / n
    mp = sum(p for _, p in pts) / n
    var = sum((t - mt) ** 2 for t, _ in pts)
    slope = sum((t - mt) * (p - mp) for t, p in pts) / var * 86400   # % za den
    if slope >= 0:
        return None
    level = mp + slope * (now - mt) / 86400                           # proložená hodnota teď
    return min(MAX_DAYS, max(0.0, level / -slope))


def low(percent: int | None, days: float | None) -> bool:
    return (percent is not None and percent <= LOW_PERCENT) or (days is not None and days <= LOW_DAYS)
