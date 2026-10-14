"""Model zobrazení -> Typst -> PNG -> přesná paleta BWR -> náhled PNG + data pro zařízení.

Šablona templates/dashboard.typ dostane model zobrazení jako JSON (text se nikdy
nevykoná jako kód) a Typst ji přeloží rovnou do PNG 480 × 800 (1 pt = 1 px).
Typst běží bez sítě a s prázdným kořenovým adresářem, nemá tedy co číst.
"""
import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

import emoji

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TYPST_FONTS = STATIC_DIR / "fonts" / "typst"
WIDTH, HEIGHT = 480, 800  # portrét; zařízení dostane obraz otočený do nativních 800x480

# Kódy barev protokolu Živý obraz (Z2): 0 bílá, 1 černá, 2 červená
WHITE, BLACK, RED = 0, 1, 2
PREVIEW_PALETTE = [255, 255, 255, 0, 0, 0, 224, 0, 0]
DEVICE_RGB = {WHITE: (255, 255, 255), BLACK: (0, 0, 0), RED: (255, 0, 0)}
# Práh černé (jas 0–255); stejná logika jako firmware pro PNG
BLACK_THRESHOLD = 160

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


def typst_str(value) -> str:
    """Text jako řetězcový literál Typstu ("…") – žádný obsah se nevykoná jako kód."""
    s = "" if value is None else str(value)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "").replace("\t", " ")
    return '"' + "".join(ch for ch in s if ch >= " ") + '"'


def render_typst(view: dict, updated: str, next_wake: str = "") -> str:
    """Zdroj pro Typst: data jako JSON v řetězcovém literálu + šablona dashboard.typ."""
    codes = mdi_codes()
    names = _icon_names(view, set(TYPST_ICONS))
    # emoji z tabulky -> plné ikony MDI (Noto Emoji kreslí mnohé jen obrysem)
    view = emoji.apply(view, emoji.icon_chars(codes))
    # blok Šablona (HTML) se ukáže jen jako text
    tpl = {b["id"]: re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", str(b.get("html") or ""))).strip()
           for b in view["blocks"] if b["type"] == "template"}
    data = {"view": view, "updated": updated, "updated_time": updated.split()[-1], "next_wake": next_wake or "",
            "icons": {n: codes[n] for n in names if n in codes}, "template_text": tpl}
    source = (APP_DIR / "templates" / "dashboard.typ").read_text(encoding="utf-8")
    return f"#let data = json.decode({typst_str(json.dumps(data, ensure_ascii=False, default=str))})\n" + source


def typst_binary() -> str:
    for name in (os.environ.get("TYPST"), "typst"):
        if name and shutil.which(name):
            return os.path.abspath(shutil.which(name))  # překlad běží v dočasném adresáři
    raise RuntimeError("Typst nenalezen")


def typst_png(source: str) -> Image.Image:
    with tempfile.TemporaryDirectory(prefix="espink-typst-") as root:  # prázdný kořen: šablona nemá co číst
        out = subprocess.run([typst_binary(), "compile", "--root", root, "--font-path", str(TYPST_FONTS),
                              "--ignore-system-fonts", "--format", "png", "--ppi", "72", "-", "-"],
                             input=source.encode(), capture_output=True, timeout=60, check=False, cwd=root)
    if out.returncode or not out.stdout:
        raise RuntimeError(f"Typst: {out.stderr.decode(errors='replace')[-600:]}")
    img = Image.open(io.BytesIO(out.stdout)).convert("RGB")
    if img.size != (WIDTH, HEIGHT):
        canvas = Image.new("RGB", (WIDTH, HEIGHT), "white")
        canvas.paste(img.crop((0, 0, min(WIDTH, img.width), min(HEIGHT, img.height))))
        img = canvas
    return img


async def rasterize(view: dict, updated: str, next_wake: str = "") -> Image.Image:
    t = time.monotonic()
    img = await asyncio.to_thread(typst_png, render_typst(view, updated, next_wake))
    if os.environ.get("DEV") == "1":
        print(f"[render] typst: {time.monotonic() - t:.2f} s")
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
    img = await rasterize(view, updated, next_wake)
    pal = await asyncio.to_thread(quantize, img)
    payload = await asyncio.to_thread(device_payload, pal, rotate, fmt)
    return to_png(pal), payload
