"""Kronesporet fase 1: Doffin-innsamling -> lagring -> join mot statsregnskapet -> flagging.

Eksempel (fra repo-roten):
  python scripts/run_phase1.py --year 2025 --statsregnskap data/statsregnskap_2025.csv

Last ned statsregnskapet manuelt fra https://statsregnskapet.dfo.no/last-ned
(nettstedet stenger for automatiske tilkoblinger fra skyen). Se
src/analysis/join_and_flag.py for hvilke kolonner som forventes.

Uten --statsregnskap samles og lagres Doffin-data, men flaggingen hoppes over.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.join_and_flag import (  # noqa: E402
    THRESHOLD,
    flag_deviations,
    load_statsregnskap_csv,
    summary,
)
from src.collectors.doffin import collect, init_db, save_contracts  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=date.today().year - 1, help="Publiseringsår i Doffin")
    ap.add_argument("--statsregnskap", type=Path, help="Sti til nedlastet statsregnskap-CSV")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "offentlig_forbruk.db")
    ap.add_argument("--threshold", type=float, default=THRESHOLD, help="Avviksterskel (0.05 = 5 %%)")
    ap.add_argument("--no-details", action="store_true",
                    help="Raskt: bruk anslått verdi fra søket i stedet for tildelt verdi (ett kall per kunngjøring)")
    ap.add_argument("--max-notices", type=int, help="Stopp etter N kunngjøringer (for testing)")
    ap.add_argument("--skip-collect", action="store_true", help="Bruk Doffin-data som allerede ligger i databasen")
    ap.add_argument("--artskonto-prefix", nargs="*", default=[],
                    help="Ta bare med statsregnskap-rader med disse artskonto-prefiksene, f.eks. 6 7")
    for field in ("year", "orgnr", "name", "amount", "artskonto"):
        ap.add_argument(f"--col-{field}", help=f"Kolonnenavn for {field} i statsregnskap-CSV")
    return ap.parse_args()


def main() -> int:
    a = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conn = init_db(a.db)
    try:
        # 1. Innsamling + 2. lagring
        if not a.skip_collect:
            print(f"[1/3] Henter Doffin-kontrakter for {a.year} ...")
            contracts = collect(a.year, fetch_details=not a.no_details, max_notices=a.max_notices)
            saved = save_contracts(conn, contracts)
            print(f"[2/3] Lagret {saved} kontrakter i {a.db}")
        else:
            print("[1-2/3] Hopper over innsamling, bruker eksisterende Doffin-data")

        # 3. Join + flagging
        if a.statsregnskap is None:
            print("[3/3] Ingen --statsregnskap gitt, hopper over flagging.")
        else:
            columns = {f: getattr(a, f"col_{f}") for f in ("year", "orgnr", "name", "amount", "artskonto")
                       if getattr(a, f"col_{f}")}
            try:
                rows = load_statsregnskap_csv(conn, a.statsregnskap, columns or None, tuple(a.artskonto_prefix))
            except (FileNotFoundError, ValueError) as e:
                print(f"Feil i statsregnskap-filen: {e}", file=sys.stderr)
                return 1
            print(f"[3/3] Lastet {rows} rader fra {a.statsregnskap.name}, flagger avvik over {a.threshold:.0%} ...")
            flag_deviations(conn, a.threshold)

        s = summary(conn)
    finally:
        conn.close()

    print()
    print(f"Kontrakter i databasen: {s['contracts']} ({s['contracts_with_value']} med verdi)")
    print(f"Virksomhet/år koblet:   {s['matched_agency_years']}")
    print(f"Flagg (avvik > {a.threshold:.0%}):   {s['flags']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
