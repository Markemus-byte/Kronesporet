"""Kronesporet fase 1: Doffin-innsamling -> lagring -> join mot statsregnskapet -> flagging.

Eksempel (fra repo-roten):
  python scripts/run_phase1.py --year 2025 --statsregnskap data/statsregnskap_2025.csv

  # Uten statsregnskap-fil: kjøp av varer og tjenester fra årsrapportene i Kudos
  python scripts/run_phase1.py --year 2025 --skip-collect --kudos

  # DFØs månedsrapport (xlsx, hittil i år): Doffin hentes for samme måneder
  python scripts/run_phase1.py --year 2026 --to-month 8 \
      --statsregnskap data/utgifter_til_staten_august_2026.xlsx

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
from src.collectors.kudos_arsrapport import load_kudos_purchases  # noqa: E402
from src.collectors.statsregnskap import load_utgifter_xlsx  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=date.today().year - 1, help="Publiseringsår i Doffin")
    ap.add_argument("--statsregnskap", type=Path, help="Sti til nedlastet statsregnskap-CSV")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "offentlig_forbruk.db")
    ap.add_argument("--threshold", type=float, default=THRESHOLD, help="Avviksterskel (0.05 = 5 %%)")
    ap.add_argument("--no-details", action="store_true",
                    help="Raskt: bruk anslått verdi fra søket i stedet for tildelt verdi (ett kall per kunngjøring)")
    ap.add_argument("--max-notices", type=int, help="Stopp etter N kunngjøringer (for testing)")
    ap.add_argument("--kudos", action="store_true",
                    help="Bruk kjøp av varer og tjenester fra årsrapportene i Kudos i stedet for en statsregnskap-fil")
    ap.add_argument("--kudos-top", type=int, default=0,
                    help="Bare de N Doffin-kjøperne med størst kontraktsverdi (0 = alle)")
    ap.add_argument("--to-month", type=int, default=12,
                    help="Hent Doffin bare januar til og med denne måneden (match hittil-i-år-regnskap)")
    ap.add_argument("--posts", default="1-49",
                    help="Postene som tas med fra xlsx-rapporten, f.eks. 1-49 (drift og investering) eller 1-99")
    ap.add_argument("--no-brreg", action="store_true",
                    help="Ikke slå opp kapittelnavn i Enhetsregisteret (bare navnetreff mot Doffin)")
    ap.add_argument("--top", type=int, default=10, help="Vis de N største flaggene")
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
            contracts = collect(a.year, fetch_details=not a.no_details, max_notices=a.max_notices,
                                to_month=a.to_month)
            saved = save_contracts(conn, contracts)
            print(f"[2/3] Lagret {saved} kontrakter i {a.db}")
        else:
            print("[1-2/3] Hopper over innsamling, bruker eksisterende Doffin-data")

        # 3. Join + flagging
        if a.kudos:
            print(f"[3/3] Henter årsrapporter {a.year} fra Kudos (topp {a.kudos_top or 'alle'} kjøpere) ...")
            tried, found = load_kudos_purchases(conn, a.year, a.kudos_top or None)
            print(f"      Kjøpstall funnet for {found} av {tried}, flagger avvik over {a.threshold:.0%} ...")
            flag_deviations(conn, a.threshold)
        elif a.statsregnskap is None:
            print("[3/3] Ingen --statsregnskap eller --kudos gitt, hopper over flagging.")
        else:
            columns = {f: getattr(a, f"col_{f}") for f in ("year", "orgnr", "name", "amount", "artskonto")
                       if getattr(a, f"col_{f}")}
            try:
                if a.statsregnskap.suffix.lower() == ".xlsx":
                    lo, _, hi = a.posts.partition("-")
                    yr, month, rows = load_utgifter_xlsx(conn, a.statsregnskap, range(int(lo), int(hi or lo) + 1),
                                                         use_brreg=not a.no_brreg)
                    print(f"      Regnskap {yr} januar-{month:02d}, poster {a.posts}")
                else:
                    rows = load_statsregnskap_csv(conn, a.statsregnskap, columns or None,
                                                  tuple(a.artskonto_prefix))
            except (FileNotFoundError, ValueError) as e:
                print(f"Feil i statsregnskap-filen: {e}", file=sys.stderr)
                return 1
            print(f"[3/3] Lastet {rows} rader fra {a.statsregnskap.name}, flagger avvik over {a.threshold:.0%} ...")
            flag_deviations(conn, a.threshold)

        s = summary(conn)
        top = conn.execute(
            "SELECT description FROM flags f JOIN contract_spend_comparison c "
            "ON f.source_id = c.orgnr || ':' || c.year WHERE f.flag_type = 'doffin_avvik' "
            "ORDER BY ABS(c.contract_sum - c.spend_sum) DESC LIMIT ?", (a.top,)
        ).fetchall() if a.statsregnskap or a.kudos else []
    finally:
        conn.close()

    print()
    print(f"Kontrakter i databasen: {s['contracts']} ({s['contracts_with_value']} med verdi)")
    print(f"Virksomhet/år koblet:   {s['matched_agency_years']}")
    print(f"Flagg (avvik > {a.threshold:.0%}):   {s['flags']}")
    if top:
        print(f"\nStørste avvik i kroner (topp {len(top)}):")
        for (desc,) in top:
            print(f"  - {desc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
