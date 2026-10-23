"""Bygg Kronesporet.no som statiske sider fra analysedatabasen.

Eksempel (fra repo-roten), etter scripts/run_phase1.py:
  python scripts/build_site.py --db data/offentlig_forbruk.db --out docs

Sidene i docs/ kan publiseres direkte med GitHub Pages
(Settings -> Pages -> Deploy from a branch -> main, /docs).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.join_and_flag import THRESHOLD  # noqa: E402
from src.web.build import build_site  # noqa: E402
from src.web.data import load_dataset  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "offentlig_forbruk.db")
    ap.add_argument("--out", type=Path, default=ROOT / "docs", help="Mappen nettstedet skrives til")
    ap.add_argument("--year", type=int, help="Regnskapsår (standard: siste år i databasen)")
    ap.add_argument("--threshold", type=float, default=THRESHOLD, help="Terskelen flaggene ble laget med")
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"Fant ikke {args.db}. Kjør scripts/run_phase1.py først, eller gi --db.")
    ds = load_dataset(args.db, year=args.year, threshold=args.threshold)
    pages = build_site(ds, args.out)
    flagged = sum(a.flagged for a in ds.agencies)
    print(f"{pages} sider skrevet til {args.out} ({len(ds.agencies)} virksomheter, {ds.year}, "
          f"{flagged} med avvik over {ds.threshold:.0%}"
          + (f", {sum(1 for a in ds.agencies if a.coverage and a.coverage.flagged)} med uvanlig dekning"
             if ds.has_coverage else "") + ")")


if __name__ == "__main__":
    main()
