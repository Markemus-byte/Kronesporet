"""Meld fra til søkemotorene om nye og endrede sider med IndexNow (https://www.indexnow.org).

IndexNow tas imot av Bing (som også forsyner DuckDuckGo, Yahoo og Ecosia), Yandex, Seznam og Naver. Google bruker
sidekartet i Search Console i stedet. Ingen konto trengs: nøkkelfilen docs/<INDEXNOW_KEY>.txt (src/web/build.py)
viser at vi eier domenet.

Kjøres av .github/workflows/nettside.yml etter at docs/ er committet på main:

  python scripts/indexnow.py --docs docs --changed-since HEAD~1   # sidene som ble endret i siste commit
  python scripts/indexnow.py --docs docs --all                    # alle sidene i sitemap.xml

Venter først til GitHub Pages viser den nye forsiden og nøkkelfilen (inntil --wait sekunder), så søkemotorene ikke
henter gamle sider. Feil stopper ikke publiseringen; de står i loggen.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.web.build import INDEXNOW_KEY  # noqa: E402

ENDPOINT = "https://api.indexnow.org/indexnow"
BATCH = 10_000  # største antall adresser i ett kall


def sitemap_urls(docs: Path) -> list[str]:
    return re.findall(r"<loc>([^<]+)</loc>", (docs / "sitemap.xml").read_text(encoding="utf-8"))


def base_url(docs: Path) -> str:
    cname = (docs / "CNAME").read_text().strip() if (docs / "CNAME").exists() else ""
    if cname:
        return f"https://{cname}/"
    urls = sitemap_urls(docs)
    if not urls:
        sys.exit("Fant ingen adresse i sitemap.xml")
    return re.match(r"https?://[^/]+/", urls[0]).group(0)


def changed_urls(docs: Path, since: str, base: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", since, "HEAD", "--", str(docs)],
                         capture_output=True, text=True, check=True, cwd=ROOT).stdout.split()
    prefix = docs.resolve().relative_to(ROOT).as_posix() + "/"
    urls = []
    for name in out:
        if not name.endswith(".html") or name.endswith("404.html") or not (ROOT / name).exists():
            continue
        rel = name[len(prefix):]
        urls.append(base + ("" if rel == "index.html" else rel))
    return urls


def wait_until_live(docs: Path, base: str, seconds: int) -> bool:
    """True når nøkkelfilen og den nye forsiden er ute på nettet."""
    want = (docs / "index.html").read_text(encoding="utf-8")
    deadline = time.time() + seconds
    while True:
        try:
            key = requests.get(f"{base}{INDEXNOW_KEY}.txt", timeout=30)
            front = requests.get(base, timeout=30, headers={"Cache-Control": "no-cache"})
            if key.ok and key.text.strip() == INDEXNOW_KEY and front.ok and front.text == want:
                return True
        except requests.RequestException as e:
            print(f"Venter på GitHub Pages: {e}")
        if time.time() > deadline:
            return False
        time.sleep(20)


def submit(base: str, urls: list[str]) -> bool:
    host = re.match(r"https?://([^/]+)/", base).group(1)
    ok = True
    for i in range(0, len(urls), BATCH):
        chunk = urls[i:i + BATCH]
        r = requests.post(ENDPOINT, timeout=60, json={
            "host": host, "key": INDEXNOW_KEY, "keyLocation": f"{base}{INDEXNOW_KEY}.txt", "urlList": chunk})
        # 200 = tatt imot, 202 = tatt imot, nøkkelen sjekkes senere
        print(f"IndexNow: {len(chunk)} adresser → {r.status_code} {r.text[:200]}")
        ok &= r.status_code in (200, 202)
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs", type=Path, default=ROOT / "docs")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Alle sidene i sitemap.xml")
    group.add_argument("--changed-since", metavar="REF", help="Sidene endret siden denne git-committen")
    ap.add_argument("--wait", type=int, default=900, help="Sekunder å vente på at GitHub Pages er oppdatert")
    args = ap.parse_args()
    if not INDEXNOW_KEY:
        print("INDEXNOW_KEY er tom i src/web/build.py; ingenting meldes")
        return
    base = base_url(args.docs)
    urls = sitemap_urls(args.docs) if args.all else changed_urls(args.docs, args.changed_since, base)
    if not urls:
        print("Ingen endrede sider å melde fra om")
        return
    if not wait_until_live(args.docs, base, args.wait):
        print(f"GitHub Pages viste ikke den nye versjonen innen {args.wait} s; melder fra likevel")
    if not submit(base, urls):
        print("::warning::IndexNow tok ikke imot alle adressene (se over)")


if __name__ == "__main__":
    main()
