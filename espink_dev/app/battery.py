"""Odhad výdrže baterie z vývoje napětí za posledních pár dní.

Napětí se při každém probuzení uloží (nejvýš jednou za 30 min, 7 dní zpět) do battery.json.
Procenta podle vybíjecí křivky (model.battery_percent) proložená přímkou dají pokles za den
a z něj počet dní do vybití. Skok napětí o víc než CHARGE_JUMP (nabíjení, odpojení USB,
výměna baterie) historii smaže – odhad se pak počítá znovu až z nových dat.
"""
import json
import os

import layout as layout_mod
from model import battery_percent

SAMPLE_GAP = 1800          # s – nejvýš jeden vzorek za 30 min
KEEP = 7 * 86400           # s – jak daleko zpět se počítá
MIN_SPAN = 86400           # s – odhad až z dat aspoň za den (šum ±0,02 V ≈ ±3 %)
MIN_SAMPLES = 8
CHARGE_JUMP = 0.06         # V – skok proti poslední hodině = nabíjení / odpojení / výměna
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
    return out


def save(data: dict) -> None:
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path())


def add(h: dict, ts: float, volts: float | None) -> bool:
    """Přidá vzorek; True = historie se změnila (uložit). Při skoku napětí začne znovu."""
    if not volts or volts <= 0:
        return False
    s = h.setdefault("samples", [])
    recent = [v for t, v in s if ts - t < 3600] or [v for _, v in s[-1:]]
    if recent and abs(volts - sum(recent) / len(recent)) > CHARGE_JUMP:
        s.clear()
        h["notified"] = False
    if s and ts - s[-1][0] < SAMPLE_GAP:
        return False
    s.append([round(ts), round(volts, 4)])
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
