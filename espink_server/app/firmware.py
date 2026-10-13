"""Webový instalátor firmwaru: binárka z app/firmware s doplněnou konfigurací.

Firmware (patch 05) má v obrazu blok se značkou ZOCFG1 – host a port serveru
a heslo konfiguračního AP. Při stažení se blok přepíše hodnotami ze stránky
Instalace a přepočítá se kontrolní součet a SHA-256 obrazu ESP32 (jinak by ho
bootloader odmítl). V repozitáři je tak jen neutrální binárka bez tajných údajů.
"""
import hashlib
import ipaddress
import json
import os
import re
import secrets
import struct
from pathlib import Path

DIR = Path(__file__).resolve().parent / "firmware"
MAGIC = b"ZOCFG1:espink\x00"
# struct ZoConfig: magic[16], host[64], port[8], apPassword[64]
FIELDS = (("host", 16, 64), ("port", 80, 8), ("ap_password", 88, 64))
IMAGE_MAGIC = 0xE9
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,63}$")
# Pořadí a adresy oddílů – stejně jako při nahrání přes PlatformIO
PARTS = (("bootloader.bin", 0x0), ("partitions.bin", 0x8000), ("boot_app0.bin", 0xE000), ("firmware.bin", 0x10000))


def available() -> bool:
    return all((DIR / name).is_file() for name, _ in PARTS)


def build_info() -> dict:
    try:
        return json.loads((DIR / "build.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def settings_path() -> str:
    return os.path.join(os.path.dirname(os.environ.get("LAYOUT_PATH", "/data/layout.json")) or ".", "installer.json")


def load_settings() -> dict:
    """Uložené hodnoty instalátoru; heslo AP se při prvním použití vygeneruje."""
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not data.get("ap_password"):
        data["ap_password"] = "".join(secrets.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(12))
        save_settings(data)
    return data


def save_settings(data: dict) -> None:
    tmp = settings_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)  # heslo AP
    os.replace(tmp, settings_path())


def check(host: str, port, ap_password: str) -> dict:
    host = str(host or "").strip()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not HOST_RE.match(host):
            raise ValueError("neplatná adresa serveru (IP nebo název bez mezer, max. 63 znaků)")
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("port musí být 1–65535")
    ap = str(ap_password or "")
    if not 8 <= len(ap) <= 63 or not ap.isascii() or not ap.isprintable():
        raise ValueError("heslo AP musí mít 8–63 znaků ASCII")
    return {"host": host, "port": port, "ap_password": ap}


def _segments_end(img: bytes) -> tuple[int, list[tuple[int, int]], bool]:
    """Konec segmentů a jejich (offset dat, délka) v obrazu ESP32; hash_appended."""
    if len(img) < 24 or img[0] != IMAGE_MAGIC:
        raise ValueError("firmware.bin není obraz ESP32")
    count, hash_appended = img[1], img[23] == 1
    pos, segs = 24, []
    for _ in range(count):
        _, length = struct.unpack_from("<II", img, pos)
        segs.append((pos + 8, length))
        pos += 8 + length
    return pos, segs, hash_appended


def patch_image(img: bytes, cfg: dict) -> bytes:
    """Přepíše blok ZOCFG1 a dopočítá kontrolní součet a SHA-256."""
    i = img.find(MAGIC)
    if i < 0 or img.find(MAGIC, i + 1) >= 0:
        raise ValueError("firmware.bin neobsahuje (právě jeden) blok ZOCFG1 – je sestavený s patchem 05?")
    out = bytearray(img)
    values = {"host": cfg["host"], "port": str(cfg["port"]), "ap_password": cfg["ap_password"]}
    for key, off, size in FIELDS:
        raw = values[key].encode()
        if len(raw) >= size:
            raise ValueError(f"{key} je moc dlouhé")
        out[i + off:i + off + size] = raw.ljust(size, b"\0")
    end, segs, hash_appended = _segments_end(out)
    if not any(start <= i and i + 160 <= start + length for start, length in segs):
        raise ValueError("blok ZOCFG1 neleží v jednom segmentu obrazu")
    checksum = 0xEF
    for start, length in segs:
        for b in out[start:start + length]:
            checksum ^= b
    # za segmenty výplň tak, aby kontrolní bajt byl na pozici ≡ 15 (mod 16)
    csum_pos = end + (15 - end % 16)
    out[csum_pos] = checksum
    if hash_appended:
        out[csum_pos + 1:csum_pos + 33] = hashlib.sha256(out[:csum_pos + 1]).digest()
    return bytes(out)


def part(name: str, cfg: dict) -> bytes:
    if name not in dict(PARTS):
        raise FileNotFoundError(name)
    data = (DIR / name).read_bytes()
    return patch_image(data, cfg) if name == "firmware.bin" else data


def manifest(version: str) -> dict:
    """Manifest pro ESP Web Tools; cesty k částem jsou relativní k manifestu."""
    return {
        "name": "ESPink (Živý obraz pro ESPink Server)",
        "version": version,
        "new_install_prompt_erase": True,
        "builds": [{"chipFamily": "ESP32-S3", "parts": [{"path": name, "offset": off} for name, off in PARTS]}],
    }
