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
import json
import math
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


# ---- kartet (Norge med naboland) ----------------------------------------------
# Landegrenser fra Natural Earth 1:50m (offentlig eie) via world-atlas, lastes ned første gang.
WORLD_ATLAS = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json"
CACHE = Path.home() / ".cache/kronesporet/countries-50m.json"


def world_atlas() -> str:
    if not CACHE.exists():
        import urllib.request
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(WORLD_ATLAS, CACHE)
    return str(CACHE)


def load_topo(path, ids):
    t = json.load(open(path)); tr = t["transform"]; sx, sy = tr["scale"]; tx, ty = tr["translate"]
    arcs = []
    for a in t["arcs"]:
        x = y = 0; pts = []
        for dx, dy in a:
            x += dx; y += dy; pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    def arc(i):
        return arcs[i] if i >= 0 else arcs[~i][::-1]
    def ring(r):
        pts = []
        for i in r:
            a = arc(i); pts.extend(a if not pts else a[1:])
        return pts
    out = {}
    for g in t["objects"]["countries"]["geometries"]:
        if g.get("id") in ids:
            polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
            out[g["id"]] = [[ring(r) for r in p] for p in polys]
    return out


LAT0 = 64.5; LON0 = 17.0; K = math.cos(math.radians(LAT0))
IDS = {"578": "NO", "752": "SE", "246": "FI", "208": "DK", "643": "RU", "233": "EE", "428": "LV", "440": "LT", "276": "DE", "826": "GB", "352": "IS"}

def proj(lon, lat, scale, cx, cy):
    return cx + (lon - LON0) * K * scale, cy - (lat - LAT0) * scale

def dp(pts, eps):
    if len(pts) < 3: return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1; L = math.hypot(dx, dy) or 1e-9
    i, dmax = 0, -1
    for j in range(1, len(pts) - 1):
        d = abs(dy * pts[j][0] - dx * pts[j][1] + x2 * y1 - y2 * x1) / L
        if d > dmax: i, dmax = j, d
    if dmax > eps:
        return dp(pts[:i + 1], eps)[:-1] + dp(pts[i:], eps)
    return [pts[0], pts[-1]]

def area(r):
    return abs(sum(r[i][0] * r[i - 1][1] - r[i - 1][0] * r[i][1] for i in range(len(r)))) / 2

