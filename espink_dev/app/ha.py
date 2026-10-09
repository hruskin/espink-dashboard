"""Komunikace s Home Assistantem přes Supervisor proxy (nebo HA_URL + HA_TOKEN při vývoji)."""
import asyncio
import os
from datetime import datetime, timedelta

import aiohttp

from options import ENTITY_RE

TIMEOUT = aiohttp.ClientTimeout(total=15)


class HomeAssistant:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        if os.environ.get("SUPERVISOR_TOKEN") or os.path.exists("/data/options.json"):
            # V add-onu vždy přes Supervisor; chybějící token je chyba, ne důvod jít jinudy
            self.base = "http://supervisor/core/api"
            token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
            if not token:
                print("[ha] VAROVÁNÍ: chybí SUPERVISOR_TOKEN")
        else:
            self.base = os.environ.get("HA_URL", "http://homeassistant.local:8123").rstrip("/") + "/api"
            token = os.environ.get("HA_TOKEN", "")
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        # vývoj: HA přes IP s certifikátem na doménu => HA_VERIFY_SSL=0
        self.ssl = False if os.environ.get("HA_VERIFY_SSL") == "0" else None

    async def _get(self, path: str, params: dict | None = None):
        async with self.session.get(self.base + path, headers=self.headers, params=params, timeout=TIMEOUT, ssl=self.ssl) as r:
            r.raise_for_status()
            return await r.json()

    async def _post(self, path: str, payload: dict):
        async with self.session.post(self.base + path, headers=self.headers, json=payload, timeout=TIMEOUT, ssl=self.ssl) as r:
            r.raise_for_status()
            return await r.json()

    async def ping(self) -> None:
        """Ověří, že API HA odpovídá – jinak nemá smysl kreslit prázdný dashboard."""
        try:
            await self._get("/")
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Home Assistant API nedostupné ({type(e).__name__}: {e})") from e

    async def state(self, entity_id: str) -> dict | None:
        if not ENTITY_RE.match(entity_id or ""):
            if entity_id:
                print(f"[ha] neplatné ID entity ignorováno: {entity_id!r}")
            return None
        try:
            return await self._get(f"/states/{entity_id}")
        except Exception as e:  # noqa: BLE001 – jedna chybějící entita nesmí shodit celý dashboard
            print(f"[ha] stav {entity_id}: {e}")
            return None

    async def calendar_events(self, entity_id: str, start: datetime, end: datetime) -> list[dict]:
        if not ENTITY_RE.match(entity_id or ""):
            if entity_id:
                print(f"[ha] neplatné ID entity ignorováno: {entity_id!r}")
            return []
        try:
            return await self._get(
                f"/calendars/{entity_id}",
                {"start": start.isoformat(), "end": end.isoformat()},
            )
        except Exception as e:  # noqa: BLE001
            print(f"[ha] kalendář {entity_id}: {e}")
            return []

    async def daily_forecast(self, entity_id: str) -> list[dict]:
        """Denní předpověď; když ji entita neumí, dopočítá se z twice_daily/hourly."""
        if not ENTITY_RE.match(entity_id or ""):
            if entity_id:
                print(f"[ha] neplatné ID entity ignorováno: {entity_id!r}")
            return []
        errors = []
        for kind in ("daily", "twice_daily", "hourly"):
            try:
                res = await self._post(
                    "/services/weather/get_forecasts?return_response",
                    {"entity_id": entity_id, "type": kind},
                )
                items = res.get("service_response", {}).get(entity_id, {}).get("forecast", [])
            except Exception as e:  # noqa: BLE001
                errors.append(f"{kind}: {e}")
                continue
            if items:
                return items if kind == "daily" else aggregate_daily(items)
        print(f"[ha] předpověď {entity_id} nedostupná ({'; '.join(errors) or 'prázdná'})")
        return []

    async def entities(self) -> list[dict]:
        """Seznam entit pro našeptávač v nastavení (id, název, jednotka)."""
        states = await self._get("/states")
        return sorted(
            ({"id": s["entity_id"], "name": s["attributes"].get("friendly_name", ""),
              "unit": s["attributes"].get("unit_of_measurement", ""), "state": str(s.get("state", ""))[:60]}
             for s in states),
            key=lambda e: e["id"],
        )

    async def save_addon_options(self, opts: dict) -> None:
        """Uloží konfiguraci add-onu přes Supervisor (validuje ji podle schématu)."""
        url = self.base.removesuffix("/core/api") + "/addons/self/options"
        async with self.session.post(url, headers=self.headers, json={"options": opts}, timeout=TIMEOUT, ssl=self.ssl) as r:
            body = await r.json(content_type=None)
            if r.status != 200 or body.get("result") != "ok":
                raise RuntimeError(body.get("message") or f"HTTP {r.status}")

    async def set_state(self, entity_id: str, state, attributes: dict) -> None:
        if not ENTITY_RE.match(entity_id or ""):
            if entity_id:
                print(f"[ha] neplatné ID entity ignorováno: {entity_id!r}")
            return None
        try:
            await self._post(f"/states/{entity_id}", {"state": state, "attributes": attributes})
        except Exception as e:  # noqa: BLE001
            print(f"[ha] zápis {entity_id}: {e}")


def aggregate_daily(items: list[dict]) -> list[dict]:
    """Sloučí hodinovou/dvoudenní předpověď do dnů (max/min teplota, součet srážek,
    počasí nejblíže poledni)."""
    days: dict[str, dict] = {}
    for f in items:
        try:
            dt = datetime.fromisoformat(f["datetime"]).astimezone()
        except (KeyError, ValueError):
            continue
        key = dt.date().isoformat()
        d = days.setdefault(key, {"datetime": dt.replace(hour=12, minute=0).isoformat(),
                                  "temperature": None, "templow": None, "precipitation": 0.0,
                                  "condition": None, "_dist": 99})
        temps = [t for t in (f.get("temperature"), f.get("templow")) if isinstance(t, (int, float))]
        if temps:
            d["temperature"] = max(temps + ([d["temperature"]] if d["temperature"] is not None else []))
            d["templow"] = min(temps + ([d["templow"]] if d["templow"] is not None else []))
        if isinstance(f.get("precipitation"), (int, float)):
            d["precipitation"] += f["precipitation"]
        dist = abs(dt.hour - 12)
        if f.get("condition") and dist < d["_dist"]:
            d["condition"], d["_dist"] = f["condition"], dist
    out = []
    for d in days.values():
        d.pop("_dist")
        out.append(d)
    return sorted(out, key=lambda d: d["datetime"])


async def collect(ha: HomeAssistant, needs: dict, now: datetime) -> dict:
    """Stáhne všechna surová data potřebná pro rozvržení (paralelně); needs viz layout.needs()."""
    await ha.ping()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day_start + timedelta(days=31 + 1)  # viz model.LOOKAHEAD_DAYS
    state_ids, cal_ids, fc_ids = needs["states"], needs["calendars"], needs["forecasts"]
    states, events, forecasts = await asyncio.gather(
        asyncio.gather(*(ha.state(e) for e in state_ids)),
        asyncio.gather(*(ha.calendar_events(c, day_start, end) for c in cal_ids)),
        asyncio.gather(*(ha.daily_forecast(w) for w in fc_ids)),
    )
    return {
        "states": {e: s for e, s in zip(state_ids, states) if s},
        "events": dict(zip(cal_ids, events)),
        "forecasts": dict(zip(fc_ids, forecasts)),
    }
