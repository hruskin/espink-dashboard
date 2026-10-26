"""ESPink Server – lokální náhrada serveru Živý obraz pro Home Assistant.

Zařízení (firmware Živý obraz) volá POST /index.php?timestampCheck=1|0 s JSONem
o stavu (baterie, RSSI …). Odpovídáme hlavičkami Timestamp (mění se jen při změně
obsahu => jinak firmware nepřekresluje) a PreciseSleep (rozvrh) a v těle posíláme
obrázek (Z2 nebo PNG).
"""
import argparse
import asyncio
import html
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from aiohttp import web

import battery as battery_mod
import ha as ha_mod
import layout as layout_mod
import firmware as firmware_mod
import mock
import model
import mqtt as mqtt_mod
import options
import panels as panels_mod
import render

def load_container_env() -> None:
    """s6-overlay nepředá CMD proměnné kontejneru (SUPERVISOR_TOKEN, TZ) – načíst je ručně."""
    env_dir = Path("/run/s6/container_environment")
    if env_dir.is_dir():
        for f in env_dir.iterdir():
            if f.is_file():
                os.environ.setdefault(f.name, f.read_text().strip())  # soubory končí \n
    time.tzset()


load_container_env()

INGRESS_IP = "172.30.32.2"
DEV = os.environ.get("DEV") == "1"
MOCK = os.environ.get("MOCK") == "1"
PREVIEW_TTL = 60   # s – jak dlouho editor používá stažená data z HA pro náhled (= výchozí render_interval)


def after(now: datetime, seconds: int) -> str:
    """Doba do další obnovy pro patičku: „5 min“, „1 h 30 min“."""
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def template_mtime() -> float:
    return max(p.stat().st_mtime for p in (render.APP_DIR / "templates").rglob("*") if p.is_file())


def now_local() -> datetime:
    return datetime.now().astimezone()


class Target:
    """Stav jednoho panelu: konfigurace, co deska naposledy hlásila a obrázek, který dostává."""

    def __init__(self, cfg: dict, default_schedule: list[str]):
        self.device: dict = {}
        self.view: dict | None = None
        self.view_hash = ""
        self.content_ts = 0
        self.preview = b""
        self.payload = b""
        self.updated_text = ""
        self.next_text = ""       # „další obnova“ v aktuálním obrázku
        self.served_ts = None     # Timestamp, který deska naposledy dostala (= má na displeji)
        self.served_view: dict | None = None   # model zobrazení na displeji (proč se překresluje)
        self.error = ""
        self.shown: dict = {}     # zaokrouhlené hodnoty na displeji (hystereze, model.hnum)
        self.night = False        # obsah je noční obraz (rozvržení Na noc, k času ranního probuzení)
        self.last_device_rebuild = 0.0
        self.last_publish = 0.0
        self.configure(cfg, default_schedule)

    def configure(self, cfg: dict, default_schedule: list[str]) -> None:
        self.cfg = cfg
        self.schedule = options.Schedule.parse(cfg["schedule"] or default_schedule)


