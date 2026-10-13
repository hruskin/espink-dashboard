"""Model zobrazení -> obrázek -> přesná paleta BWR -> náhled PNG + data pro zařízení.

Vykreslovače (volba renderer):
  chromium   – HTML/CSS šablona, snímek z headless Chromia (původní, velký obraz)
  weasyprint – stejná HTML/CSS šablona přes WeasyPrint (PDF) a pdftoppm (PNG)
  typst      – vlastní šablona dashboard.typ.j2 přeložená Typstem rovnou do PNG
"""
import asyncio
import io
import json
import logging
import os
import pwd
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
WIDTH, HEIGHT = 480, 800  # portrét; zařízení dostane obraz otočený do nativních 800x480
# Headless Chromium (některé verze) odečte z --window-size výšku neviditelného UI,
# takže okno bereme vyšší a snímek ořízneme.
WINDOW_EXTRA = 300

# Kódy barev protokolu Živý obraz (Z2): 0 bílá, 1 černá, 2 červená
WHITE, BLACK, RED = 0, 1, 2
PREVIEW_PALETTE = [255, 255, 255, 0, 0, 0, 224, 0, 0]
DEVICE_RGB = {WHITE: (255, 255, 255), BLACK: (0, 0, 0), RED: (255, 0, 0)}
# Práh černé (jas 0–255); stejná logika jako firmware pro PNG
BLACK_THRESHOLD = 160

_env = Environment(loader=FileSystemLoader(APP_DIR / "templates"), autoescape=select_autoescape(["html.j2", "html"]))
ENGINES = ("chromium", "weasyprint", "typst")
ENGINE = "chromium"  # nastavuje main.py podle konfigurace (renderer)
TYPST_FONTS = STATIC_DIR / "fonts" / "typst"


def chromium_binary() -> str:
    for name in (os.environ.get("CHROMIUM"), "chromium-browser", "chromium", "google-chrome-stable", "google-chrome"):
        if name and shutil.which(name):
            return shutil.which(name)
    raise RuntimeError("Chromium nenalezen")


def render_html(view: dict, updated: str, next_wake: str = "") -> str:
    return _env.get_template("dashboard.html.j2").render(
        v=view, updated=updated, updated_time=updated.split()[-1], next_wake=next_wake, static=STATIC_DIR.as_uri())


# V kontejneru běží add-on jako root – Chromium (s --no-sandbox) spouštíme pod
# neprivilegovaným uživatelem z Dockerfile.
RENDER_USER = "espink-render"


def unprivileged(tmp: str) -> dict:
    if os.geteuid() != 0:
        return {}
    try:
        pw = pwd.getpwnam(RENDER_USER)
    except KeyError:
        return {}
    os.chown(tmp, pw.pw_uid, pw.pw_gid)
    return {"user": pw.pw_uid, "group": pw.pw_gid, "env": {**os.environ, "HOME": tmp}}


_unprivileged_ok = True


async def screenshot(html: str) -> Image.Image:
    """Pod neprivilegovaným uživatelem; kdyby to v daném prostředí nešlo,
    raději vykreslit postaru (s varováním) než nekreslit vůbec."""
    global _unprivileged_ok
    if _unprivileged_ok:
        try:
            return await _screenshot(html, drop=True)
        except RuntimeError as e:
            if os.geteuid() != 0:
                raise
            _unprivileged_ok = False
            print(f"[security] VAROVÁNÍ: Chromium pod {RENDER_USER} selhal ({e}); vykresluji jako root")
    return await _screenshot(html, drop=False)


