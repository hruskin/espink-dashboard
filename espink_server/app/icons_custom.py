"""Vlastní ikony (SVG 24×24, kreslené pro e-paper), v konfiguraci „espink:název“.

Kreslí je šablona Typst (image.decode) i editor (náhled a výběr ikon). Jsou to pevné
řetězce z kódu, žádná data od uživatele.
"""
PAPER_HAT_BAND = "<path fill-rule='evenodd' d='M1 16.2h22v5.6H1zm2 2v1.6h18v-1.6z'/>"
# obrys silnějšími tahy (v náklonu se tenké linky rozpadají)
PAPER_HAT_OUTLINE_THICK = ("<path fill-rule='evenodd' d='M12 1.5 21.5 15h-19zm0 4.4-5.3 7.3h10.6z'/>"
                           "<path fill-rule='evenodd' d='M1 16.6h22v5.6H1zm2.4 2.1v1.4h17.2v-1.4z'/>")
PAPER_HAT_SOLID = "<path d='M12 1.5 21.5 15h-19z'/><path d='M1 16.6h22v5.2H1z'/>"
PAPER_HAT = "<path fill-rule='evenodd' d='M12 1.5 21.5 15.5h-19zM10 8h4v1.4h-4zm-2 3h8v1.4H8z'/>" + PAPER_HAT_BAND


def _tilt(body: str, deg: int) -> str:
    """Nakloněná čepice (jak ji nosí Večerníček) – otočená kolem středu, zmenšená, ať se vejde do 24×24."""
    return ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'>"
            f"<g transform='translate(12 12.4) rotate({deg}) scale(0.9) translate(-12 -11.7)'>{body}</g></svg>")


CUSTOM = {
    # papírová čepice z novin (Večerníček): plná, dva bílé řádky novin, páska s linkou
    "paper-hat": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'>" + PAPER_HAT + "</svg>",
    # nakloněná doleva / doprava
    # (silueta bez linek – tenké linky by se v náklonu rozpadly do schodů)
    "paper-hat-tilt-left": _tilt(PAPER_HAT_SOLID, -14),
    "paper-hat-tilt-right": _tilt(PAPER_HAT_SOLID, 14),
    "paper-hat-outline-tilt-left": _tilt(PAPER_HAT_OUTLINE_THICK, -14),
    "paper-hat-outline-tilt-right": _tilt(PAPER_HAT_OUTLINE_THICK, 14),
    # tři řádky novin, plná páska
    "paper-hat-3": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'>"
                   "<path fill-rule='evenodd' d='M12 1.5 21.5 15.5h-19zM10.6 6.6h2.8V8h-2.8zM9 9.4h6v1.4H9zm-1.6 2.8h9.2v1.4H7.4z'/>"
                   "<path d='M1 16.2h22v5.6H1z'/></svg>",
    # obrys s černými řádky novin
    "paper-hat-outline": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'>"
                         "<path fill-rule='evenodd' d='M12 1.5 21.5 15.5h-19zm0 3.6-6.2 8.8h12.4z'/>"
                         "<path d='M10.5 9h3v1.4h-3zm-1.7 2.6h6.4V13H8.8z'/>"
                         + PAPER_HAT_BAND + "</svg>",
}
