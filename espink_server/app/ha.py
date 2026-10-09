"""Komunikace s Home Assistantem přes Supervisor proxy (nebo HA_URL + HA_TOKEN při vývoji)."""
import asyncio
import os
from datetime import datetime, timedelta

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=15)


class HomeAssistant:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        if os.environ.get("SUPERVISOR_TOKEN") or os.path.exists("/data/options.json"):
            # V add-onu vždy přes Supervisor; chybějící token je chyba, ne důvod jít jinudy
            self.base = "http://supervisor/core/api"
            token = os.environ.get("SUPERVISOR_TOKEN", "")
            if not token:
                print("[ha] VAROVÁNÍ: chybí SUPERVISOR_TOKEN")
        else:
            self.base = os.environ.get("HA_URL", "http://homeassistant.local:8123").rstrip("/") + "/api"
            token = os.environ.get("HA_TOKEN", "")
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def _get(self, path: str, params: dict | None = None):
        async with self.session.get(self.base + path, headers=self.headers, params=params, timeout=TIMEOUT) as r:
            r.raise_for_status()
            return await r.json()

    async def _post(self, path: str, payload: dict):
        async with self.session.post(self.base + path, headers=self.headers, json=payload, timeout=TIMEOUT) as r:
            r.raise_for_status()
            return await r.json()

    async def ping(self) -> None:
        """Ověří, že API HA odpovídá – jinak nemá smysl kreslit prázdný dashboard."""
        try:
            await self._get("/")
        except Exception as e:  # noqa: BLE001
            hint = ""
            if self.base.startswith("http://supervisor"):
                hint = (" – pokud má HA zapnuté HTTPS, nastavte v terminálu HA"
                        " `ha core options --ssl=true` a restartujte add-on")
            raise RuntimeError(f"Home Assistant API nedostupné ({type(e).__name__}: {e}){hint}") from e

    async def state(self, entity_id: str) -> dict | None:
        if not entity_id:
            return None
        try:
            return await self._get(f"/states/{entity_id}")
        except Exception as e:  # noqa: BLE001 – jedna chybějící entita nesmí shodit celý dashboard
            print(f"[ha] stav {entity_id}: {e}")
            return None

    async def calendar_events(self, entity_id: str, start: datetime, end: datetime) -> list[dict]:
        try:
            return await self._get(
                f"/calendars/{entity_id}",
                {"start": start.isoformat(), "end": end.isoformat()},
            )
        except Exception as e:  # noqa: BLE001
            print(f"[ha] kalendář {entity_id}: {e}")
            return []

    async def daily_forecast(self, entity_id: str) -> list[dict]:
        if not entity_id:
            return []
        try:
            res = await self._post(
                "/services/weather/get_forecasts?return_response",
                {"entity_id": entity_id, "type": "daily"},
            )
            return res.get("service_response", {}).get(entity_id, {}).get("forecast", [])
        except Exception as e:  # noqa: BLE001
            print(f"[ha] předpověď {entity_id}: {e}")
            return []

    async def set_state(self, entity_id: str, state, attributes: dict) -> None:
        try:
            await self._post(f"/states/{entity_id}", {"state": state, "attributes": attributes})
        except Exception as e:  # noqa: BLE001
            print(f"[ha] zápis {entity_id}: {e}")


async def collect(ha: HomeAssistant, opts: dict, now: datetime) -> dict:
    """Stáhne všechna surová data potřebná pro dashboard (paralelně)."""
    state_ids = [
        opts[k]
        for k in ("meteo_temperature", "meteo_humidity", "meteo_rain_today", "meteo_pressure",
                  "indoor_temperature", "weather", "departures", "disruptions")
        if opts.get(k)
    ]
    await ha.ping()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day_start + timedelta(days=opts["event_days"] + 1)
    cal_ids = [c["entity"] for c in opts["calendars"]]
    for k in ("nameday_calendar", "holiday_calendar"):
        if opts.get(k):
            cal_ids.append(opts[k])

    states, events, forecast = await asyncio.gather(
        asyncio.gather(*(ha.state(e) for e in state_ids)),
        asyncio.gather(*(ha.calendar_events(c, day_start, end) for c in cal_ids)),
        ha.daily_forecast(opts.get("weather", "")),
    )
    return {
        "states": {e: s for e, s in zip(state_ids, states) if s},
        "events": dict(zip(cal_ids, events)),
        "forecast": forecast,
    }
