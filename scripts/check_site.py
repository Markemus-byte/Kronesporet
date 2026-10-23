"""Sjekk at alle interne lenker i den bygde nettsiden peker på filer som finnes.

Brukes av GitHub Actions etter byggingen. Eksempel: python scripts/check_site.py docs
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

LINK_RE = re.compile(r'(?:href|src)="([^"#?]+)')


def broken_links(site: Path) -> list[tuple[Path, str]]:
    bad = []
    for page in sorted(site.rglob("*.html")):
        for target in LINK_RE.findall(page.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            if not (page.parent / target).resolve().exists():
                bad.append((page.relative_to(site), target))
    return bad


def main() -> None:
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "docs")
    pages = list(site.rglob("*.html"))
    if not pages:
        sys.exit(f"Fant ingen HTML-sider i {site}")
    bad = broken_links(site)
    for page, target in bad[:50]:
        print(f"{page}: {target}")
    if bad:
        sys.exit(f"{len(bad)} brutte lenker i {len(pages)} sider")
    print(f"{len(pages)} sider, ingen brutte lenker")


if __name__ == "__main__":
    main()