class Server:
    def __init__(self, opts: dict):
        self.opts = opts
        self.layouts, self.layout_saved, self.layout_error = layout_mod.load_all(opts)
        self.panels = panels_mod.load(opts, self.layouts)
        self.targets: dict[str, Target] = {}
        self.sync_targets()
        self.draft_lock = asyncio.Lock()
        self.preview_cache: dict = {}   # data z HA pro náhledy v editoru (fetch_raw_preview)
        self.lock = asyncio.Lock()
        self.ha: ha_mod.HomeAssistant | None = None
        self.mqtt: mqtt_mod.Mqtt | None = None
        self.announced: dict[str, str] = {}   # MAC -> poslední odeslaná konfigurace discovery
        self.legacy_checked: set[str] = set()
        self.last_error = ""
        self.last_build = 0.0
        # bezpečnost: omezení rychlosti a odmítnutá zařízení (čekají na povolení v editoru)
        self.pending: dict[str, dict] = {}
        self.security = panels_mod.load_security()   # klíče desek (X-API-Key), jednorázové TOFU
        self.stats = panels_mod.load_stats()         # probuzení a překreslení za den (Stav, senzory v HA)
        self.battery = battery_mod.load()            # napětí za poslední dny => odhad výdrže
        self.auth_lock = asyncio.Lock()
        if not self.security["tofu_done"] and (opts.get("allowed_devices") or self.panels):
            self.security["tofu_done"] = True        # instalace z doby před klíči: první deska už je
            # desky povolené před verzí s klíči se klíč naučí při prvním spojení – jen 14 dní
            self.security["learn_until"] = time.time() + LEARN_DAYS * 86400
            self.save_security()
        elif "learn_until" not in self.security:
            # aktualizace z verze bez okna pro učení: desky bez klíče ho mají 14 dní na naučení;
            # nová instalace okno nepotřebuje (desky dostanou klíč při povolení)
            self.security["learn_until"] = time.time() + LEARN_DAYS * 86400 if self.security["tofu_done"] else 0
            self.save_security()
        self.failures: dict[str, list[float]] = {}   # MAC/IP -> časy chybných pokusů (omezení hádání klíče)

    # ── panely ───────────────────────────────────────────────────────────
    def sync_targets(self) -> None:
        """Stav podle seznamu panelů; bez panelů jeden náhledový („“) s prvním rozvržením."""
        cfgs = self.panels or [{"mac": "", "name": "Náhled", "layout": next(iter(self.layouts)),
                                "schedule": [], "battery_sensor": ""}]
        old, self.targets = self.targets, {}
        for cfg in cfgs:
            t = old.get(cfg["mac"]) or Target(cfg, self.opts["schedule"])
            t.configure(cfg, self.opts["schedule"])
            self.targets[cfg["mac"]] = t

    def rotate_for(self, t: Target) -> int:
        """Otočení obrazu panelu (jak je pověšený); bez vlastního to z konfigurace add-onu."""
        r = t.cfg.get("rotate")
        return self.opts["rotate"] if r is None else r

    def count(self, t: Target, now: datetime, expect: bool, confirmed: bool, reasons: list[str] = ()) -> None:
        """Probuzení a překreslení za dnešek. Deska překreslí, když dostane jiný Timestamp, než má
        (expect). Jestli to opravdu udělala, pozná server až při dalším probuzení: deska pošle
        lastRefreshDuration jen po překreslení (confirmed). Nesoulad se zaloguje."""
        mac, name = t.cfg["mac"], t.cfg["name"]
        s = self.stats.setdefault(mac, {})
        day = now.date().isoformat()
        if s.get("day") != day:
            s.update(day=day, wakes=0, redraws=0, unchanged=0, missed=0, reasons={})
        prev = s.get("expect")   # co se čekalo při minulém probuzení (None = nevíme, např. první kontakt)
        if confirmed:
            s["redraws"] = s.get("redraws", 0) + 1
            s["last_redraw"] = s.get("expect_at") or now.isoformat(timespec="seconds")
            if prev is False:
                print(f"[device] {name}: překreslila se, i když obsah byl stejný (reset desky?)")
        elif prev:
            s["missed"] = s.get("missed", 0) + 1
            s["last_missed"] = s.get("expect_at")
            print(f"[device] {name}: měla se překreslit ({s.get('expect_at')}), ale nepřekreslila")
        elif prev is False:
            s["unchanged"] = s.get("unchanged", 0) + 1
        if expect:   # důvody překreslení za den (co se změnilo od obsahu na displeji)
            r = s.setdefault("reasons", {})
            for x in reasons:
                r[x] = r.get(x, 0) + 1
            s["last_reason"] = ", ".join(reasons)[:250]
        s["wakes"] = s.get("wakes", 0) + 1
        s["expect"], s["expect_at"] = expect, now.isoformat(timespec="seconds")
        try:
            panels_mod.save_stats(self.stats)
        except OSError as e:
            print(f"[stats] nelze uložit: {e}")

    def track_battery(self, t: Target) -> None:
        """Napětí do historie pro odhad výdrže; při slabé baterii jedno upozornění v HA
        (znovu až po nabití – skok napětí historii i příznak smaže)."""
        mac = t.cfg["mac"]
        h = self.battery.setdefault(mac, {})
        if not battery_mod.add(h, time.time(), t.device.get("voltage")):
            return
        percent = model.battery_percent(t.device.get("voltage"))
        days = battery_mod.estimate(h, time.time())
        if battery_mod.low(percent, days) and not h.get("notified") and self.ha and not MOCK:
            h["notified"] = True
            asyncio.create_task(self.notify_battery(t.cfg, percent, days))
        try:
            battery_mod.save(self.battery)
        except OSError as e:
            print(f"[battery] nelze uložit: {e}")

    async def notify_battery(self, cfg: dict, percent: int | None, days: float | None) -> None:
        n = round(days) if days is not None else None
        left = "" if n is None else f", vydrží ještě asi {n} {'den' if n == 1 else 'dny' if 1 < n < 5 else 'dní'}"
        msg = f"Panel {cfg['name']}: baterie {percent} %{left}. Je čas ji nabít."
        print(f"[battery] {msg}")
        await self.ha.call_service("persistent_notification", "create", {
            "title": "ESPink: slabá baterie", "message": msg,
            "notification_id": mqtt_mod.node(cfg["mac"]) + "_battery"})
        target = self.opts.get("battery_notify") or ""
        if target.startswith("notify."):
            await self.ha.call_service("notify", target.split(".", 1)[1], {"title": "ESPink: slabá baterie", "message": msg})

    def stats_for(self, mac: str) -> dict:
        s = self.stats.get(mac) or {}
        today = s.get("day") == now_local().date().isoformat()
        n = lambda k: s.get(k, 0) if today else 0  # noqa: E731
        days = battery_mod.estimate(self.battery.get(mac) or {}, time.time())
        return {"battery_days": round(days) if days is not None else None,
                "wakes_today": n("wakes"), "redraws_today": n("redraws"), "unchanged_today": n("unchanged"),
                "missed_today": n("missed"), "last_redraw": s.get("last_redraw"), "last_missed": s.get("last_missed"),
                "redraw_reasons": dict(sorted((s.get("reasons") or {}).items(), key=lambda x: -x[1])) if today else {},
                "last_redraw_reason": s.get("last_reason")}

    def save_security(self) -> None:
        try:
            panels_mod.save_security(self.security)
        except OSError as e:
            print(f"[security] nelze uložit {panels_mod.security_path()}: {e}")

    def layout_for(self, t: Target) -> dict:
        return self.layouts.get(t.cfg["layout"]) or next(iter(self.layouts.values()))

    def night_at(self, t: Target, now: datetime) -> datetime | None:
        """Poslední obraz před dlouhým spánkem (noc): čas ranního probuzení, pokud má panel
        rozvržení Na noc. Obraz se pak sestaví k tomuto času – ráno ukáže nový den."""
        if t.cfg.get("night_layout") not in self.layouts:
            return None
        sleep = t.schedule.sleep_seconds(now)
        return now + timedelta(seconds=sleep) if sleep >= NIGHT_SLEEP else None

    def uses_layout(self, t: Target, lid: str) -> bool:
        return self.layout_for(t)["id"] == lid or t.cfg.get("night_layout") == lid

    def target(self, mac: str | None) -> Target:
        return self.targets.get(mac or "") or next(iter(self.targets.values()))

    def save_panels(self, panels: list[dict]) -> None:
        panels_mod.save(panels)
        self.panels = panels
        self.sync_targets()

    def add_panel(self, mac: str, name: str = "", layout: str = "") -> dict:
        taken = {p["name"] for p in self.panels}
        name = layout_mod.clean_name(name) or panels_mod.default_name(mac, taken)
        cfg = panels_mod.sanitize({"mac": mac, "name": name, "layout": layout,
                                   "battery_sensor": panels_mod.suggest_sensor(name)}, self.layouts)
        self.save_panels(self.panels + [cfg])
        print(f"[panels] nový panel {name} ({mac})")
        return cfg

    # ── sestavení obrázku ────────────────────────────────────────────────
    async def rebuild(self, force: bool = False, only: list[Target] | None = None) -> None:
        async with self.lock:
            now = now_local()
            raws: dict[str, dict] = {}  # data z HA jednou pro každé rozvržení
            errors = []
            for t in list(self.targets.values()) if only is None else only:
                night = self.night_at(t, now)
                lay = self.layouts[t.cfg["night_layout"]] if night else self.layout_for(t)
                t.night = bool(night)
                try:
                    if lay["id"] not in raws:
                        raws[lay["id"]] = await self.fetch_raw(lay, now)
                    device = t.device or await self.device_from_sensor(t.cfg["battery_sensor"])
                    # ve vlákně: šablona může běžet až 2 s a smyčka serveru (desky) musí odpovídat dál
                    view = await asyncio.to_thread(model.build, raws[lay["id"]], lay, device, night or now, memory=t.shown)
                    digest = model.digest(view)
                    self.last_build = time.time()
                    if digest == t.view_hash and not force:
                        continue
                    updated = f"{now.day}. {now.month}. {now:%H:%M}"  # %-d nefunguje v musl (Alpine)
                    # odhad; přesná doba se dokreslí, až se deska ozve (handle_device)
                    next_text = after(now, t.schedule.sleep_seconds(now))
                    t.preview, t.payload = await render.render(
                        view, updated, self.rotate_for(t), self.opts["image_format"], next_text)
                    t.view, t.view_hash, t.content_ts = view, digest, int(now.timestamp())
                    t.updated_text, t.next_text, t.error = updated, next_text, ""
                    print(f"[render] {t.cfg['name']}: nový obsah {digest[:8]} ({len(t.payload)} B)")
                except Exception as e:  # noqa: BLE001 – server musí běžet dál i při chybě
                    t.error = f"{now:%H:%M:%S} {type(e).__name__}: {e}"
                    errors.append(f"{t.cfg['name']}: {t.error}" if len(self.targets) > 1 else t.error)
                    print(f"[render] {t.cfg['name']}: chyba: {t.error}")
            if only is None or errors:
                self.last_error = "; ".join(errors)

    async def fetch_raw(self, layout: dict, now: datetime) -> dict:
        needs = layout_mod.needs(layout)
        if MOCK:
            return mock.raw(needs, now)
        raw = await ha_mod.collect(self.ha, needs, now)
        # data pro desky předvyplní mezipaměť editoru – i první náhled je pak bez stahování
        # (ne obraz „Na noc“ sestavený k ránu: kalendáře by byly od jiného dne)
        if abs((now - now_local()).total_seconds()) < 120:
            self.remember_raw(needs, raw, now)
        return raw

    PREVIEW_KINDS = {"states": "states", "calendars": "events", "forecasts": "forecasts"}

    def remember_raw(self, needs: dict, raw: dict, now: datetime) -> None:
        c, t = self.preview_cache, time.time()
        if c.get("day") != now.date():   # kalendáře se stahují od začátku dne
            c.clear()
            c["day"] = now.date()
        for k, rk in self.PREVIEW_KINDS.items():
            for e in needs[k]:
                c[(k, e)] = (t, raw[rk].get(e))

    async def cached(self, key, fetch):
        """Mezipaměť editoru (PREVIEW_TTL s): náhled po každé úpravě nestahuje data znovu."""
        hit = self.preview_cache.get(key)
        if hit and time.time() - hit[0] < PREVIEW_TTL:
            return hit[1]
        value = await fetch()
        self.preview_cache[key] = (time.time(), value)
        return value

    async def fetch_raw_preview(self, layout: dict, now: datetime) -> dict:
        """Data pro náhled v editoru: z HA se stáhne jen to, co v mezipaměti chybí nebo je starší
        než PREVIEW_TTL (nová entita v návrhu se tak stáhne hned)."""
        needs = layout_mod.needs(layout)
        if MOCK:
            return mock.raw(needs, now)
        c, t = self.preview_cache, time.time()
        if c.get("day") != now.date():
            c.clear()
        missing = {k: [e for e in needs[k] if not ((k, e) in c and t - c[(k, e)][0] < PREVIEW_TTL)] for k in self.PREVIEW_KINDS}
        if any(missing.values()):
            self.remember_raw(missing, await ha_mod.collect(self.ha, missing, now), now)
        get = lambda k: {e: c[(k, e)][1] for e in needs[k] if c[(k, e)][1] is not None}  # noqa: E731
        return {"states": get("states"), "events": get("calendars"), "forecasts": get("forecasts")}

    async def device_from_sensor(self, sensor: str) -> dict:
        """Deska se k tomuto serveru zatím nehlásila (DEV vedle stabilního add-onu) –
        napětí a signál pro náhled vzít ze senzoru baterie, který plní stabilní verze."""
        sensor = sensor or self.opts.get("battery_sensor")
        if MOCK or not self.ha or not sensor:
            return {}
        s = await self.ha.state(sensor)
        a = (s or {}).get("attributes", {})
        return {"voltage": model.fnum(a.get("voltage")), "rssi": model.fnum(a.get("rssi"))} if a.get("voltage") else {}

    async def loop(self) -> None:
        # Ve vývoji se při změně šablony hned překreslí (bez čekání na data)
        last_tpl, next_data = template_mtime(), 0.0
        while True:
            tpl = template_mtime() if DEV else last_tpl
            if time.time() >= next_data or tpl != last_tpl:
                await self.rebuild(force=tpl != last_tpl)
                next_data, last_tpl = time.time() + self.opts["render_interval"], tpl
            await asyncio.sleep(1 if DEV else self.opts["render_interval"])

    # ── zařízení ─────────────────────────────────────────────────────────
    async def handle_device(self, request: web.Request) -> web.Response:
        if (request.content_length or 0) > DEVICE_MAX_BODY:  # deska posílá ~1 kB JSON
            return web.Response(status=413)
        raw = await request.content.read(DEVICE_MAX_BODY + 1)   # i při přenosu po částech (bez Content-Length)
        if len(raw) > DEVICE_MAX_BODY:
            return web.Response(status=413)
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, RecursionError):  # nevalidní JSON, UTF-8, zanoření – bez tracebacku v logu
            body = {}
        if not isinstance(body, dict):
            body = {}
        part = lambda k: body.get(k) if isinstance(body.get(k), dict) else {}  # noqa: E731
        system, network, display = part("system"), part("network"), part("display")
        check = request.query.get("timestampCheck", "1")
        mac = options.normalize_mac(network.get("mac"))
        api_key = panels_mod.clean_key(request.headers.get("X-API-Key"))
        async with self.auth_lock:  # souběžné požadavky nesmí obejít jednorázové povolení
            ok = await self.device_allowed(mac, request.remote, body, api_key)
        if not ok:
            return web.Response(status=403)
        if mac not in self.targets:  # povolená deska bez panelu (např. MAC zapsaná v Nastavení)
            self.add_panel(mac)
        t = self.targets[mac]
        now = now_local()
        sleep = t.schedule.sleep_seconds(now)

        if check == "1":
            old_bucket = model._device(t.device)["battery"]
            prev = t.device
            t.device = {
                "voltage": model.fnum(system.get("vccVoltage")),
                "rssi": model.fnum(network.get("rssi")),
                "mac": mac,
                "ip": request.remote,  # skutečná adresa spojení, ne hodnota z JSONu
                "fw": short_text(body.get("fwVersion")),
                # deska posílá délku překreslení jen po probuzení, které následuje po překreslení
                # (potvrzení pro count) – jinak ponechat poslední známou hodnotu
                # (≈20 000 ms = vypršel limit ovladače panelu)
                "refresh_ms": ms(display.get("lastRefreshDuration")) or prev.get("refresh_ms"),
                "download_ms": ms(network.get("lastDownloadDuration")) or prev.get("download_ms"),
                "cpu_temp": model.fnum(system.get("cpuTemp")),
                "reset_reason": short_text(system.get("resetReason")),
                "last_seen": now.isoformat(timespec="seconds"),
                "sleep": sleep,
            }
            # Změna baterie o celý krok (5 %) se má projevit hned v tomto probuzení
            # (nejvýš jednou za 30 s – jinak by šlo střídáním hodnot vynutit vykreslení při každém požadavku)
            # Na noc: poslední probuzení před dlouhým spánkem dostane noční obraz hned (ne až v dalším přepočtu)
            if not t.payload or bool(self.night_at(t, now)) != t.night or (
                    model._device(t.device)["battery"] != old_bucket and time.time() - t.last_device_rebuild > 30):
                t.last_device_rebuild = time.time()
                await self.rebuild(only=[t])

        if not t.payload:
            await self.rebuild(force=True, only=[t])
        if check == "1":
            # Deska překresluje, když dostane jiný Timestamp, než má – obrázek si přečte rovnou
            # z této odpovědi (druhý požadavek s timestampCheck=0 posílá jen stránkovaný režim)
            redraw = t.content_ts != t.served_ts
            reasons = model.change_reasons(t.served_view, t.view) if redraw else []
            self.count(t, now, redraw, confirmed=bool(ms(display.get("lastRefreshDuration"))), reasons=reasons)
            self.track_battery(t)
            await self.stamp_next_wake(t, now, sleep)
            asyncio.create_task(self.publish_device(t, now, sleep))
            print(f"[device] {t.cfg['name']} {t.device['ip']} {t.device['voltage']} V,"
                  f" {t.device['rssi']} dBm, spánek {sleep} s{', překreslí – ' + ', '.join(reasons) if redraw else ''}")
        headers = {
            "Timestamp": str(t.content_ts),
            "PreciseSleep": str(sleep),
            "Content-Type": "image/png" if self.opts["image_format"] == "png" else "application/octet-stream",
            "Connection": "close",
        }
        return web.Response(body=t.payload, headers=headers)

    async def stamp_next_wake(self, t: Target, now: datetime, sleep: int) -> None:
        """Deska bude překreslovat => dokreslit do patičky přesnou dobu do další obnovy.
        Obsah (a Timestamp) se nemění, takže to nevyvolá žádné překreslení navíc."""
        if t.content_ts != t.served_ts and t.view:
            wanted = after(now, sleep)
            if wanted != t.next_text:
                async with self.lock:
                    try:
                        t.preview, t.payload = await render.render(
                            t.view, t.updated_text, self.rotate_for(t), self.opts["image_format"], wanted)
                        t.next_text = wanted
                    except Exception as e:  # noqa: BLE001 – raději starý odhad než nic
                        print(f"[render] patička: {e}")
        t.served_ts, t.served_view = t.content_ts, t.view

    def blocked(self, *who: str) -> bool:
        """Příliš mnoho chybných pokusů z MAC nebo IP (hádání klíče) → na čas odmítat bez dalšího."""
        now = time.time()
        for w in who:
            if w and len([t for t in self.failures.get(w, []) if now - t < FAIL_WINDOW]) >= (FAIL_MAX_MAC if ":" in w and len(w) == 17 else FAIL_MAX_IP):
                return True
        return False

    def fail(self, *who: str) -> None:
        now = time.time()
        for w in who:
            if w:
                self.failures[w] = [t for t in self.failures.get(w, []) if now - t < FAIL_WINDOW] + [now]
        if len(self.failures) > 500:   # paměť: zapomenout nejstarší
            for k in sorted(self.failures, key=lambda k: self.failures[k][-1])[:250]:
                del self.failures[k]

    async def device_allowed(self, mac: str | None, ip: str | None, body: dict, api_key: str) -> bool:
        """Jen zařízení ze seznamu allowed_devices se známým klíčem (X-API-Key). Úplně první
        deska se smí zapsat sama (trust on first use) – jen jednou za život instalace.
        Ostatní (i deska se změněným klíčem, např. po smazání flash) čekají na povolení.
        Chybné pokusy se počítají – po několika se MAC/IP na čas odmítá (hádání klíče)."""
        allowed = self.opts.get("allowed_devices") or []
        keys = self.security["keys"]
        # pořadí: blokace IP (hádání z jedné adresy) → správný klíč povolené desky → blokace MAC.
        # Správný klíč projde i při blokaci MAC – jinak by šlo povolenou desku z jiné IP odříznout.
        if self.blocked(ip):
            return False
        if mac and mac in allowed and api_key and keys.get(mac) == api_key:
            return True
        if self.blocked(mac):
            self.fail(ip)   # další pokusy na zablokovanou MAC se počítají IP (jinak by hádání pokračovalo)
            return False
        if mac and mac in allowed:
            known = keys.get(mac)
            if known and known == api_key:
                return True
            # deska povolená před verzí s klíči: klíč se naučí při prvním spojení, ale jen v přechodném
            # období po aktualizaci (jinak by si ho mohl „zabrat“ kdokoli, kdo zná MAC)
            if not known and api_key and time.time() < self.security.get("learn_until", 0):
                keys[mac] = api_key
                self.save_security()
                print(f"[security] zapamatován klíč desky {mac}")
                return True
            reason = "key"
            self.fail(mac, ip)
        elif mac and not allowed and not self.panels and not self.security["tofu_done"] and api_key:
            try:
                await self.allow(mac, api_key)
                print(f"[security] první zařízení {mac} ({ip}) zapsáno mezi povolená")
                return True
            except Exception as e:  # noqa: BLE001
                print(f"[security] nelze uložit povolené zařízení: {e}")
                return False
        else:
            reason = "new"
            self.fail(ip)
        key = mac or "(bez MAC)"
        seen = self.pending.get(key)
        if not seen or time.time() - seen["t"] > 600:
            print(f"[security] odmítnuto zařízení {key} z {ip}" + (" – nesouhlasí klíč" if reason == "key" else ""))
        # klíč čekající desky nepřepisuje požadavek z jiné IP (podvržená „přeinstalovaná“ deska)
        keep = seen and seen.get("key") and seen.get("ip") != ip and time.time() - seen["t"] < 3600
        system = body.get("system") if isinstance(body.get("system"), dict) else {}
        entry = {"mac": key, "ip": seen["ip"] if keep else ip, "t": time.time(), "when": now_local().strftime("%d. %m. %H:%M:%S"),
                 "fw": short_text(body.get("fwVersion")), "voltage": model.fnum(system.get("vccVoltage")),
                 "reason": reason, "key": seen["key"] if keep else (api_key or (seen or {}).get("key", ""))}
        entry["key_hint"] = entry["key"][-3:] if entry["key"] else ""
        self.pending[key] = entry
        # nejvýš 3 záznamy z jedné IP a 10 celkem – náhodné MAC nevytlačí skutečnou novou desku
        from_ip = sorted((k for k, p in self.pending.items() if p["ip"] == ip), key=lambda k: self.pending[k]["t"])
        for k in from_ip[:-3]:
            del self.pending[k]
        if len(self.pending) > 10:
            del self.pending[min(self.pending, key=lambda k: self.pending[k]["t"])]
        return False

    async def allow(self, mac: str, api_key: str = "") -> None:
        allowed = self.opts.get("allowed_devices") or []
        if mac not in allowed:
            new = dict(self.opts, allowed_devices=allowed + [mac])
            await self.persist_options(new)
            self.opts = new
        if api_key:
            self.security["keys"][mac] = api_key
        self.security["tofu_done"] = True
        self.save_security()
        self.pending.pop(mac, None)
        self.failures.pop(mac, None)

    async def persist_options(self, new: dict) -> None:
        if self.ha and self.ha.base.startswith("http://supervisor"):
            await self.ha.save_addon_options(new)
        else:  # vývoj mimo HA
            with open(os.environ.get("OPTIONS_PATH", "options.json"), "w", encoding="utf-8") as f:
                json.dump(new, f, ensure_ascii=False, indent=2)

    async def publish_device(self, t: Target, now: datetime, sleep: int) -> None:
        if MOCK or not self.ha:
            return
        # nejvýš jeden zápis do HA za minutu
        if time.time() - t.last_publish < 60:
            return
        t.last_publish = time.time()
        d = t.device
        wake = datetime.fromtimestamp(now.timestamp() + sleep).astimezone()
        state = {
            "battery": model.battery_percent(d.get("voltage")), "voltage": d.get("voltage"), "rssi": d.get("rssi"),
            "refresh_s": round(d["refresh_ms"] / 1000, 1) if d.get("refresh_ms") else None,
            "cpu_temp": d.get("cpu_temp"), "last_seen": d.get("last_seen"),
            "next_wake": wake.isoformat(timespec="seconds"),
            # důvod posledního restartu desky („brownout“ = pokles napětí, např. při překreslení)
            "reset_reason": d.get("reset_reason"), "ip": d.get("ip"), "mac": d.get("mac"), "firmware": d.get("fw"),
            **self.stats_for(t.cfg["mac"]),
        }
        if self.mqtt:
            await self.announce(t.cfg, d.get("fw"))
            self.mqtt.publish(mqtt_mod.state_topic(t.cfg["mac"]), state)
        elif t.cfg["battery_sensor"]:  # bez MQTT: stav přes REST API (po restartu HA zmizí)
            await self.ha.set_state(t.cfg["battery_sensor"], state["battery"] if state["battery"] is not None else "unknown", {
                "unit_of_measurement": "%", "device_class": "battery", "state_class": "measurement",
                "friendly_name": f"{t.cfg['name']} baterie", **{k: v for k, v in state.items() if k not in ("battery", "refresh_s")},
                "refresh_ms": d.get("refresh_ms"),
            })

    # ── MQTT discovery ───────────────────────────────────────────────────
    async def announce(self, cfg: dict, fw: str | None = None) -> None:
        """Zařízení panelu v HA (retained konfigurace) – odešle se jen při změně (název, firmware…)."""
        if not self.mqtt or not cfg["mac"]:
            return
        await self.drop_legacy_state(cfg)
        fw = fw or (self.targets[cfg["mac"]].device.get("fw") if cfg["mac"] in self.targets else None)
        msg = mqtt_mod.discovery(cfg, fw)
        key = json.dumps(msg, sort_keys=True, ensure_ascii=False)
        if self.announced.get(cfg["mac"]) != key:
            self.mqtt.publish(mqtt_mod.config_topic(cfg["mac"]), msg)
            self.announced[cfg["mac"]] = key
            print(f"[mqtt] zařízení {msg['dev']['name']} ohlášeno")

    async def drop_legacy_state(self, cfg: dict) -> None:
        """Senzor baterie zapsaný dřív přes REST (s atributem voltage) by blokoval stejné
        entity ID pro MQTT entitu – před prvním ohlášením ho smazat (jen ze stavů, není v registru)."""
        sensor = cfg["battery_sensor"]
        if not sensor or sensor in self.legacy_checked or not self.ha:
            return
        self.legacy_checked.add(sensor)
        try:
            s = await self.ha.state(sensor)
            a = (s or {}).get("attributes", {})
            if {"voltage", "last_seen", "next_wake"} <= a.keys():  # jen stav ve tvaru, který zapisoval add-on
                await self.ha.delete_state(sensor)
                print(f"[mqtt] smazán starý stav {sensor} zapsaný přes REST")
        except Exception as e:  # noqa: BLE001
            print(f"[mqtt] nelze zkontrolovat {sensor}: {e}")

    async def announce_all(self) -> None:
        self.announced.clear()  # po (znovu)připojení poslat vše – broker nemusí retained zprávy uchovat
        for p in self.panels:
            await self.announce(p)

    def forget(self, mac: str) -> None:
        """Odebraný panel: smazat zařízení z HA (prázdná retained konfigurace) i poslední stav."""
        if self.mqtt:
            self.mqtt.publish(mqtt_mod.config_topic(mac), "")
            self.mqtt.publish(mqtt_mod.state_topic(mac), "")
        self.announced.pop(mac, None)

    # ── UI (jen přes Ingress) ────────────────────────────────────────────
    @web.middleware
    async def ingress_only(self, request: web.Request, handler):
        if request.path != "/index.php":
            if DEV:  # lokální vývoj: jen localhost (ochrana proti DNS rebindingu z prohlížeče)
                if (request.host or "").rsplit(":", 1)[0] not in ("localhost", "127.0.0.1", "[::1]"):
                    raise web.HTTPForbidden(text="DEV server: jen přes localhost")
            elif request.remote != INGRESS_IP:
                raise web.HTTPForbidden(text="Pouze přes panel v Home Assistantu")
        return await handler(request)

    async def handle_index(self, request: web.Request) -> web.Response:
        return web.FileResponse(render.APP_DIR / "templates" / "status.html", headers={"Cache-Control": "no-store"})

    def ts_key(self) -> str:
        return ",".join(str(t.content_ts) for t in self.targets.values())

    async def handle_ts(self, request: web.Request) -> web.Response:
        return web.Response(text=self.ts_key(), headers={"Cache-Control": "no-store"})

    async def handle_preview(self, request: web.Request) -> web.Response:
        t = self.target(request.query.get("panel"))
        if not t.preview:
            await self.rebuild(force=True, only=[t])
        return web.Response(body=t.preview, content_type="image/png", headers={"Cache-Control": "no-store"})

    async def handle_view(self, request: web.Request) -> web.Response:
        t = self.target(request.query.get("panel"))
        return web.json_response({"panel": t.cfg, "view": t.view, "device": t.device, "hash": t.view_hash},
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=2, default=str))

    # ── stránky (jen přes Ingress) ───────────────────────────────────────
    async def handle_editor(self, request: web.Request) -> web.Response:
        return web.FileResponse(render.APP_DIR / "templates" / "editor.html",
                                headers={"Cache-Control": "no-store"})

    async def handle_panels_page(self, request: web.Request) -> web.Response:
        return web.FileResponse(render.APP_DIR / "templates" / "panels.html",
                                headers={"Cache-Control": "no-store"})

    async def handle_entities(self, request: web.Request) -> web.Response:
        if MOCK:
            ids = set()
            for lay in self.layouts.values():
                raw = mock.raw(layout_mod.needs(lay), now_local())
                ids |= set(raw["states"]) | set(raw["events"])
            ids |= {"sensor.temperature_10", "weather.home"}
            return web.json_response([{"id": i, "name": i.split(".")[1].replace("_", " "), "unit": ""} for i in sorted(ids)])
        try:
            return web.json_response(await self.ha.entities())
        except Exception as e:  # noqa: BLE001
            return web.json_response({"error": str(e)}, status=502)

    # ── rozvržení (editor) ───────────────────────────────────────────────
    def layout_list(self) -> list[dict]:
        return [{"id": lid, "name": lay["name"], "panels": [p["name"] for p in self.panels if p["layout"] == lid]}
                for lid, lay in self.layouts.items()]

    def layout_arg(self, request: web.Request) -> dict:
        lid = request.query.get("id")
        if lid and lid not in self.layouts:
            raise web.HTTPNotFound(text=json.dumps({"error": "rozvržení neexistuje"}, ensure_ascii=False),
                                   content_type="application/json")
        return self.layouts.get(lid) or next(iter(self.layouts.values()))

    async def handle_layout(self, request: web.Request) -> web.Response:
        return web.json_response({"layout": self.layout_arg(request), "layouts": self.layout_list(),
                                  "saved": self.layout_saved, "error": self.layout_error,
                                  "schema": layout_mod.schema()},
                                 headers={"Cache-Control": "no-store"})

    @staticmethod
    async def read_json(request: web.Request) -> dict:
        if request.content_type != "application/json":  # ochrana proti CSRF
            raise web.HTTPUnsupportedMediaType(text="očekávám application/json")
        try:
            data = await request.json()
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            raise bad_request("očekávám objekt JSON")
        return data

    async def read_layout(self, request: web.Request, current: dict) -> dict:
        data = await self.read_json(request)
        try:  # ID a název se mění zvlášť (vytvořit / přejmenovat), návrh nese jen bloky a revizi
            return layout_mod.sanitize(dict(data, id=current["id"], name=current["name"]))
        except (ValueError, TypeError) as e:
            raise bad_request(str(e))

    async def handle_layout_save(self, request: web.Request) -> web.Response:
        current = self.layout_arg(request)
        new = await self.read_layout(request, current)
        # Ochrana proti přepsání změn z jiné karty: návrh musí vycházet z aktuální revize
        if new["rev"] != current.get("rev", 0) and request.query.get("force") != "1":
            return web.json_response({"error": "Rozvržení bylo mezitím uloženo jinde.", "conflict": True,
                                      "layout": current}, status=409)
        try:
            new = layout_mod.save(self.layouts, new, current if self.layout_saved else None)
        except OSError as e:
            return web.json_response({"error": f"nelze uložit: {e}"}, status=500)
        self.layouts[new["id"]] = new
        self.layout_saved, self.layout_error = True, ""
        await self.rebuild(force=True, only=[t for t in self.targets.values() if self.uses_layout(t, new["id"])])
        print(f"[layout] rozvržení {new['name']} uloženo")
        return web.json_response({"layout": new, "error": self.last_error or None})

    async def write_layouts(self, layouts: dict) -> None:
        try:
            layout_mod.write_all(layouts)
        except OSError as e:
            raise web.HTTPInternalServerError(text=json.dumps({"error": f"nelze uložit: {e}"}, ensure_ascii=False),
                                              content_type="application/json")
        self.layouts, self.layout_saved = layouts, True

    def check_layout_name(self, name: str, own_id: str = "") -> str:
        name = layout_mod.clean_name(name)
        if not name:
            raise bad_request("rozvržení musí mít název")
        if any(lay["name"].casefold() == name.casefold() and lid != own_id for lid, lay in self.layouts.items()):
            raise bad_request(f"rozvržení s názvem {name} už existuje")
        return name

    async def handle_layout_create(self, request: web.Request) -> web.Response:
        data = await self.read_json(request)
        name = self.check_layout_name(data.get("name"))
        src = self.layouts.get(data.get("copy") or "")
        lid = layout_mod.new_id(self.layouts)
        new = dict(src, id=lid, name=name, rev=0) if src else layout_mod.sanitize({"id": lid, "name": name, "blocks": []})
        await self.write_layouts({**self.layouts, lid: new})
        print(f"[layout] nové rozvržení {name}" + (f" (kopie {src['name']})" if src else ""))
        return web.json_response({"layout": new, "layouts": self.layout_list()})

    async def handle_layout_rename(self, request: web.Request) -> web.Response:
        cur = self.layout_arg(request)
        name = self.check_layout_name((await self.read_json(request)).get("name"), cur["id"])
        await self.write_layouts({**self.layouts, cur["id"]: dict(cur, name=name)})
        self.sync_targets()
        return web.json_response({"layout": self.layouts[cur["id"]], "layouts": self.layout_list()})

    async def handle_layout_delete(self, request: web.Request) -> web.Response:
        await self.read_json(request)  # jen kvůli kontrole Content-Type (CSRF)
        cur = self.layout_arg(request)
        users = [p["name"] for p in self.panels if cur["id"] in (p["layout"], p.get("night_layout"))]
        if users:
            raise bad_request(f"rozvržení používá panel {', '.join(users)} – nejdřív mu přiřaď jiné")
        if len(self.layouts) == 1:
            raise bad_request("poslední rozvržení nejde smazat")
        await self.write_layouts({k: v for k, v in self.layouts.items() if k != cur["id"]})
        self.sync_targets()
        print(f"[layout] rozvržení {cur['name']} smazáno")
        return web.json_response({"layouts": self.layout_list()})

    async def handle_layout_preview(self, request: web.Request) -> web.Response:
        """Náhled neuloženého rozvržení – stejný renderer jako pro desku (přesné pixely).
        Baterie a rozvrh z panelu, který rozvržení používá."""
        current = self.layout_arg(request)
        draft = await self.read_layout(request, current)
        users = [t for t in self.targets.values() if self.layout_for(t)["id"] == current["id"]]
        t = users[0] if users else self.target(None)
        async with self.draft_lock:  # náhledy jeden po druhém
            now = now_local()
            try:
                raw = await self.fetch_raw_preview(draft, now)
                device = t.device or await self.cached(("device", t.cfg["battery_sensor"]),
                                                       lambda: self.device_from_sensor(t.cfg["battery_sensor"]))
                view = await asyncio.to_thread(model.build, raw, draft, device, now, worst=request.query.get("worst") == "1")
                png = await render.render_preview(view, f"{now.day}. {now.month}. {now:%H:%M}",
                                                  after(now, t.schedule.sleep_seconds(now)))
            except Exception as e:  # noqa: BLE001
                return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=502)
        return web.Response(body=png, content_type="image/png", headers={
            "Cache-Control": "no-store"})

    async def handle_history(self, request: web.Request) -> web.Response:
        cur = self.layout_arg(request)
        items = []
        for it in layout_mod.history(cur["id"]):  # starší verze (i bloky pod sebou) převést na aktuální tvar
            try:
                items.append({"rev": it.get("rev", 0), "layout": layout_mod.sanitize(dict(it["layout"], id=cur["id"]))})
            except (ValueError, TypeError, KeyError):
                continue
        return web.json_response(items, headers={"Cache-Control": "no-store"})

    async def handle_refresh(self, request: web.Request) -> web.Response:
        if request.content_type != "application/json":  # ochrana proti CSRF
            return web.json_response({"error": "očekávám application/json"}, status=415)
        await self.rebuild(force=True)
        return web.json_response({"ok": True})

    # ── panely (editor) ──────────────────────────────────────────────────
    def panels_json(self) -> dict:
        allowed = set(self.opts.get("allowed_devices") or [])
        out = []
        for p in self.panels:
            t = self.targets.get(p["mac"])
            d = t.device if t else {}
            now = now_local()
            nxt = None
            if d.get("last_seen") and d.get("sleep"):
                nxt = (datetime.fromisoformat(d["last_seen"]) + timedelta(seconds=d["sleep"])).isoformat(timespec="seconds")
            out.append({**p, "allowed": p["mac"] in allowed, "content_ts": t.content_ts if t else 0,
                        "error": t.error if t else "", "size": len(t.payload) if t else 0,
                        "interval_now": t.schedule.interval_at(now) if t else None, "next_wake": nxt,
                        "rotate_used": self.rotate_for(t) if t else None, "stats": self.stats_for(p["mac"]),
                        "device": {**d, "battery": model.battery_percent(d.get("voltage"))} if d else {}})
        known = {p["mac"] for p in self.panels}
        # povolené MAC bez panelu (zapsané ručně v Konfiguraci add-onu) – panel vznikne, až se deska ozve
        # čekající: nové desky a známé desky s jiným klíčem (přeinstalované); klíč do UI neposílat
        pend = [{k: v for k, v in p.items() if k != "key"} for p in self.pending.values()
                if p["mac"] not in known or p.get("reason") == "key"]
        # ručně zapsaná MAC, která se už ozvala (čeká na povolení klíče), se ukáže jen jednou
        waiting = [{"mac": m, "allowed": True} for m in sorted(allowed - known - {p["mac"] for p in pend})]
        return {"panels": out, "pending": pend + waiting,
                "layouts": self.layout_list(), "default_schedule": self.opts["schedule"], "mqtt": bool(self.mqtt),
                "default_rotate": self.opts["rotate"], "image_format": self.opts["image_format"],
                "allowed_devices": sorted(allowed), "content_ts": self.ts_key()}

    async def handle_panels(self, request: web.Request) -> web.Response:
        return web.json_response(self.panels_json(), headers={"Cache-Control": "no-store"})

    async def handle_panel_save(self, request: web.Request) -> web.Response:
        data = await self.read_json(request)
        mac = options.normalize_mac(data.get("mac"))
        if not any(p["mac"] == mac for p in self.panels):
            raise bad_request("panel neexistuje")
        try:
            cfg = panels_mod.sanitize(data, self.layouts)
        except (ValueError, TypeError) as e:
            raise bad_request(str(e))
        if not isinstance(data.get("layout"), str) or data["layout"] not in self.layouts:
            raise bad_request("rozvržení neexistuje")
        if any(p["name"] == cfg["name"] and p["mac"] != mac for p in self.panels):
            raise bad_request(f"panel {cfg['name']} už existuje")
        old_rotate = self.rotate_for(self.targets[mac])
        self.save_panels([cfg if p["mac"] == mac else p for p in self.panels])
        await self.announce(cfg)  # nový název zařízení v HA
        # obrázek (a tím překreslení desky) se změní, jen když se změní obsah – např. jiné rozvržení;
        # jiné otočení mění jen data pro desku (model zobrazení stejný) -> vynutit
        await self.rebuild(force=self.rotate_for(self.targets[mac]) != old_rotate, only=[self.targets[mac]])
        print(f"[panels] panel {cfg['name']} uložen")
        return web.json_response(self.panels_json())

    async def handle_panel_approve(self, request: web.Request) -> web.Response:
        """Povolit odmítnutou desku a založit pro ni panel."""
        data = await self.read_json(request)
        mac = options.normalize_mac(data.get("mac"))
        if not mac:
            raise bad_request("neplatná MAC adresa")
        exists = any(p["mac"] == mac for p in self.panels)
        if not exists and any(p["name"] == layout_mod.clean_name(data.get("name")) for p in self.panels):
            raise bad_request(f"panel {data.get('name')} už existuje")
        api_key = (self.pending.get(mac) or {}).get("key", "")  # z posledního odmítnutého požadavku desky
        if not api_key:
            raise bad_request("deska se zatím neozvala se svým klíčem – povol ji, až se znovu probudí")
        try:
            await self.allow(mac, api_key)
        except Exception as e:  # noqa: BLE001
            return web.json_response({"error": f"nelze uložit povolená zařízení: {e}"}, status=500)
        if not any(p["mac"] == mac for p in self.panels):
            await self.announce(self.add_panel(mac, data.get("name"), data.get("layout")))
        await self.rebuild(only=[self.targets[mac]])
        return web.json_response(self.panels_json())

    async def handle_panel_delete(self, request: web.Request) -> web.Response:
        """Odebrat panel a zakázat jeho desku."""
        mac = options.normalize_mac((await self.read_json(request)).get("mac"))
        allowed = self.opts.get("allowed_devices") or []
        try:
            if mac in allowed:
                new = dict(self.opts, allowed_devices=[m for m in allowed if m != mac])
                await self.persist_options(new)
                self.opts = new
        except Exception as e:  # noqa: BLE001
            return web.json_response({"error": f"nelze uložit povolená zařízení: {e}"}, status=500)
        if self.security["keys"].pop(mac, None):
            self.save_security()
        self.save_panels([p for p in self.panels if p["mac"] != mac])
        self.forget(mac)
        print(f"[panels] panel {mac} odebrán")
        return web.json_response(self.panels_json())


