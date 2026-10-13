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
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

import ha as ha_mod
import layout as layout_mod
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
        self.error = ""
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
        self.lock = asyncio.Lock()
        self.ha: ha_mod.HomeAssistant | None = None
        self.mqtt: mqtt_mod.Mqtt | None = None
        self.announced: dict[str, str] = {}   # MAC -> poslední odeslaná konfigurace discovery
        self.legacy_checked: set[str] = set()
        self.last_error = ""
        self.last_build = 0.0
        # bezpečnost: omezení rychlosti a odmítnutá zařízení (čekají na povolení v editoru)
        self.pending: dict[str, dict] = {}

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

    def layout_for(self, t: Target) -> dict:
        return self.layouts.get(t.cfg["layout"]) or next(iter(self.layouts.values()))

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
                lay = self.layout_for(t)
                try:
                    if lay["id"] not in raws:
                        raws[lay["id"]] = await self.fetch_raw(lay, now)
                    device = t.device or await self.device_from_sensor(t.cfg["battery_sensor"])
                    view = model.build(raws[lay["id"]], lay, device, now)
                    digest = model.digest(view)
                    self.last_build = time.time()
                    if digest == t.view_hash and not force:
                        continue
                    updated = f"{now.day}. {now.month}. {now:%H:%M}"  # %-d nefunguje v musl (Alpine)
                    # odhad; přesná doba se dokreslí, až se deska ozve (handle_device)
                    next_text = after(now, t.schedule.sleep_seconds(now))
                    t.preview, t.payload = await render.render(
                        view, updated, self.opts["rotate"], self.opts["image_format"], next_text)
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
        return mock.raw(needs, now) if MOCK else await ha_mod.collect(self.ha, needs, now)

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
        try:
            body = await request.json() if request.can_read_body else {}
        except (json.JSONDecodeError, aiohttp.ContentTypeError):
            body = {}
        if not isinstance(body, dict):
            body = {}
        part = lambda k: body.get(k) if isinstance(body.get(k), dict) else {}  # noqa: E731
        system, network, display = part("system"), part("network"), part("display")
        check = request.query.get("timestampCheck", "1")
        mac = options.normalize_mac(network.get("mac"))
        if not await self.device_allowed(mac, request.remote, body):
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
                "ip": network.get("ipAddress") or request.remote,
                "fw": body.get("fwVersion"),
                # deska posílá délku překreslení jen po probuzení, které následuje po překreslení –
                # jinak ponechat poslední známou hodnotu (≈20 000 ms = vypršel limit ovladače panelu)
                "refresh_ms": display.get("lastRefreshDuration") or prev.get("refresh_ms"),
                "download_ms": network.get("lastDownloadDuration") or prev.get("download_ms"),
                "cpu_temp": model.fnum(system.get("cpuTemp")),
                "reset_reason": system.get("resetReason"),
                "last_seen": now.isoformat(timespec="seconds"),
                "sleep": sleep,
            }
            # Změna baterie o celý krok (5 %) se má projevit hned v tomto probuzení
            # (nejvýš jednou za 30 s – jinak by šlo střídáním hodnot vynutit Chromium při každém požadavku)
            if not t.payload or (model._device(t.device)["battery"] != old_bucket
                                 and time.time() - t.last_device_rebuild > 30):
                t.last_device_rebuild = time.time()
                await self.rebuild(only=[t])
            asyncio.create_task(self.publish_device(t, now, sleep))
            print(f"[device] {t.cfg['name']} {t.device['ip']} {t.device['voltage']} V,"
                  f" {t.device['rssi']} dBm, spánek {sleep} s")

        if not t.payload:
            await self.rebuild(force=True, only=[t])
        if check == "1":
            await self.stamp_next_wake(t, now, sleep)
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
                            t.view, t.updated_text, self.opts["rotate"], self.opts["image_format"], wanted)
                        t.next_text = wanted
                    except Exception as e:  # noqa: BLE001 – raději starý odhad než nic
                        print(f"[render] patička: {e}")
        t.served_ts = t.content_ts

    async def device_allowed(self, mac: str | None, ip: str | None, body: dict) -> bool:
        """Jen zařízení ze seznamu allowed_devices. Prázdný seznam = první zařízení,
        které se ozve, se zapíše samo (trust on first use). Ostatní čekají na povolení v editoru."""
        allowed = self.opts.get("allowed_devices") or []
        if mac and mac in allowed:
            return True
        if mac and not allowed:
            try:
                await self.allow(mac)
                print(f"[security] první zařízení {mac} ({ip}) zapsáno mezi povolená")
                return True
            except Exception as e:  # noqa: BLE001
                print(f"[security] nelze uložit povolené zařízení: {e}")
                return False
        key = mac or "(bez MAC)"
        seen = self.pending.get(key)
        if not seen or time.time() - seen["t"] > 600:
            print(f"[security] odmítnuto neznámé zařízení {key} z {ip}")
        system = body.get("system") if isinstance(body.get("system"), dict) else {}
        self.pending[key] = {"mac": key, "ip": ip, "t": time.time(), "when": now_local().strftime("%d. %m. %H:%M:%S"),
                             "fw": str(body.get("fwVersion") or "")[:40], "voltage": model.fnum(system.get("vccVoltage"))}
        if len(self.pending) > 10:  # nejvýš 10 posledních
            del self.pending[min(self.pending, key=lambda k: self.pending[k]["t"])]
        return False

    async def allow(self, mac: str) -> None:
        allowed = self.opts.get("allowed_devices") or []
        if mac not in allowed:
            new = dict(self.opts, allowed_devices=allowed + [mac])
            await self.persist_options(new)
            self.opts = new
        self.pending.pop(mac, None)

    async def persist_options(self, new: dict) -> None:
        if self.ha and self.ha.base.startswith("http://supervisor"):
            await self.ha.save_addon_options(new)
        else:  # vývoj mimo HA
            with open(os.environ.get("OPTIONS_PATH", "options.json"), "w", encoding="utf-8") as f:
                json.dump(new, f, ensure_ascii=False, indent=2)

    async def publish_device(self, t: Target, now: datetime, sleep: int) -> None:
        if MOCK or not self.ha:
            return
        if time.time() - t.last_publish < 60:  # nejvýš jeden zápis do HA za minutu
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
            if s and "voltage" in s.get("attributes", {}):
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
        if request.path != "/index.php" and not DEV and request.remote != INGRESS_IP:
            raise web.HTTPForbidden(text="Pouze přes panel v Home Assistantu")
        return await handler(request)

    async def handle_index(self, request: web.Request) -> web.Response:
        now = now_local()
        cards = []
        for t in self.targets.values():
            d = t.device
            rows = "".join(
                f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>"
                for k, v in [
                    ("MAC", t.cfg["mac"] or "–"),
                    ("Rozvržení", self.layout_for(t)["name"]),
                    ("Poslední kontakt", d.get("last_seen", "zatím nikdy")),
                    ("Baterie", f"{d.get('voltage')} V ({model.battery_percent(d.get('voltage'))} %)" if d.get("voltage") else "–"),
                    ("WiFi", f"{d.get('rssi')} dBm" if d.get("rssi") is not None else "–"),
                    ("Další probuzení za", f"{d.get('sleep')} s" if d.get("sleep") else "–"),
                    ("Obnovení displeje", f"{d.get('refresh_ms')} ms" if d.get("refresh_ms") else "–"),
                    ("Poslední restart", d.get("reset_reason") or "–"),
                    ("Teplota procesoru", f"{d.get('cpu_temp'):.0f} °C" if d.get("cpu_temp") is not None else "–"),
                    ("Firmware", d.get("fw") or "–"),
                    ("Obsah změněn", datetime.fromtimestamp(t.content_ts).strftime("%H:%M:%S") if t.content_ts else "–"),
                    ("Velikost dat", f"{len(t.payload)} B ({self.opts['image_format']})"),
                    ("Režim teď", f"probouzení po {t.schedule.sleep_seconds(now)} s"
                                  + ("" if t.cfg["schedule"] else " (výchozí rozvrh)")),
                    ("Zápis do HA", (f"MQTT ({mqtt_mod.node(t.cfg['mac'])})" if self.mqtt and t.cfg["mac"]
                                     else t.cfg["battery_sensor"] or "–")),
                    ("Chyba", t.error or "žádná"),
                ])
            q = f"panel={t.cfg['mac']}&" if t.cfg["mac"] else ""
            cards.append(f"""<section><h2>{html.escape(t.cfg['name'])}</h2><div class="wrap">
<img src="preview.png?{q}{t.content_ts}" alt="Náhled displeje {html.escape(t.cfg['name'])}"><table>{rows}</table></div></section>""")
        pending = ", ".join(f"{p['mac']} z {p['ip']} ({p['when']})" for p in self.pending.values())
        page = f"""<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ESPink</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:16px;background:#f4f4f4;color:#111}}
 .wrap{{display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start}} h2{{font-weight:500;margin:24px 0 8px}}
 img{{width:360px;max-width:100%;border:1px solid #999;background:#fff;image-rendering:pixelated}}
 table{{border-collapse:collapse;background:#fff}} th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #ddd}}
 th{{font-weight:600;color:#444}} button{{margin-top:12px;padding:8px 14px;font-size:15px}}
 @media (prefers-color-scheme:dark){{body{{background:#111;color:#eee}} table{{background:#1c1c1c}} th{{color:#bbb}} th,td{{border-color:#333}}}}
</style></head><body>
<p><a href="./"><b>Editor rozvržení</b></a> · <a href="panels"><b>Panely</b></a> · <a href="settings"><b>Nastavení</b></a> · <a href="view.json">Model zobrazení (JSON)</a></p>
<p>Povolená zařízení: {html.escape(", ".join(self.opts.get("allowed_devices") or []) or "zatím žádné (první se zapíše samo)")}<br>
Odmítnuto (čeká na povolení v Panelech): {html.escape(pending or "nic")}</p>
<button onclick="fetch('refresh',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:'{{}}'}}).then(()=>location.reload())">Překreslit hned</button>
{"".join(cards)}
<script>
 // obnovit náhledy jen při změně obsahu
 let ts="{self.ts_key()}";
 setInterval(async()=>{{try{{const r=await fetch("ts");const t=await r.text();
   if(t!==ts){{location.reload()}}}}catch(e){{}}}},{1000 if DEV else 30000});
</script></body></html>"""
        return web.Response(text=page, content_type="text/html")

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

    # ── nastavení s našeptávačem ─────────────────────────────────────────
    async def handle_editor(self, request: web.Request) -> web.Response:
        return web.FileResponse(render.APP_DIR / "templates" / "editor.html",
                                headers={"Cache-Control": "no-store"})

    async def handle_settings_page(self, request: web.Request) -> web.Response:
        return web.FileResponse(render.APP_DIR / "templates" / "settings.html",
                                headers={"Cache-Control": "no-store"})

    async def handle_panels_page(self, request: web.Request) -> web.Response:
        return web.FileResponse(render.APP_DIR / "templates" / "panels.html",
                                headers={"Cache-Control": "no-store"})

    async def handle_options(self, request: web.Request) -> web.Response:
        return web.json_response(self.opts)

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

    async def handle_settings_save(self, request: web.Request) -> web.Response:
        try:
            if request.content_type != "application/json":  # formulář z cizí stránky neprojde
                return web.json_response({"error": "očekávám application/json"}, status=415)
            new = options.sanitize(await request.json(), self.opts)
            await self.persist_options(new)
        except Exception as e:  # noqa: BLE001
            return web.json_response({"error": str(e)}, status=400)
        self.opts = new
        if not self.layout_saved:  # rozvržení zatím odvozené z konfigurace
            self.layouts = {layout_mod.DEFAULT_ID: dict(layout_mod.from_options(new), name=layout_mod.DEFAULT_NAME)}
        self.sync_targets()  # výchozí rozvrh
        await self.rebuild(force=True)
        print("[settings] konfigurace uložena")
        return web.json_response({"options": self.opts, "error": self.last_error or None})

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
        await self.rebuild(force=True, only=[t for t in self.targets.values() if self.layout_for(t)["id"] == new["id"]])
        print(f"[layout] rozvržení {new['name']} uloženo")
        return web.json_response({"layout": new, "error": self.last_error or None})

    async def write_layouts(self, layouts: dict) -> None:
        try:
            layout_mod.write_all(layouts)
        except OSError as e:
            raise web.HTTPInternalServerError(text=json.dumps({"error": f"nelze uložit: {e}"}, ensure_ascii=False),
                                              content_type="application/json")
        self.layouts, self.layout_saved = layouts, True

    async def handle_layout_create(self, request: web.Request) -> web.Response:
        data = await self.read_json(request)
        name = layout_mod.clean_name(data.get("name"))
        if not name:
            raise bad_request("rozvržení musí mít název")
        src = self.layouts.get(data.get("copy") or "")
        lid = layout_mod.new_id(self.layouts)
        new = dict(src, id=lid, name=name, rev=0) if src else layout_mod.sanitize({"id": lid, "name": name, "blocks": []})
        await self.write_layouts({**self.layouts, lid: new})
        print(f"[layout] nové rozvržení {name}" + (f" (kopie {src['name']})" if src else ""))
        return web.json_response({"layout": new, "layouts": self.layout_list()})

    async def handle_layout_rename(self, request: web.Request) -> web.Response:
        cur = self.layout_arg(request)
        name = layout_mod.clean_name((await self.read_json(request)).get("name"))
        if not name:
            raise bad_request("rozvržení musí mít název")
        await self.write_layouts({**self.layouts, cur["id"]: dict(cur, name=name)})
        self.sync_targets()
        return web.json_response({"layout": self.layouts[cur["id"]], "layouts": self.layout_list()})

    async def handle_layout_delete(self, request: web.Request) -> web.Response:
        await self.read_json(request)  # jen kvůli kontrole Content-Type (CSRF)
        cur = self.layout_arg(request)
        users = [p["name"] for p in self.panels if p["layout"] == cur["id"]]
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
        async with self.draft_lock:  # Chromium je drahý – náhledy jeden po druhém
            now = now_local()
            try:
                raw = await self.fetch_raw(draft, now)
                device = t.device or await self.device_from_sensor(t.cfg["battery_sensor"])
                view = model.build(raw, draft, device, now, worst=request.query.get("worst") == "1")
                png, _ = await render.render(view, f"{now.day}. {now.month}. {now:%H:%M}",
                                             self.opts["rotate"], "png", after(now, t.schedule.sleep_seconds(now)))
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
            out.append({**p, "allowed": p["mac"] in allowed, "content_ts": t.content_ts if t else 0,
                        "error": t.error if t else "",
                        "device": {**d, "battery": model.battery_percent(d.get("voltage"))} if d else {}})
        known = {p["mac"] for p in self.panels}
        # povolené MAC bez panelu (zapsané ručně v Nastavení) – panel vznikne, až se deska ozve
        waiting = [{"mac": m, "allowed": True} for m in allowed - known]
        return {"panels": out, "pending": [p for p in self.pending.values() if p["mac"] not in known] + waiting,
                "layouts": self.layout_list(), "default_schedule": self.opts["schedule"], "mqtt": bool(self.mqtt)}

    async def handle_panels(self, request: web.Request) -> web.Response:
        return web.json_response(self.panels_json(), headers={"Cache-Control": "no-store"})

    async def handle_panel_save(self, request: web.Request) -> web.Response:
        data = await self.read_json(request)
        mac = options.normalize_mac(data.get("mac"))
        if not any(p["mac"] == mac for p in self.panels):
            raise bad_request("panel neexistuje")
        try:
            cfg = panels_mod.sanitize(data, self.layouts)
        except ValueError as e:
            raise bad_request(str(e))
        if data.get("layout") not in self.layouts:
            raise bad_request("rozvržení neexistuje")
        if any(p["name"] == cfg["name"] and p["mac"] != mac for p in self.panels):
            raise bad_request(f"panel {cfg['name']} už existuje")
        self.save_panels([cfg if p["mac"] == mac else p for p in self.panels])
        await self.announce(cfg)  # nový název zařízení v HA
        # bez force: obrázek (a tím překreslení desky) se změní, jen když se změní obsah – např. jiné rozvržení
        await self.rebuild(only=[self.targets[mac]])
        print(f"[panels] panel {cfg['name']} uložen")
        return web.json_response(self.panels_json())

    async def handle_panel_approve(self, request: web.Request) -> web.Response:
        """Povolit odmítnutou desku a založit pro ni panel."""
        data = await self.read_json(request)
        mac = options.normalize_mac(data.get("mac"))
        if not mac:
            raise bad_request("neplatná MAC adresa")
        if any(p["name"] == layout_mod.clean_name(data.get("name")) for p in self.panels):
            raise bad_request(f"panel {data.get('name')} už existuje")
        try:
            await self.allow(mac)
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
        self.save_panels([p for p in self.panels if p["mac"] != mac])
        self.forget(mac)
        print(f"[panels] panel {mac} odebrán")
        return web.json_response(self.panels_json())


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

        app = web.Application(middlewares=[srv.ingress_only])
        app.add_routes([
            web.post("/index.php", srv.handle_device),
            web.get("/", srv.handle_editor),
            web.get("/status", srv.handle_index),
            web.get("/preview.png", srv.handle_preview),
            web.get("/ts", srv.handle_ts),
            web.get("/view.json", srv.handle_view),
            web.post("/refresh", srv.handle_refresh),
            web.get("/settings", srv.handle_settings_page),
            web.post("/settings", srv.handle_settings_save),
            web.get("/options.json", srv.handle_options),
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
        ])
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, args.host, args.port).start()
        print(f"[server] naslouchám na :{args.port} (DEV={DEV}, MOCK={MOCK}), HA API: {srv.ha.base},"
              f" časové pásmo: {now_local().tzname()}")
        await srv.loop()


if __name__ == "__main__":
    asyncio.run(main())
