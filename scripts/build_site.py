"""Bygg Kronesporet.no som statiske sider fra analysedatabasen.

Eksempel (fra repo-roten), etter scripts/run_phase1.py:
  python scripts/build_site.py --db data/offentlig_forbruk.db --out docs

Sidene i docs/ kan publiseres direkte med GitHub Pages
(Settings -> Pages -> Deploy from a branch -> main, /docs).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.join_and_flag import THRESHOLD  # noqa: E402
from src.web.build import build_site  # noqa: E402
from src.web.data import available_years, load_breadth, load_dataset  # noqa: E402


def sql_to_db(sql_path: Path, tmp_dir: str) -> Path:
    """Les en SQL-eksport (scripts/export_site_data.py) inn i en midlertidig SQLite-fil."""
    db = Path(tmp_dir) / "kronesporet.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(sql_path.read_text(encoding="utf-8"))
    finally:
        conn.close()
    return db


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "offentlig_forbruk.db",
                    help="SQLite-databasen, eller en .sql-eksport som site-data/kronesporet.sql")
    ap.add_argument("--out", type=Path, default=ROOT / "docs", help="Mappen nettstedet skrives til")
    ap.add_argument("--year", type=int, nargs="*",
                    help="Regnskapsår som skal med (standard: alle årene i databasen; siste blir forsiden)")
    ap.add_argument("--base-url", default="https://markemus-byte.github.io/Kronesporet/",
                    help="Offentlig adresse (canonical-lenker, Open Graph og sidekart); tom for å slå av")
    ap.add_argument("--cname", help="Eget domene for GitHub Pages, f.eks. kronesporet.no (skriver docs/CNAME)")
    ap.add_argument("--threshold", type=float, default=THRESHOLD, help="Terskelen flaggene ble laget med")
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"Fant ikke {args.db}. Kjør scripts/run_phase1.py først, eller gi --db.")
    with tempfile.TemporaryDirectory() as tmp:
        db = sql_to_db(args.db, tmp) if args.db.suffix == ".sql" else args.db
        build(args, db)


def build(args: argparse.Namespace, db: Path) -> None:
    years = args.year or available_years(db)
    if not years:
        sys.exit(f"{args.db}: contract_spend_comparison er tom. Kjør scripts/run_phase1.py først.")
    datasets = [load_dataset(db, year=y, threshold=args.threshold) for y in sorted(years)]
    ds = next((d for d in reversed(datasets) if not d.partial), datasets[-1])  # forsiden
    base_url = f"https://{args.cname.strip()}/" if args.cname else args.base_url
    pages = build_site(datasets, args.out, base_url=base_url, breadth=load_breadth(db))
    if args.cname:
        (args.out / "CNAME").write_text(args.cname.strip() + "\n")
    flagged = sum(a.flagged for a in ds.agencies)
    print(f"{pages} sider skrevet til {args.out} (år {', '.join(map(str, sorted(years)))}; "
          f"{len(ds.agencies)} virksomheter i {ds.year}, "
          f"{flagged} med avvik over {ds.threshold:.0%}"
          + (f", {sum(1 for a in ds.agencies if a.coverage and a.coverage.flagged)} med uvanlig dekning"
             if ds.has_coverage else "") + ")")


if __name__ == "__main__":
    main()
