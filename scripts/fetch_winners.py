"""Hent leverandørene (vinnerne) for Doffin-kontraktene i databasen.

Ett detaljkall per kunngjøring (1,2 s mellom kall, rå svar caches i
data/doffin_cache/), så det tar omtrent en halvtime for de 1 600 kontraktene
som løper i 2025 for virksomhetene med regnskapstall. Kan avbrytes og
startes igjen; kontrakter som allerede har vinnere hoppes over.

Eksempel (fra repo-roten):
  python scripts/fetch_winners.py --db data/offentlig_forbruk.db --year 2025
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collectors.doffin import DoffinClient, collect_winners, init_db  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "offentlig_forbruk.db")
    ap.add_argument("--year", type=int,
                    help="Bare kontrakter som løper i dette året, for virksomhetene med regnskapstall")
    ap.add_argument("--all", action="store_true", help="Alle kontraktene i databasen")
    ap.add_argument("--refresh", action="store_true", help="Hent på nytt også for kontrakter som har vinnere")
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "doffin_cache")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.year and not args.all:
        ap.error("gi --year eller --all")

    init_db(args.db).close()  # oppretter doffin_winners i eldre databaser
    conn = sqlite3.connect(args.db)
    try:
        buyers, running_in, year_from = None, None, None
        if args.year:
            annualized = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'doffin_contract_years'").fetchone() is not None
            running_in, year_from = (args.year, None) if annualized else (None, args.year)
            buyers = {r[0] for r in conn.execute(
                "SELECT orgnr FROM contract_spend_comparison WHERE year = ?", (args.year,))}
        done, saved = collect_winners(conn, DoffinClient(cache_dir=args.cache_dir), buyers=buyers,
                                      year_from=year_from, running_in=running_in, refresh=args.refresh)
        with_orgnr = conn.execute("SELECT COUNT(*) FROM doffin_winners WHERE orgnr IS NOT NULL").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM doffin_winners").fetchone()[0]
    finally:
        conn.close()
    print(f"{done} kunngjøringer behandlet, {saved} vinnere lagret "
          f"({total} i alt, {with_orgnr} med orgnr) i {args.db}")


if __name__ == "__main__":
    main()