async def _screenshot(html: str, drop: bool) -> Image.Image:
    with tempfile.TemporaryDirectory(prefix="espink-") as tmp:
        page = Path(tmp) / "page.html"
        out = Path(tmp) / "shot.png"
        page.write_text(html, encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            chromium_binary(),
            "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
            "--hide-scrollbars", "--no-first-run", "--mute-audio",
            "--force-device-scale-factor=1", f"--window-size={WIDTH},{HEIGHT + WINDOW_EXTRA}",
            "--default-background-color=FFFFFFFF", "--virtual-time-budget=2000",
            "--font-render-hinting=full", "--disable-lcd-text",
            # stránka nepotřebuje síť (názvy událostí jsou cizí obsah); skripty blokuje CSP
            # v šabloně (scriptEnabled=false nejde – headless snímek JS potřebuje)
            "--disable-background-networking",
            "--proxy-server=127.0.0.1:9", "--proxy-bypass-list=<-loopback>",
            f"--user-data-dir={tmp}/profile", f"--screenshot={out}", page.as_uri(),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            **(unprivileged(tmp) if drop else {}),
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("Chromium nestihl vykreslit stránku")
        if not out.exists():
            raise RuntimeError(f"Chromium selhal: {err.decode(errors='replace')[-500:]}")
        img = Image.open(out).convert("RGB")
        img.load()
    if img.size != (WIDTH, HEIGHT):
        img = img.crop((0, 0, WIDTH, HEIGHT))
    return img


def quantize(img: Image.Image) -> Image.Image:
    """RGB -> paletový obrázek s indexy 0/1/2 (bez ditheringu, text zůstane ostrý)."""
    data = img.tobytes()
    rs, gs, bs = data[0::3], data[1::3], data[2::3]
    codes = bytes(
        RED if (r >= 128 and r > g + 80 and r > b + 80)
        else (BLACK if (r * 77 + g * 150 + b * 29) >> 8 <= BLACK_THRESHOLD else WHITE)
        for r, g, b in zip(rs, gs, bs)
    )
    out = Image.frombytes("P", img.size, codes)
    out.putpalette(PREVIEW_PALETTE)
    return out


def encode_z2(img: Image.Image) -> bytes:
    """Z2: hlavička 'Z2', pak bajty (barva << 6) | počet (1–63), řádek po řádku."""
    codes = img.tobytes()
    out = bytearray(b"Z2")
    n, i = len(codes), 0
    while i < n:
        c = codes[i]
        j = i + 1
        while j < n and codes[j] == c and j - i < 63:
            j += 1
        out.append((c << 6) | (j - i))
        i = j
    return bytes(out)


def device_payload(pal: Image.Image, rotate: int, fmt: str) -> bytes:
    # rotate = otočení po směru hodinových ručiček (PIL otáčí proti směru)
    img = pal.rotate(-rotate, expand=True) if rotate else pal
    if fmt == "png":
        rgb = Image.new("RGB", img.size)
        rgb.putdata([DEVICE_RGB.get(c, DEVICE_RGB[WHITE]) for c in img.tobytes()])
        buf = io.BytesIO()
        rgb.save(buf, "PNG", optimize=True)
        return buf.getvalue()
    return encode_z2(img)


def to_png(pal: Image.Image) -> bytes:
    buf = io.BytesIO()
    pal.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ── WeasyPrint ──────────────────────────────────────────────────────────
logging.getLogger("weasyprint").setLevel(logging.ERROR)  # nepodporované CSS (jen Chromium) nehlásit
logging.getLogger("fontTools").setLevel(logging.ERROR)


def _weasy_check(url: str) -> None:
    """Jen soubory add-onu (fonty, ikony) a data: – žádná síť (názvy událostí a šablony
    jsou cizí obsah) ani jiné soubory."""
    if url.startswith("data:"):
        return
    if url.startswith("file:") and Path(unquote(urlparse(url).path)).resolve().is_relative_to(STATIC_DIR):
        return
    raise ValueError(f"zakázaná adresa: {url[:80]}")


def _weasy_fetcher():
    import weasyprint
    import weasyprint.urls
    if hasattr(weasyprint.urls, "URLFetcher"):  # WeasyPrint 68+: třída
        class Fetcher(weasyprint.urls.URLFetcher):
            def fetch(self, url, *args, **kwargs):
                _weasy_check(url)
                return super().fetch(url, *args, **kwargs)
        return Fetcher(allowed_protocols=("file", "data"))

    def fetch(url, *args, **kwargs):  # starší (Alpine 3.21: 63): funkce
        _weasy_check(url)
        return weasyprint.default_url_fetcher(url, *args, **kwargs)
    return fetch


def weasy_png(html: str) -> Image.Image:
    import weasyprint
    pdf = weasyprint.HTML(string=html, base_url=STATIC_DIR.as_uri(), url_fetcher=_weasy_fetcher()).write_pdf(
        # stránka vyšší než displej: obsah u spodního okraje by WeasyPrint jinak přesunul
        # na další stránku (body má pevných 800 px, přebytek se ořízne)
        stylesheets=[weasyprint.CSS(string=f"@page {{ size: {WIDTH}px {HEIGHT * 2}px; margin: 0 }}")])
    # 96 dpi: 1 CSS px = 1 pixel
    out = subprocess.run(["pdftoppm", "-r", "96", "-png", "-singlefile", "-"], input=pdf,
                         capture_output=True, timeout=60, check=False)
    if out.returncode or not out.stdout:
        raise RuntimeError(f"pdftoppm selhal: {out.stderr.decode(errors='replace')[-300:]}")
    return _fit(Image.open(io.BytesIO(out.stdout)).convert("RGB"))


def _fit(img: Image.Image) -> Image.Image:
    if img.size != (WIDTH, HEIGHT):
        canvas = Image.new("RGB", (WIDTH, HEIGHT), "white")
        canvas.paste(img.crop((0, 0, min(WIDTH, img.width), min(HEIGHT, img.height))))
        img = canvas
    return img


# ── Typst ───────────────────────────────────────────────────────────────
def typst_str(value) -> str:
    """Text jako řetězcový literál Typstu ("…") – žádný obsah se nevykoná jako kód."""
    s = "" if value is None else str(value)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "").replace("\t", " ")
    return '"' + "".join(ch for ch in s if ch >= " ") + '"'


