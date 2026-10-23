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

Kontraktverdier fordeles over årene kontraktene løper (src/analysis/annualize.py),
så regnskapsåret sammenlignes med årsandelen av alle kontrakter som løper det året.
Da trengs også tildelinger fra tidligere år (--history, standard 8 år) for
virksomhetene som har regnskapstall. --no-annualize gir den gamle sammenligningen
(hele kontraktsverdien i publiseringsåret).
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
from src.analysis.annualize import (  # noqa: E402
    DEFAULT_CONTRACT_MONTHS,
    DEFAULT_FRAMEWORK_MONTHS,
    annualize_contracts,
)
from src.collectors.doffin import DoffinClient, collect, init_db, save_contracts  # noqa: E402
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
    ap.add_argument("--no-annualize", action="store_true",
                    help="Ikke fordel kontraktverdi over år (hele verdien telles i publiseringsåret)")
    ap.add_argument("--history", type=int, default=8,
                    help="Hent tildelinger så mange år bakover for virksomhetene med regnskapstall (0 = ingen)")
    ap.add_argument("--framework-months", type=float, default=DEFAULT_FRAMEWORK_MONTHS,
                    help="Standardvarighet for rammeavtaler uten oppgitt periode (måneder)")
    ap.add_argument("--contract-months", type=float, default=DEFAULT_CONTRACT_MONTHS,
                    help="Standardvarighet for andre kontrakter uten oppgitt periode (måneder)")
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "doffin_cache",
                    help="Lagre rå Doffin-kunngjøringer her, så nye kjøringer slipper å hente dem på nytt")
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
            print(f"[1/4] Henter Doffin-kontrakter for {a.year} ...")
            contracts = collect(a.year, fetch_details=not a.no_details, max_notices=a.max_notices,
                                to_month=a.to_month, client=DoffinClient(cache_dir=a.cache_dir))
            saved = save_contracts(conn, contracts)
            print(f"[2/4] Lagret {saved} kontrakter i {a.db}")
        else:
            print("[1-2/4] Hopper over innsamling, bruker eksisterende Doffin-data")

        # 3. Regnskapstall
        if a.kudos:
            print(f"[3/4] Henter årsrapporter {a.year} fra Kudos (topp {a.kudos_top or 'alle'} kjøpere) ...")
            tried, found = load_kudos_purchases(conn, a.year, a.kudos_top or None)
            print(f"      Kjøpstall funnet for {found} av {tried}")
        elif a.statsregnskap is None:
            print("[3/4] Ingen --statsregnskap eller --kudos gitt, hopper over flagging.")
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
            print(f"[3/4] Lastet {rows} rader fra {a.statsregnskap.name}")

        # 4. Årsfordeling + join + flagging
        compare = a.kudos or a.statsregnskap is not None
        annualized = compare and not a.no_annualize
        if annualized:
            buyers = {r[0] for r in conn.execute(
                "SELECT DISTINCT orgnr FROM statsregnskap WHERE orgnr IS NOT NULL")}
            years = list(range(a.year - 1, a.year - a.history - 1, -1))
            # Doffin-data lagret før kontraktsperioden ble lest inn: hent året på nytt for disse kjøperne
            if a.skip_collect and conn.execute(
                "SELECT COUNT(*) FROM doffin_contracts WHERE year = ? AND value_type = 'awarded' "
                "AND is_framework IS NULL", (a.year,)
            ).fetchone()[0]:
                years.insert(0, a.year)
            if years and buyers:
                print(f"[4/4] Henter tildelinger {min(years)}-{max(years)} for {len(buyers)} virksomheter ...")
                client = DoffinClient(cache_dir=a.cache_dir)
                for y in years:
                    n = save_contracts(conn, collect(y, fetch_details=not a.no_details, buyers=buyers,
                                                     client=client))
                    print(f"      {y}: {n} kontrakter")
            until = date(a.year, a.to_month + 1, 1) if a.to_month < 12 else None
            counts = annualize_contracts(conn, a.framework_months, a.contract_months, until=until)
            print("      Kontraktsperiode fra: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        if compare:
            print(f"      Flagger avvik over {a.threshold:.0%} "
                  f"({'årsfordelt kontraktverdi' if annualized else 'hele kontraktverdien i publiseringsåret'}) ...")
            flag_deviations(conn, a.threshold, annualized=annualized)

        s = summary(conn)
        top = conn.execute(
            "SELECT description FROM flags f JOIN contract_spend_comparison c "
            "ON f.source_id = c.orgnr || ':' || c.year WHERE f.flag_type = 'doffin_avvik' "
            "ORDER BY ABS(c.contract_sum - c.spend_sum) DESC LIMIT ?", (a.top,)
        ).fetchall() if compare else []
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
