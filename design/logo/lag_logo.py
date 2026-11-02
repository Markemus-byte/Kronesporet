"""Lager logoen til Kronesporet (SVG med tekst som paths, fonten Inter) og PNG-variantene.

Bare nødvendig når logoen skal endres; filene ligger ferdige i src/web/assets/.
Krever fontTools og uharfbuzz (pip install fonttools uharfbuzz), fonten Inter og Node med Playwright:

    python design/logo/lag_logo.py          # valgt ikon (VALGT under)
    python design/logo/lag_logo.py a        # et annet av forslagene
"""
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from pathlib import Path
import subprocess
import sys

FONTS = {"bold": "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
         "xbold": "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf",
         "semi": "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf",
         "dbold": "/usr/share/fonts/opentype/inter/InterDisplay-Bold.otf"}
_cache = {}

def text_path(text, weight, size, x, y, track=0.0):
    """Returnerer (svg path d, bredde) for tekst med baseline i y."""
    path = FONTS[weight]
    if path not in _cache:
        blob = hb.Blob.from_file_path(path)
        _cache[path] = (hb.Font(hb.Face(blob)), TTFont(path))
    hfont, tt = _cache[path]
    upem = tt["head"].unitsPerEm
    buf = hb.Buffer(); buf.add_str(text); buf.guess_segment_properties()
    hb.shape(hfont, buf, {"kern": True, "liga": True})
    gs = tt.getGlyphSet(); order = tt.getGlyphOrder()
    pen = SVGPathPen(gs)
    s = size / upem
    cx = 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        name = order[info.codepoint]
        tp = TransformPen(pen, (s, 0, 0, -s, x + (cx + pos.x_offset) * s, y - pos.y_offset * s))
        gs[name].draw(tp)
        cx += pos.x_advance + track * upem
    return pen.getCommands(), (cx - track * upem) * s

GREEN = "#0f5c4d"; DARK = "#0c3b32"; INK = "#1c1f23"; AMBER = "#e8a84a"; CREAM = "#fbfaf7"

# ---- ikonene (64x64 koordinater) ---------------------------------------------
def icon_a(bg=True):
    """A: Mynt med spor — en krone-mynt som har lagt igjen et spor av prikker."""
    kr, w = text_path("kr", "xbold", 15, 0, 0)
    out = [f'<rect width="64" height="64" rx="14" fill="{GREEN}"/>'] if bg else []
    out += [
        f'<path d="M8 55 C 14 50, 18 46, 24 42" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-dasharray="0 6.5" opacity=".8"/>',
        f'<circle cx="39" cy="28" r="17" fill="#fff"/>',
        f'<circle cx="39" cy="28" r="13.2" fill="none" stroke="{GREEN}" stroke-width="1.6"/>',
        f'<path transform="translate({39 - w/2:.2f} 33.4)" d="{kr}" fill="{GREEN}"/>',
    ]
    return out

def icon_b(bg=True):
    """B: K-sporet — en K tegnet som et spor med knutepunkter; ett punkt flagget."""
    fg = "#fff"
    out = [f'<rect width="64" height="64" rx="14" fill="{GREEN}"/>'] if bg else []
    out += [
        f'<g fill="none" stroke="{fg}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round">',
        '<path d="M21 14 V50"/>',
        '<path d="M21 34 L39 18"/>',
        '<path d="M29 27 L40 46"/>',
        '</g>',
        f'<circle cx="43" cy="16" r="5.5" fill="{fg}"/>',
        f'<circle cx="42" cy="49" r="5.5" fill="{AMBER}"/>',
    ]
    return out

def icon_c(bg=True):
    """C: Lupen — en lupe som granskar kronene."""
    kr, w = text_path("kr", "xbold", 13, 0, 0)
    out = [f'<rect width="64" height="64" rx="14" fill="{GREEN}"/>'] if bg else []
    out += [
        f'<path d="M37.5 37.5 L50 50" stroke="{AMBER}" stroke-width="7" stroke-linecap="round"/>',
        f'<circle cx="27" cy="27" r="14.5" fill="{GREEN}" stroke="#fff" stroke-width="5"/>',
        f'<path transform="translate({27 - w/2:.2f} 31.6)" d="{kr}" fill="#fff"/>',
    ]
    return out

ICONS = {"a": icon_a, "b": icon_b, "c": icon_c}
NAMES = {"a": "Mynt med spor", "b": "K-sporet", "c": "Lupen"}

def svg(w, h, body, bg=None):
    r = f'<rect width="{w}" height="{h}" fill="{bg}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            f'{r}{"".join(body)}</svg>\n')