_MDI_RE = re.compile(r'\.mdi-([a-z0-9-]+)::?before\s*\{\s*content:\s*"\\([0-9A-Fa-f]+)"')
_mdi_codes: dict[str, int] = {}
# ikony použité přímo v šabloně (ostatní – počasí, kalendáře, entity – jsou v modelu)
TYPST_ICONS = ("bus", "alert", "update", "timer-sand", "calendar-week", "water-percent", "weather-pouring",
               "home-thermometer-outline", "gauge", "flower-tulip-outline")


def mdi_codes() -> dict[str, int]:
    """Název ikony -> kód znaku ve fontu Material Design Icons (z CSS knihovny)."""
    if not _mdi_codes:
        css = (STATIC_DIR / "mdi" / "css" / "materialdesignicons.min.css").read_text(encoding="utf-8")
        _mdi_codes.update((n, int(c, 16)) for n, c in _MDI_RE.findall(css))
    return _mdi_codes


def _icon_names(obj, out: set) -> set:
    if isinstance(obj, dict):
        for k, val in obj.items():
            if k.endswith("icon") and isinstance(val, str):
                out.add(val.removeprefix("mdi:"))
            else:
                _icon_names(val, out)
    elif isinstance(obj, list):
        for x in obj:
            _icon_names(x, out)
    return out


def render_typst(view: dict, updated: str, next_wake: str = "") -> str:
    """Zdroj pro Typst: data jako JSON v řetězcovém literálu + šablona dashboard.typ."""
    codes = mdi_codes()
    names = _icon_names(view, set(TYPST_ICONS))
    # šablona (HTML) se v Typstu ukáže jen jako text
    tpl = {b["id"]: re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", str(b.get("html") or ""))).strip()
           for b in view["blocks"] if b["type"] == "template"}
    data = {"view": view, "updated": updated, "updated_time": updated.split()[-1], "next_wake": next_wake or "",
            "icons": {n: codes[n] for n in names if n in codes}, "template_text": tpl}
    source = (APP_DIR / "templates" / "dashboard.typ").read_text(encoding="utf-8")
    return f"#let data = json.decode({typst_str(json.dumps(data, ensure_ascii=False, default=str))})\n" + source


def typst_binary() -> str:
    for name in (os.environ.get("TYPST"), "typst"):
        if name and shutil.which(name):
            return shutil.which(name)
    raise RuntimeError("Typst nenalezen")


def typst_png(source: str) -> Image.Image:
    with tempfile.TemporaryDirectory(prefix="espink-typst-") as root:  # prázdný kořen: šablona nemá co číst
        out = subprocess.run([typst_binary(), "compile", "--root", root, "--font-path", str(TYPST_FONTS),
                              "--ignore-system-fonts", "--format", "png", "--ppi", "72", "-", "-"],
                             input=source.encode(), capture_output=True, timeout=60, check=False, cwd=root)
    if out.returncode or not out.stdout:
        raise RuntimeError(f"Typst: {out.stderr.decode(errors='replace')[-600:]}")
    return _fit(Image.open(io.BytesIO(out.stdout)).convert("RGB"))


async def rasterize(view: dict, updated: str, next_wake: str = "", engine: str | None = None) -> Image.Image:
    engine = engine or ENGINE
    t = time.monotonic()
    if engine == "typst":
        img = await asyncio.to_thread(typst_png, render_typst(view, updated, next_wake))
    elif engine == "weasyprint":
        img = await asyncio.to_thread(weasy_png, render_html(view, updated, next_wake))
    else:
        img = await screenshot(render_html(view, updated, next_wake))
    if os.environ.get("DEV") == "1":
        print(f"[render] {engine}: {time.monotonic() - t:.2f} s")
    return img


async def render(view: dict, updated: str, rotate: int, fmt: str, next_wake: str = "",
                 engine: str | None = None) -> tuple[bytes, bytes]:
    """Vrátí (náhled PNG v portrétu, data pro zařízení)."""
    img = await rasterize(view, updated, next_wake, engine)
    pal = await asyncio.to_thread(quantize, img)
    payload = await asyncio.to_thread(device_payload, pal, rotate, fmt)
    return to_png(pal), payload
