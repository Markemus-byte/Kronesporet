"""Månedlig oppdatering av site-data/kronesporet.sql (kjøres av .github/workflows/oppdatering.yml).

Tar utgangspunkt i dataene som allerede ligger i repoet og legger til det som er nytt:
  1. Nye kontraktstildelinger i Doffin for virksomhetene på nettsiden (og underenhetene deres),
     med kontraktsperiode og vinnere. Kunngjøringer som allerede er lagret, hentes ikke på nytt.
  2. Nyere rådata fra statsregnskapet: xlsx-filer gitt med --raadata, eller lastet ned med
     --fetch-raadata (scripts/fetch_raadata.py). En nyere måned erstatter den forrige for samme år.
  3. Årsfordeling, avvik og Doffin-dekning regnes ut på nytt, og tallene eksporteres.

Oppdateringen holder seg til virksomhetene som allerede er på nettsiden. Nye virksomheter trenger
Doffin-historikk og underenheter fra Enhetsregisteret og legges til med scripts/run_phase1.py.

Eksempel (fra repo-roten):
  python scripts/update_site_data.py --fetch-raadata
  python scripts/update_site_data.py --raadata data/raadata_september_2026_bevilgningsregnskap.xlsx
  python scripts/update_site_data.py --max-new 5 --dry-run     # prøvekjøring, skriver ingenting
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_site import sql_to_db  # noqa: E402
from scripts.export_site_data import export  # noqa: E402
from src.analysis.annualize import annualize_contracts  # noqa: E402
from src.analysis.coverage import flag_coverage_outliers  # noqa: E402
from src.analysis.join_and_flag import THRESHOLD, flag_deviations  # noqa: E402
from src.collectors.doffin import (  # noqa: E402
    DoffinClient, DoffinError, _first_buyer_orgnr, init_db, iter_search_hits, month_ranges, normalize,
    save_contracts, save_winners, winners,
)
from src.collectors.statsregnskap import load_raadata_xlsx  # noqa: E402
from src.web.data import source_month  # noqa: E402

log = logging.getLogger("oppdatering")


def agencies(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT DISTINCT orgnr FROM statsregnskap WHERE orgnr IS NOT NULL")}


def buyers(conn: sqlite3.Connection) -> set[str]:
    """Orgnr som kan stå som kjøper i Doffin: virksomhetene og underenhetene deres."""
    return agencies(conn) | {r[0] for r in conn.execute("SELECT child FROM orgnr_parent")}


def ledger_period(conn: sqlite3.Connection) -> dict[int, int]:
    """År -> siste måned regnskapet dekker, ut fra rådatafilene i statsregnskap."""
    out: dict[int, int] = {}
    for year, source in conn.execute("SELECT DISTINCT year, source_file FROM statsregnskap"):
        month = source_month(source)
        if month:
            out[year] = max(out.get(year, 0), month)
    return out


def fetch_new_contracts(conn: sqlite3.Connection, years: list[int], client: DoffinClient,
                        max_new: int | None = None) -> int:
    """Hent tildelinger publisert i `years` som ikke ligger i databasen, med detaljer og vinnere."""
    wanted = buyers(conn)
    known = {r[0] for r in conn.execute("SELECT id FROM doffin_contracts")}
    today = date.today()
    new: dict[str, dict] = {}
    for year in years:
        to_month = today.month if year == today.year else 12
        for start, end in month_ranges(year, to_month):
            for hit in iter_search_hits(client, start, end):
                if hit["id"] not in known and _first_buyer_orgnr(hit) in wanted:
                    new.setdefault(hit["id"], hit)
        log.info("Doffin %s: %d nye kunngjøringer så langt", year, len(new))
    ids = sorted(new)[:max_new] if max_new else sorted(new)
    saved = 0
    for i, notice_id in enumerate(ids, 1):
        try:
            detail = client.notice(notice_id)
        except DoffinError as e:
            log.warning("Hopper over %s: %s", notice_id, e)
            continue
        saved += save_contracts(conn, [normalize(new[notice_id], detail)])
        save_winners(conn, notice_id, winners(detail))
        if i % 50 == 0:
            conn.commit()
            log.info("Detaljer: %d/%d", i, len(ids))
    conn.commit()
    return saved


def keep_only(conn: sqlite3.Connection, orgnrs: set[str]) -> int:
    """Fjern regnskapstall for virksomheter som ikke er på nettsiden. Returnerer antall virksomheter fjernet."""
    extra = agencies(conn) - orgnrs
    for table in ("statsregnskap", "statsregnskap_tilskudd"):
        conn.executemany(f"DELETE FROM {table} WHERE orgnr = ?", [(o,) for o in extra])
    conn.commit()
    return len(extra)


def load_ledgers(conn: sqlite3.Connection, files: list[Path]) -> list[str]:
    """Last rådatafiler som er nyere enn det som ligger inne for samme år. Returnerer beskrivelser."""
    have = ledger_period(conn)
    loaded = []
    for path in sorted(files):
        month = source_month(path.name)
        year = next((int(p) for p in path.stem.replace("-", "_").split("_") if p.isdigit() and len(p) == 4), None)
        if month and year and have.get(year, 0) >= month:
            log.info("%s: har allerede %s til og med måned %d", path.name, year, have[year])
            continue
        year, month, n = load_raadata_xlsx(conn, path)
        have[year] = month
        loaded.append(f"{year} januar–{month:02d} ({n} virksomheter, {path.name})")
    return loaded


def summary_line(conn: sqlite3.Connection) -> str:
    n_contracts = conn.execute("SELECT COUNT(*) FROM doffin_contracts").fetchone()[0]
    last = conn.execute("SELECT MAX(publication_date) FROM doffin_contracts").fetchone()[0] or "?"
    periods = ", ".join(f"{y}" + (f" (jan–{m:02d})" if m < 12 else "") for y, m in sorted(ledger_period(conn).items()))
    return f"{n_contracts} Doffin-kontrakter, siste publisert {str(last)[:10]}; statsregnskap {periods}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=ROOT / "site-data" / "kronesporet.sql")
    ap.add_argument("--raadata", type=Path, nargs="*", default=[], help="Nye rådatafiler (xlsx) fra statsregnskapet")
    ap.add_argument("--fetch-raadata", action="store_true",
                    help="Prøv å laste ned nyeste rådata fra statsregnskapet.dfo.no (scripts/fetch_raadata.py)")
    ap.add_argument("--years", type=int, nargs="*",
                    help="Publiseringsår som sjekkes i Doffin (standard: i fjor og i år)")
    ap.add_argument("--max-new", type=int, help="Hent høyst så mange nye kunngjøringer (prøvekjøring)")
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "doffin_cache")
    ap.add_argument("--dry-run", action="store_true", help="Regn ut alt, men ikke skriv site-data/")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    today = date.today()
    years = a.years or [today.year - 1, today.year]
    report: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        db = sql_to_db(a.data, tmp)
        conn = init_db(db)
        before = summary_line(conn)
        roots_before = agencies(conn)

        files = list(a.raadata)
        if a.fetch_raadata:
            from scripts.fetch_raadata import fetch_latest
            try:
                files += fetch_latest(Path(tmp) / "raadata", ledger_period(conn))
            except Exception as e:  # nettstedet stenger ofte for automatiske kall; Doffin oppdateres uansett
                log.warning("Kunne ikke hente rådata fra statsregnskapet: %s", e)
                report.append(f"Rådata: kunne ikke hentes automatisk ({e}).")
        for line in load_ledgers(conn, files):
            report.append(f"Rådata lastet: {line}")

        dropped = keep_only(conn, roots_before)
        if dropped:
            log.info("%d virksomheter i rådata er ikke på nettsiden og er holdt utenfor", dropped)

        n_new = fetch_new_contracts(conn, years, DoffinClient(cache_dir=a.cache_dir), a.max_new)
        report.append(f"Doffin: {n_new} nye kontrakter ({', '.join(map(str, years))}).")

        periods = ledger_period(conn)
        latest = max(periods) if periods else today.year
        until = date(latest, periods[latest] + 1, 1) if periods.get(latest, 12) < 12 else None
        annualize_contracts(conn, until=until)
        flag_deviations(conn, THRESHOLD, annualized=True)
        flag_coverage_outliers(conn)
        after = summary_line(conn)
        conn.close()

        report[:0] = [f"Før: {before}", f"Etter: {after}"]
        if a.dry_run:
            report.append("Prøvekjøring: site-data/ er ikke endret.")
        else:
            counts = export(db, a.data)
            report.append("Eksportert: " + ", ".join(f"{t} {n}" for t, n in counts.items()))

    text = "\n".join(f"- {line}" for line in report)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Månedlig oppdatering\n\n" + text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