def paths(src, size, scale, eps, min_area, cx=None, cy=None, bbox_pad=8):
    cx = size / 2 if cx is None else cx; cy = size / 2 if cy is None else cy
    data = load_topo(src, set(IDS))
    out = {}
    for cid, polys in data.items():
        d = []
        for poly in polys:
            for k, ring in enumerate(poly):
                pts = [proj(lon, lat, scale, cx, cy) for lon, lat in ring if -30 < lon < 60]
                if len(pts) < 3: continue
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                if max(xs) < -bbox_pad or min(xs) > size + bbox_pad or max(ys) < -bbox_pad or min(ys) > size + bbox_pad: continue
                if area(pts) < min_area: continue
                if pts[0] == pts[-1]: pts = pts[:-1]
                far = max(range(len(pts)), key=lambda j: (pts[j][0]-pts[0][0])**2 + (pts[j][1]-pts[0][1])**2)
                s = dp(pts[:far + 1], eps)[:-1] + dp(pts[far:] + [pts[0]], eps)[:-1]
                if len(s) < 3: continue
                d.append("M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in s) + "Z")
        out[IDS[cid]] = "".join(d)
    return out


_map_cache = {}


def _map(simple: bool):
    if simple not in _map_cache:
        eps, min_area = (0.55, 3.0) if simple else (0.14, 0.25)
        _map_cache[simple] = paths(world_atlas(), 64, SCALE, eps, min_area, cx=CX, cy=CY)
    return _map_cache[simple]


SCALE = 64 / 15.4
CX, CY = 64 * 0.47, 64 * 0.49
HUB = (10.3, 59.75)  # Oslo-området
RAYS = [(-0.04, 0.52), (-0.02, 0.98), (0.42, 1.04), (0.86, 1.04), (1.04, 0.70)]


def icon_d(bg=True, simple=False, norway_only=False):
    """D: Norgeskartet — Norge i hvitt med nabolandene rundt, en krone-mynt i Oslo-området
    og spor som går ut fra Norge. simple=True er for favicon (16–32 px): grovere kyst, ingen tekst."""
    p = _map(simple)
    if norway_only:
        return [f'<path d="{p["NO"]}" fill="#fff"/>']
    hx, hy = proj(*HUB, SCALE, CX, CY)
    out = []
    if bg:
        out.append('<defs><clipPath id="ks-ikon"><rect width="64" height="64" rx="14"/></clipPath></defs>'
                   '<g clip-path="url(#ks-ikon)">')
    out.append(f'<rect width="64" height="64" fill="{GREEN}"/>')
    for k in ("SE", "FI", "DK", "RU", "EE", "LV", "LT", "DE", "GB"):
        if p.get(k):
            out.append(f'<path d="{p[k]}" fill="#fff" fill-opacity=".16"/>')
    for ex, ey in RAYS:
        if simple:
            out.append(f'<path d="M{hx:.2f} {hy:.2f}L{ex * 64:.2f} {ey * 64:.2f}" stroke="{AMBER}" '
                       f'stroke-width="2.2" stroke-linecap="round"/>')
        else:
            out.append(f'<path d="M{hx:.2f} {hy:.2f}L{ex * 64:.2f} {ey * 64:.2f}" stroke="{AMBER}" '
                       f'stroke-width="1.4" stroke-linecap="round" stroke-dasharray="0.05 2.9"/>')
    out.append(f'<path d="{p["NO"]}" fill="#fff"/>')
    r = 7.7 if not simple else 8.5
    out.append(f'<circle cx="{hx:.2f}" cy="{hy:.2f}" r="{r}" fill="{AMBER}" stroke="{GREEN}" stroke-width="1.3"/>')
    if not simple:
        kr, w = text_path("kr", "xbold", 7.7, 0, 0)
        out.append(f'<path transform="translate({hx - w / 2:.2f} {hy + 2.75:.2f})" d="{kr}" fill="{DARK}"/>')
    if bg:
        out.append("</g>")
    return out

ICONS = {"a": icon_a, "b": icon_b, "c": icon_c, "d": icon_d}
NAMES = {"a": "Mynt med spor", "b": "K-sporet", "c": "Lupen", "d": "Norgeskartet"}

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

VALGT = "d"
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
    mark = svg(64, 64, icon_d(norway_only=True) if key == "d" else ICONS[key](bg=False))
    logo, W = horizontal(key, dark=True)
    if key == "d":  # stort kart til høyre, tonet inn mot teksten
        big = svg(64, 64, icon_d(bg=False))
        art = (f'<div style="position:absolute;right:-110px;top:0">{sized(big, 630, 630)}</div>'
               f'<div style="position:absolute;left:680px;top:0;width:110px;height:630px;'
               f'background:linear-gradient(90deg,{DARK},rgba(12,59,50,0))"></div>')
        width = 600
    else:
        art = f'<div style="position:absolute;right:-60px;bottom:-110px;opacity:.09">{sized(mark, 400, 400)}</div>'
        width = 900
    return f"""<div style="width:1200px;height:630px;background:{DARK};color:#f4f1ea;font-family:Inter,sans-serif;
position:relative;overflow:hidden;box-sizing:border-box;padding:84px 90px">
<div style="height:96px;position:relative;z-index:1">{sized(logo, round(W * 1.5), 96)}</div>
<div style="font-size:62px;font-weight:700;letter-spacing:-.02em;line-height:1.08;margin-top:60px;max-width:{width}px;position:relative;z-index:1">
Hvor går statens penger?</div>
<div style="font-size:30px;color:#b8d2ca;margin-top:22px;max-width:{width - 40}px;line-height:1.35;position:relative;z-index:1">
Den mest komplette oversikten over offentlige innkjøp i Norge.</div>
<div style="position:absolute;left:90px;bottom:60px;font-size:26px;font-weight:600;color:#6fc7b0">kronesporet.no</div>
{art}
</div>"""


def main(key: str) -> None:
    ic = svg(64, 64, ICONS[key]())
    # Kartet har en forenklet utgave for små størrelser (fanen i nettleseren, toppen av nettsiden).
    small = svg(64, 64, icon_d(simple=True)) if key == "d" else ic
    (ASSETS / "favicon.svg").write_text(small)
    logo, W = horizontal(key)
    (ASSETS / "logo.svg").write_text(logo)
    dark, _ = horizontal(key, dark=True)
    (ASSETS / "logo-mork.svg").write_text(dark)
    render(sized(ic, 512, 512), ASSETS / "ikon-512.png", 512, 512)
    render(sized(small, 32, 32), ASSETS / "favicon-32.png", 32, 32)
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