def horizontal(key, dark=False):
    """Ikon + ordmerke. 'Krone' i blekk, 'sporet' i grønt (lyst/mørkt tema)."""
    ink = "#f4f1ea" if dark else INK
    acc = "#6fc7b0" if dark else GREEN
    size = 40
    d1, w1 = text_path("Krone", "bold", size, 0, 0, track=-0.01)
    d2, w2 = text_path("sporet", "bold", size, 0, 0, track=-0.01)
    x0 = 64 + 16
    base = 32 + size * 0.36
    body = [f'<g>{"".join(ICONS[key]())}</g>',
            f'<path transform="translate({x0} {base:.1f})" d="{d1}" fill="{ink}"/>',
            f'<path transform="translate({x0 + w1 + 0.5:.1f} {base:.1f})" d="{d2}" fill="{acc}"/>']
    W = x0 + w1 + w2 + 4
    return svg(round(W), 64, body), round(W)

VALGT = "b"
ASSETS = Path(__file__).resolve().parents[2] / "src/web/assets"
HERE = Path(__file__).resolve().parent


def render(html: str, dst: Path, w: int, h: int, scale: int = 1) -> None:
    """Tegner HTML til PNG med Chromium (render.js)."""
    tmp = HERE / "_tmp.html"
    tmp.write_text(f'<html><head><style>html,body{{margin:0;background:transparent}}svg{{display:block}}</style>'
                   f'</head><body>{html}</body></html>')
    subprocess.run(["node", str(HERE / "render.js"), str(tmp), str(dst), str(w), str(h), str(scale)], check=True)
    tmp.unlink()


def sized(svg_text: str, w: int, h: int) -> str:
    return svg_text.replace('width="', f'data-w="', 1).replace('height="', 'data-h="', 1).replace(
        "<svg ", f'<svg width="{w}" height="{h}" ', 1)


def og_image(key: str) -> str:
    """Delingsbilde 1200x630 (Facebook, LinkedIn, Slack, iMessage)."""
    mark = svg(64, 64, ICONS[key](bg=False))
    logo, W = horizontal(key, dark=True)
    return f"""<div style="width:1200px;height:630px;background:{DARK};color:#f4f1ea;font-family:Inter,sans-serif;
position:relative;overflow:hidden;box-sizing:border-box;padding:84px 90px">
<div style="height:96px">{sized(logo, round(W * 1.5), 96)}</div>
<div style="font-size:66px;font-weight:700;letter-spacing:-.02em;line-height:1.08;margin-top:70px;max-width:900px">
Hvor går statens penger?</div>
<div style="font-size:30px;color:#b8d2ca;margin-top:22px;max-width:900px;line-height:1.35">
Den mest komplette oversikten over offentlige innkjøp: Doffin, statsregnskapet og Enhetsregisteret samlet.</div>
<div style="position:absolute;left:90px;bottom:60px;font-size:26px;font-weight:600;color:#6fc7b0">kronesporet.no</div>
<div style="position:absolute;right:-60px;bottom:-110px;opacity:.09">{sized(mark, 400, 400)}</div>
</div>"""


def main(key: str) -> None:
    ic = svg(64, 64, ICONS[key]())
    (ASSETS / "favicon.svg").write_text(ic)
    logo, W = horizontal(key)
    (ASSETS / "logo.svg").write_text(logo)
    dark, _ = horizontal(key, dark=True)
    (ASSETS / "logo-mork.svg").write_text(dark)
    render(sized(ic, 512, 512), ASSETS / "ikon-512.png", 512, 512)
    render(sized(ic, 32, 32), ASSETS / "favicon-32.png", 32, 32)
    # Apple legger på egne runde hjørner: fyll hele flaten.
    full = svg(64, 64, [f'<rect width="64" height="64" fill="{GREEN}"/>'] + ICONS[key](bg=False))
    render(sized(full, 180, 180), ASSETS / "apple-touch-icon.png", 180, 180)
    # E-post: hvit bakgrunn (Outlook viser ikke gjennomsiktighet pent), 2x for skarpe skjermer.
    render(f'<div style="background:#fff;padding:0">{sized(logo, W * 3 // 2, 96)}</div>',
           ASSETS / "logo-epost.png", W * 3 // 2, 96, 2)
    render(og_image(key), ASSETS / "og-bilde.png", 1200, 630)
    print("Skrev logoen til", ASSETS)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "forslag":
        out = Path("out"); out.mkdir(exist_ok=True)
        for k in ICONS:
            (out / f"{k}-ikon.svg").write_text(svg(64, 64, ICONS[k]()))
            (out / f"{k}-logo.svg").write_text(horizontal(k)[0])
            (out / f"{k}-logo-mork.svg").write_text(horizontal(k, dark=True)[0])
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else VALGT)
