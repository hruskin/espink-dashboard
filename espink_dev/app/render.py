"""HTML -> PNG (headless Chromium) -> přesná paleta BWR -> náhled PNG + data pro zařízení."""
import asyncio
import io
import os
import pwd
import shutil
import tempfile
from pathlib import Path

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

_env = Environment(loader=FileSystemLoader(APP_DIR / "templates"), autoescape=select_autoescape(["j2", "html"]))


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


async def render(view: dict, updated: str, rotate: int, fmt: str, next_wake: str = "") -> tuple[bytes, bytes]:
    """Vrátí (náhled PNG v portrétu, data pro zařízení)."""
    img = await screenshot(render_html(view, updated, next_wake))
    pal = await asyncio.to_thread(quantize, img)
    payload = await asyncio.to_thread(device_payload, pal, rotate, fmt)
    return to_png(pal), payload
