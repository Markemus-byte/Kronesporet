"""Last ned nyeste «Rådata <måned> <år> (bevilgningsregnskap)» fra statsregnskapet.

Leter etter xlsx-lenker til rådata på nedlastingssidene og henter filene som er nyere enn det
som allerede er lastet inn (per år). Nettstedet stenger av og til for automatiske kall; da
feiler hentingen med en tydelig melding, og den månedlige oppdateringen fortsetter med Doffin.

  python scripts/fetch_raadata.py --out data/raadata          # lister og laster ned det nyeste
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.web.data import MONTHS  # noqa: E402

log = logging.getLogger("raadata")
PAGES = (
    "https://statsregnskapet.dfo.no/last-ned",
    "https://dfo.no/fagomrader/rapportering-til-statsregnskapet",
)
USER_AGENT = "Kronesporet/0.3 (+https://kronesporet.no; aapne data under NLOD)"
_LINK = re.compile(r"""<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)


def _period(text: str) -> tuple[int, int] | None:
    """(år, måned) fra en lenke eller lenketekst: «Rådata august 2026», «raadata_desember_2025…»."""
    t = unquote(text).lower()
    if not re.search(r"r(aa|å)data", t):
        return None
    m = re.search(r"(" + "|".join(MONTHS) + r")[\s_-]+(20\d\d)", t) or re.search(r"(20\d\d)[\s_-]+(" + "|".join(MONTHS) + r")", t)
    if not m:
        return None
    a, b = m.groups()
    month, year = (a, b) if a in MONTHS else (b, a)
    return int(year), MONTHS.index(month) + 1


def find_links(session: requests.Session) -> dict[tuple[int, int], str]:
    """(år, måned) -> nedlastingsadresse for rådatafilene som er lenket fra nedlastingssidene."""
    found: dict[tuple[int, int], str] = {}
    errors = []
    for page in PAGES:
        try:
            r = session.get(page, timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            errors.append(f"{page}: {e}")
            continue
        for href, text in _LINK.findall(r.text):
            label = re.sub(r"<[^>]+>", " ", unescape(text))
            period = _period(href) or _period(label)
            if period and ("xlsx" in href.lower() or "bevilgning" in (href + label).lower()):
                found.setdefault(period, urljoin(page, unescape(href)))
    if not found and errors:
        raise RuntimeError("; ".join(errors))
    if not found:
        raise RuntimeError("fant ingen rådatalenker på " + ", ".join(PAGES))
    return found


def fetch_latest(out: Path, have: dict[int, int], session: requests.Session | None = None) -> list[Path]:
    """Last ned den nyeste filen for hvert år som er nyere enn `have` (år -> måned). Returnerer filene."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    links = find_links(session)
    log.info("Rådata på nettstedet: %s", ", ".join(f"{y}-{m:02d}" for y, m in sorted(links)))
    newest: dict[int, int] = {}
    for year, month in links:
        newest[year] = max(newest.get(year, 0), month)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for year, month in sorted(newest.items()):
        if have.get(year, 0) >= month or (have and year < min(have)):
            continue
        path = out / f"raadata_{MONTHS[month - 1]}_{year}_bevilgningsregnskap.xlsx"
        r = session.get(links[(year, month)], timeout=300)
        r.raise_for_status()
        if not r.content.startswith(b"PK"):
            raise RuntimeError(f"{links[(year, month)]} ga ikke en xlsx-fil")
        path.write_bytes(r.content)
        log.info("Lastet ned %s (%d kB)", path.name, len(r.content) // 1000)
        files.append(path)
    return files


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "raadata")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for path in fetch_latest(a.out, {}):
        print(path)


if __name__ == "__main__":
    main()