async def detect_server(srv: "Server") -> dict:
    """Adresa HA v místní síti a port add-onu od Supervisoru (pro výchozí nastavení firmwaru)."""
    host, port = "", 8099
    try:
        net = await srv.ha.supervisor("/network/info") or {}
        for iface in net.get("interfaces", []):
            addrs = (iface.get("ipv4") or {}).get("address") or []
            if addrs and (iface.get("primary") or not host):
                host = addrs[0].split("/")[0]
        info = await srv.ha.supervisor("/addons/self/info") or {}
        port = int((info.get("network") or {}).get("8099/tcp") or port)
    except Exception as e:  # noqa: BLE001
        print(f"[firmware] nelze zjistit adresu HA: {e}")
    if not host and srv.ha and not srv.ha.base.startswith("http://supervisor"):
        host = srv.ha.base.split("//")[1].split(":")[0].split("/")[0]  # vývoj: HA_URL
    return {"host": host, "port": port}


class Installer:
    """Webový instalátor firmwaru (ESP Web Tools) – stránka, manifest a části obrazu."""

    def __init__(self, srv: "Server"):
        self.srv = srv

    async def settings(self) -> dict:
        s = firmware_mod.load_settings()
        detected = await detect_server(self.srv)
        return {"host": s.get("host") or detected["host"], "port": s.get("port") or detected["port"],
                "ap_password": s["ap_password"], "detected": detected}

    async def page(self, request: web.Request) -> web.Response:
        return web.FileResponse(render.APP_DIR / "templates" / "install.html", headers={"Cache-Control": "no-store"})

    async def info(self, request: web.Request) -> web.Response:
        return web.json_response({"available": firmware_mod.available(), "build": firmware_mod.build_info(),
                                  "settings": await self.settings()}, headers={"Cache-Control": "no-store"})

    async def save(self, request: web.Request) -> web.Response:
        data = await Server.read_json(request)
        try:
            cfg = firmware_mod.check(data.get("host"), data.get("port"), data.get("ap_password"))
        except (ValueError, TypeError) as e:
            raise bad_request(str(e))
        firmware_mod.save_settings(cfg)
        return await self.info(request)

    async def manifest(self, request: web.Request) -> web.Response:
        b = firmware_mod.build_info()
        return web.json_response(firmware_mod.manifest(f"{b.get('firmware', '?')} ({b.get('built', '')[:10]})"),
                                 headers={"Cache-Control": "no-store"})

    async def part(self, request: web.Request) -> web.Response:
        s = await self.settings()
        try:
            cfg = firmware_mod.check(s["host"], s["port"], s["ap_password"])
            data = firmware_mod.part(request.match_info["chip"], request.match_info["name"], cfg)
        except FileNotFoundError:
            raise web.HTTPNotFound()
        except ValueError as e:
            raise bad_request(f"nastavení firmwaru: {e}")
        if request.match_info["name"] == "firmware.bin":
            print(f"[firmware] stažení pro instalaci ({request.match_info['chip']}): server {cfg['host']}:{cfg['port']}")
        return web.Response(body=data, content_type="application/octet-stream", headers={"Cache-Control": "no-store"})


