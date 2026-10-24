"""Kronesporet fase 1: Doffin-innsamling -> lagring -> join mot statsregnskapet -> flagging.

Eksempel (fra repo-roten):
  # Anbefalt: rådata fra statsregnskapet (bevilgningsregnskap, desember = hele året), flere år samtidig,
  # og årsrapporter fra Kudos for nettobudsjetterte virksomheter (universiteter m.fl.) som mangler der
  python scripts/run_phase1.py --year 2025 --kudos --statsregnskap \
      data/raadata_desember_2023_bevilgningsregnskap.xlsx \
      data/raadata_desember_2024_bevilgningsregnskap.xlsx \
      data/raadata_desember_2025_bevilgningsregnskap.xlsx

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
import sqlite3
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
from src.analysis.coverage import (  # noqa: E402
    PEER_BANDS, PERSIST_MIN, PERSIST_WINDOW, Z_THRESHOLD, flag_coverage_outliers,
)  # noqa: E402
from src.collectors.doffin import DoffinClient, collect, init_db, save_contracts  # noqa: E402
from src.collectors.kudos_arsrapport import load_kudos_purchases  # noqa: E402
from src.collectors.brreg import load_subunits, main_units, parent_map  # noqa: E402
from src.collectors.statsregnskap import (  # noqa: E402
    is_raadata, load_raadata_xlsx, load_utgifter_xlsx, raadata_orgnrs,
)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=date.today().year - 1, help="Publiseringsår i Doffin")
    ap.add_argument("--statsregnskap", type=Path, nargs="+",
                    help="Nedlastede statsregnskap-filer: rådata (xlsx, anbefalt), «Utgifter til staten» (xlsx) "
                         "eller CSV. Flere filer gir flere år, f.eks. desember 2023, 2024 og 2025")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "offentlig_forbruk.db")
    ap.add_argument("--threshold", type=float, default=THRESHOLD, help="Avviksterskel (0.05 = 5 %%)")
    ap.add_argument("--no-details", action="store_true",
                    help="Raskt: bruk anslått verdi fra søket i stedet for tildelt verdi (ett kall per kunngjøring)")
    ap.add_argument("--max-notices", type=int, help="Stopp etter N kunngjøringer (for testing)")
    ap.add_argument("--kudos", action="store_true",
                    help="Hent kjøp av varer og tjenester fra årsrapportene i Kudos for kjøpere som ikke "
                         "har tall i --statsregnskap (eller for alle, uten statsregnskap-fil)")
    ap.add_argument("--kudos-top", type=int, default=0,
                    help="Bare de N Doffin-kjøperne med størst kontraktsverdi (0 = alle)")
    ap.add_argument("--to-month", type=int, default=12,
                    help="Hent Doffin bare januar til og med denne måneden (match hittil-i-år-regnskap)")
    ap.add_argument("--posts", default="1-49",
                    help="Postene som tas med fra xlsx-rapporten, f.eks. 1-49 (drift og investering) eller 1-99")
    ap.add_argument("--no-brreg", action="store_true",
                    help="Ikke bruk Enhetsregisteret: verken kapittelnavn (xlsx-rapporten) eller underenheter "
                         "(kunngjøringer fra f.eks. politidistriktene føres da ikke på Politiet)")
    ap.add_argument("--top", type=int, default=10, help="Vis de N største flaggene")
    ap.add_argument("--z-threshold", type=float, default=Z_THRESHOLD,
                    help="Flagg Doffin-dekning som avviker mer enn så mange robuste standardavvik fra lignende virksomheter")
    ap.add_argument("--peer-bands", type=int, default=PEER_BANDS,
                    help="Antall størrelsesgrupper (etter kjøp) som virksomhetene sammenlignes innenfor")
    ap.add_argument("--persist-window", type=int, default=PERSIST_WINDOW,
                    help="Antall år bakover som vurderes for dekningsflagget (standard 3)")
    ap.add_argument("--persist-min", type=int, default=PERSIST_MIN,
                    help="Antall av disse årene med avvik samme vei som gir flagg (standard 2); færre gir observasjon")
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


def _spend_orgnrs(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT DISTINCT orgnr FROM statsregnskap WHERE orgnr IS NOT NULL")}


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
        if a.statsregnskap is None and not a.kudos:
            print("[3/4] Ingen --statsregnskap eller --kudos gitt, hopper over flagging.")
        for path in a.statsregnskap or []:
            columns = {f: getattr(a, f"col_{f}") for f in ("year", "orgnr", "name", "amount", "artskonto")
                       if getattr(a, f"col_{f}")}
            try:
                if path.suffix.lower() == ".xlsx" and is_raadata(path):
                    aliases = {} if a.no_brreg else main_units(raadata_orgnrs(path))
                    yr, month, n = load_raadata_xlsx(conn, path, aliases)
                    print(f"[3/4] Rådata {yr} januar-{month:02d}: kjøp av varer og tjenester for {n} virksomheter "
                          f"({path.name})")
                    continue
                if path.suffix.lower() == ".xlsx":
                    lo, _, hi = a.posts.partition("-")
                    yr, month, rows = load_utgifter_xlsx(conn, path, range(int(lo), int(hi or lo) + 1),
                                                         use_brreg=not a.no_brreg)
                    print(f"      Regnskap {yr} januar-{month:02d}, poster {a.posts}")
                else:
                    rows = load_statsregnskap_csv(conn, path, columns or None, tuple(a.artskonto_prefix))
            except (FileNotFoundError, ValueError) as e:
                print(f"Feil i statsregnskap-filen: {e}", file=sys.stderr)
                return 1
            print(f"[3/4] Lastet {rows} rader fra {path.name}")
        if a.statsregnskap and not a.no_brreg:
            # Underenheter først, så Kudos ikke prøver politidistrikter o.l. som egne virksomheter
            load_subunits(conn, _spend_orgnrs(conn))
        if a.kudos:
            print(f"[3/4] Henter årsrapporter {a.year} fra Kudos for virksomheter uten tall i statsregnskapet "
                  f"(topp {a.kudos_top or 'alle'} kjøpere) ...")
            tried, found = load_kudos_purchases(conn, a.year, a.kudos_top or None)
            print(f"      Kjøpstall funnet for {found} av {tried}")
        if (a.statsregnskap or a.kudos) and not a.no_brreg:
            n_sub = load_subunits(conn, _spend_orgnrs(conn))
            print(f"      {n_sub} underenheter/organisasjonsledd fra Enhetsregisteret føres på virksomheten sin")

        # 4. Årsfordeling + join + flagging
        compare = a.kudos or a.statsregnskap is not None
        annualized = compare and not a.no_annualize
        if annualized:
            buyers = _spend_orgnrs(conn) | set(parent_map(conn))
            # Alle regnskapsår (flere rådatafiler gir flere år) pluss --history år før det første,
            # så kontrakter tildelt før perioden også får sin årsandel
            spend_years = [r[0] for r in conn.execute("SELECT DISTINCT year FROM statsregnskap")] or [a.year]
            years = [y for y in range(max(spend_years), min(spend_years) - a.history - 1, -1) if y != a.year]
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
            flag_coverage_outliers(conn, a.z_threshold, a.peer_bands,
                                   persist_window=a.persist_window, persist_min=a.persist_min)

        s = summary(conn)
        top = conn.execute(
            "SELECT description FROM flags f JOIN contract_spend_comparison c "
            "ON f.source_id = c.orgnr || ':' || c.year WHERE f.flag_type = 'doffin_avvik' "
            "ORDER BY ABS(c.contract_sum - c.spend_sum) DESC LIMIT ?", (a.top,)
        ).fetchall() if compare else []
        coverage = conn.execute(
            "SELECT description FROM flags f JOIN doffin_coverage d ON f.source_id = d.orgnr || ':' || d.year "
            "WHERE f.flag_type = 'doffin_dekning' ORDER BY ABS(d.z) DESC"
        ).fetchall() if compare else []
        median_cov = conn.execute(
            "SELECT coverage FROM doffin_coverage ORDER BY coverage LIMIT 1 OFFSET "
            "(SELECT COUNT(*) FROM doffin_coverage) / 2"
        ).fetchone() if compare else None
    finally:
        conn.close()

    print()
    print(f"Kontrakter i databasen: {s['contracts']} ({s['contracts_with_value']} med verdi)")
    print(f"Virksomhet/år koblet:   {s['matched_agency_years']}")
    print(f"Flagg (avvik > {a.threshold:.0%}):   {s['flags']}")
    if compare:
        print(f"Flagg (Doffin-dekning skiller seg ut {a.persist_min} av {a.persist_window} år, |z| > {a.z_threshold:g}): "
              f"{len(coverage)}"
              + (f"  (mediandekning {median_cov[0]:.0%})" if median_cov else ""))
    if top:
        print(f"\nStørste avvik i kroner (topp {len(top)}):")
        for (desc,) in top:
            print(f"  - {desc}")
    if coverage:
        print("\nDoffin-dekning som skiller seg ut fra lignende virksomheter:")
        for (desc,) in coverage:
            print(f"  - {desc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
