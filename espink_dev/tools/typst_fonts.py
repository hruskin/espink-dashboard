"""Statické řezy fontů pro renderer Typst (Typst 0.12 nenačte variabilní fonty).

Z app/static/fonts/*.ttf (variabilní) vyrobí app/static/fonts/typst/*.ttf:
Inter 400–800 (opsz 14) a Inter Display (opsz 32, velká čísla), Roboto Condensed
400–800, Noto Emoji 400/700 a Material Design Icons jako TTF. Text je oříznutý na
latinku, řečtinu a cyrilici (menší add-on). Spuštění (potřebuje fonttools a brotli):
    python tools/typst_fonts.py
"""
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

APP = Path(__file__).resolve().parent.parent / "app" / "static"
SRC, OUT = APP / "fonts", APP / "fonts" / "typst"
TEXT_RANGES = ("U+0020-024F,U+0250-02FF,U+0300-036F,U+0370-03FF,U+0400-04FF,U+1E00-1EFF,"
               "U+2000-206F,U+20A0-20CF,U+2100-214F,U+2190-21FF,U+2200-22FF,U+25A0-25FF,U+2600-26FF")


def static(src: str, family: str, weight: int, axes: dict, keep: str | None) -> None:
    f = instancer.instantiateVariableFont(TTFont(SRC / src), {"wght": weight, **axes})
    if keep:
        opts = subset.Options()
        opts.layout_features = ["*"]
        opts.name_IDs = ["*"]
        opts.notdef_outline = True
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=subset.parse_unicodes(keep))
        sub.subset(f)
    name = f["name"]
    for rec in list(name.names):
        if rec.nameID in (1, 2, 4, 6, 16, 17):
            name.removeNames(nameID=rec.nameID)
    ps = family.replace(" ", "") + f"-{weight}"
    for nid, val in ((1, family), (2, "Regular"), (4, f"{family} {weight}"), (6, ps), (16, family), (17, "Regular")):
        name.setName(val, nid, 3, 1, 0x409)
    f["OS/2"].usWeightClass = weight
    f["OS/2"].fsSelection = (f["OS/2"].fsSelection & ~0b1100001) | 0b1000000  # REGULAR, ne BOLD/ITALIC
    f["head"].macStyle = 0
    for t in ("STAT", "fvar", "avar", "gvar", "HVAR", "MVAR"):
        if t in f:
            del f[t]
    f.save(OUT / f"{ps}.ttf")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for w in (400, 500, 600, 700, 800):
        static("Inter.ttf", "Inter", w, {"opsz": 14}, TEXT_RANGES)
        static("RobotoCondensed.ttf", "RobotoCondensed", w, {}, TEXT_RANGES)
    for w in (400, 800):
        static("Inter.ttf", "Inter Display", w, {"opsz": 32}, TEXT_RANGES)
    for w in (400, 700):
        static("NotoEmoji.ttf", "Noto Emoji", w, {}, None)
    mdi = TTFont(APP / "mdi" / "fonts" / "materialdesignicons-webfont.woff2")
    mdi.flavor = None
    mdi.save(OUT / "MaterialDesignIcons.ttf")
    print("\n".join(f"{p.name}: {p.stat().st_size // 1024} kB" for p in sorted(OUT.glob("*.ttf"))))


if __name__ == "__main__":
    main()