DEVICE_MAX_BODY = 16 * 1024
LEARN_DAYS = 14                          # přechodné období učení klíčů dříve povolených desek
NIGHT_SLEEP = 3 * 3600                   # spánek aspoň 3 h = noc: obraz z rozvržení Na noc
FAIL_WINDOW, FAIL_MAX_MAC, FAIL_MAX_IP = 900, 5, 20   # chybné pokusy za 15 min → dočasné odmítání


def ms(value) -> int | None:
    """Doba v ms od desky (překreslení, stažení): číslo v rozumném rozsahu, jinak nic."""
    v = model.fnum(value)
    return int(v) if v is not None and 0 < v <= 600_000 else None


def short_text(value, limit: int = 40) -> str | None:
    """Text od desky (verze FW, důvod restartu): zkrátit a bez řídicích znaků (log, HA, MQTT)."""
    if value is None:
        return None
    return "".join(ch for ch in str(value) if ch.isprintable())[:limit]


def bad_request(text: str) -> web.HTTPBadRequest:
    return web.HTTPBadRequest(text=json.dumps({"error": text}, ensure_ascii=False), content_type="application/json")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", metavar="PNG", help="jen vykreslit náhled do souboru a skončit")
    p.add_argument("--port", type=int, default=8099)
    p.add_argument("--host", default="0.0.0.0", help="adresa pro naslouchání (vývoj: 127.0.0.1)")
    args = p.parse_args()

    srv = Server(options.load())
    async with aiohttp.ClientSession() as session:
        srv.ha = ha_mod.HomeAssistant(session)
        if not args.once and not MOCK:
            loop = asyncio.get_running_loop()
            srv.mqtt = await mqtt_mod.connect(srv.ha, lambda: asyncio.run_coroutine_threadsafe(srv.announce_all(), loop))
        if args.once:
            await srv.rebuild(force=True)
            if srv.last_error:
                raise SystemExit(srv.last_error)
            with open(args.once, "wb") as f:
                f.write(srv.target(None).preview)
            print(f"náhled: {args.once}, data pro zařízení: {len(srv.target(None).payload)} B")
            return

        inst = Installer(srv)
        app = web.Application(middlewares=[srv.ingress_only])
        app.add_routes([
            web.post("/index.php", srv.handle_device),
            web.get("/", srv.handle_editor),
            web.get("/status", srv.handle_index),
            web.get("/preview.png", srv.handle_preview),
            web.get("/ts", srv.handle_ts),
            web.get("/view.json", srv.handle_view),
            web.post("/refresh", srv.handle_refresh),
            web.get("/entities.json", srv.handle_entities),
            web.get("/layout.json", srv.handle_layout),
            web.static("/static", render.STATIC_DIR),
            web.post("/layout", srv.handle_layout_save),
            web.post("/layout/preview", srv.handle_layout_preview),
            web.get("/layout/history.json", srv.handle_history),
            web.post("/layouts/create", srv.handle_layout_create),
            web.post("/layouts/rename", srv.handle_layout_rename),
            web.post("/layouts/delete", srv.handle_layout_delete),
            web.get("/panels", srv.handle_panels_page),
            web.get("/panels.json", srv.handle_panels),
            web.post("/panels/save", srv.handle_panel_save),
            web.post("/panels/approve", srv.handle_panel_approve),
            web.post("/panels/delete", srv.handle_panel_delete),
            web.get("/install", inst.page),
            web.get("/firmware/info.json", inst.info),
            web.post("/firmware/settings", inst.save),
            web.get("/firmware/manifest.json", inst.manifest),
            web.get(r"/firmware/{chip:esp32s3|esp32}/{name:[a-z_0-9]+\.bin}", inst.part),
        ])
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, args.host, args.port).start()
        print(f"[server] naslouchám na :{args.port} (DEV={DEV}, MOCK={MOCK}), HA API: {srv.ha.base},"
              f" časové pásmo: {now_local().tzname()}")
        await srv.loop()


if __name__ == "__main__":
    asyncio.run(main())
