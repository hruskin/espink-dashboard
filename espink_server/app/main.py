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

import aiohttp
from aiohttp import web

import ha as ha_mod
import mock
import model
import options
import render

INGRESS_IP = "172.30.32.2"
DEV = os.environ.get("DEV") == "1"
MOCK = os.environ.get("MOCK") == "1"


def now_local() -> datetime:
    return datetime.now().astimezone()


class Server:
    def __init__(self, opts: dict):
        self.opts = opts
        self.schedule = options.Schedule.parse(opts["schedule"])
        self.lock = asyncio.Lock()
        self.ha: ha_mod.HomeAssistant | None = None
        self.view: dict | None = None
        self.view_hash = ""
        self.content_ts = 0
        self.preview = b""
        self.payload = b""
        self.device: dict = {}
        self.last_error = ""
        self.last_build = 0.0

    # ── sestavení obrázku ────────────────────────────────────────────────
    async def rebuild(self, force: bool = False) -> None:
        async with self.lock:
            now = now_local()
            try:
                raw = mock.raw(self.opts, now) if MOCK else await ha_mod.collect(self.ha, self.opts, now)
                view = model.build(raw, self.opts, self.device, now)
                digest = model.digest(view)
                self.last_build = time.time()
                if digest == self.view_hash and not force:
                    return
                updated = f"{now.day}. {now.month}. {now:%H:%M}"  # %-d nefunguje v musl (Alpine)
                self.preview, self.payload = await render.render(
                    view, updated, self.opts["rotate"], self.opts["image_format"])
                self.view, self.view_hash, self.content_ts = view, digest, int(now.timestamp())
                self.last_error = ""
                print(f"[render] nový obsah {digest[:8]} ({len(self.payload)} B)")
            except Exception as e:  # noqa: BLE001 – server musí běžet dál i při chybě
                self.last_error = f"{now:%H:%M:%S} {type(e).__name__}: {e}"
                print(f"[render] chyba: {self.last_error}")

    async def loop(self) -> None:
        while True:
            await self.rebuild()
            await asyncio.sleep(self.opts["render_interval"])

    # ── zařízení ─────────────────────────────────────────────────────────
    async def handle_device(self, request: web.Request) -> web.Response:
        try:
            body = await request.json() if request.can_read_body else {}
        except (json.JSONDecodeError, aiohttp.ContentTypeError):
            body = {}
        system, network, display = body.get("system", {}), body.get("network", {}), body.get("display", {})
        check = request.query.get("timestampCheck", "1")
        now = now_local()
        sleep = self.schedule.sleep_seconds(now)

        if check == "1":
            old_bucket = model._device(self.device)["battery"]
            self.device = {
                "voltage": model.fnum(system.get("vccVoltage")),
                "rssi": model.fnum(network.get("rssi")),
                "mac": network.get("mac"),
                "ip": network.get("ipAddress") or request.remote,
                "fw": body.get("fwVersion"),
                "refresh_ms": display.get("lastRefreshDuration"),
                "download_ms": network.get("lastDownloadDuration"),
                "reset_reason": system.get("resetReason"),
                "last_seen": now.isoformat(timespec="seconds"),
                "sleep": sleep,
            }
            # Změna baterie o celý krok (5 %) se má projevit hned v tomto probuzení
            if not self.payload or model._device(self.device)["battery"] != old_bucket:
                await self.rebuild()
            asyncio.create_task(self.publish_device(now, sleep))
            print(f"[device] {self.device['ip']} {self.device['voltage']} V, {self.device['rssi']} dBm, spánek {sleep} s")

        if not self.payload:
            await self.rebuild(force=True)
        headers = {
            "Timestamp": str(self.content_ts),
            "PreciseSleep": str(sleep),
            "Content-Type": "image/png" if self.opts["image_format"] == "png" else "application/octet-stream",
            "Connection": "close",
        }
        return web.Response(body=self.payload, headers=headers)

    async def publish_device(self, now: datetime, sleep: int) -> None:
        if MOCK or not self.ha or not self.opts.get("battery_sensor"):
            return
        d = self.device
        pct = model.battery_percent(d.get("voltage"))
        wake = datetime.fromtimestamp(now.timestamp() + sleep).astimezone()
        await self.ha.set_state(self.opts["battery_sensor"], pct if pct is not None else "unknown", {
            "unit_of_measurement": "%", "device_class": "battery", "state_class": "measurement",
            "friendly_name": "ESPink baterie", "voltage": d.get("voltage"), "rssi": d.get("rssi"),
            "last_seen": d.get("last_seen"), "next_wake": wake.isoformat(timespec="seconds"),
            "refresh_ms": d.get("refresh_ms"), "ip": d.get("ip"), "firmware": d.get("fw"),
        })

    # ── UI (jen přes Ingress) ────────────────────────────────────────────
    @web.middleware
    async def ingress_only(self, request: web.Request, handler):
        if request.path != "/index.php" and not DEV and request.remote != INGRESS_IP:
            raise web.HTTPForbidden(text="Pouze přes panel v Home Assistantu")
        return await handler(request)

    async def handle_index(self, request: web.Request) -> web.Response:
        d = self.device
        rows = "".join(
            f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>"
            for k, v in [
                ("Poslední kontakt", d.get("last_seen", "zatím nikdy")),
                ("Baterie", f"{d.get('voltage')} V ({model.battery_percent(d.get('voltage'))} %)" if d.get("voltage") else "–"),
                ("WiFi", f"{d.get('rssi')} dBm" if d.get("rssi") is not None else "–"),
                ("Další probuzení za", f"{d.get('sleep')} s" if d.get("sleep") else "–"),
                ("Obnovení displeje", f"{d.get('refresh_ms')} ms" if d.get("refresh_ms") else "–"),
                ("Firmware", d.get("fw") or "–"),
                ("Obsah změněn", datetime.fromtimestamp(self.content_ts).strftime("%H:%M:%S") if self.content_ts else "–"),
                ("Velikost dat", f"{len(self.payload)} B ({self.opts['image_format']})"),
                ("Režim teď", f"probouzení po {self.schedule.sleep_seconds(now_local())} s"),
                ("Chyba", self.last_error or "žádná"),
            ])
        page = f"""<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ESPink</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:16px;background:#f4f4f4;color:#111}}
 .wrap{{display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start}}
 img{{width:480px;max-width:100%;border:1px solid #999;background:#fff;image-rendering:pixelated}}
 table{{border-collapse:collapse;background:#fff}} th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #ddd}}
 th{{font-weight:600;color:#444}} button{{margin-top:12px;padding:8px 14px;font-size:15px}}
 @media (prefers-color-scheme:dark){{body{{background:#111;color:#eee}} table{{background:#1c1c1c}} th{{color:#bbb}} th,td{{border-color:#333}}}}
</style></head><body>
<div class="wrap"><img id="p" src="preview.png?{self.content_ts}" alt="Náhled displeje">
<div><table>{rows}</table>
<form method="post" action="refresh"><button>Překreslit hned</button></form>
<p><a href="view.json">Model zobrazení (JSON)</a></p></div></div>
<script>setTimeout(()=>location.reload(),30000)</script></body></html>"""
        return web.Response(text=page, content_type="text/html")

    async def handle_preview(self, request: web.Request) -> web.Response:
        if not self.preview:
            await self.rebuild(force=True)
        return web.Response(body=self.preview, content_type="image/png", headers={"Cache-Control": "no-store"})

    async def handle_view(self, request: web.Request) -> web.Response:
        return web.json_response({"view": self.view, "device": self.device, "hash": self.view_hash},
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=2, default=str))

    async def handle_refresh(self, request: web.Request) -> web.Response:
        await self.rebuild(force=True)
        raise web.HTTPSeeOther("./")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", metavar="PNG", help="jen vykreslit náhled do souboru a skončit")
    p.add_argument("--port", type=int, default=8099)
    args = p.parse_args()

    srv = Server(options.load())
    async with aiohttp.ClientSession() as session:
        srv.ha = ha_mod.HomeAssistant(session)
        if args.once:
            await srv.rebuild(force=True)
            if srv.last_error:
                raise SystemExit(srv.last_error)
            with open(args.once, "wb") as f:
                f.write(srv.preview)
            print(f"náhled: {args.once}, data pro zařízení: {len(srv.payload)} B")
            return

        app = web.Application(middlewares=[srv.ingress_only])
        app.add_routes([
            web.post("/index.php", srv.handle_device),
            web.get("/index.php", srv.handle_device),
            web.get("/", srv.handle_index),
            web.get("/preview.png", srv.handle_preview),
            web.get("/view.json", srv.handle_view),
            web.post("/refresh", srv.handle_refresh),
        ])
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", args.port).start()
        print(f"[server] naslouchám na :{args.port} (DEV={DEV}, MOCK={MOCK})")
        await srv.loop()


if __name__ == "__main__":
    asyncio.run(main())
