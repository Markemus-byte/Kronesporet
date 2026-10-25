"""Månedlig oppdatering av site-data/kronesporet.sql (kjøres av .github/workflows/oppdatering.yml).

Tar utgangspunkt i dataene som allerede ligger i repoet og legger til det som er nytt:
  1. Nye kontraktstildelinger i Doffin for virksomhetene på nettsiden (og underenhetene deres),
     med kontraktsperiode og vinnere. Kunngjøringer som allerede er lagret, hentes ikke på nytt.
  2. Nyere rådata fra statsregnskapet: xlsx-filer i site-data/raadata/ (lastes opp der via GitHub),
     gitt med --raadata eller lastet ned med --fetch-raadata (scripts/fetch_raadata.py; nettstedet
     stenger for kall fra skyen, også fra GitHub Actions). En nyere måned erstatter den forrige for
     samme år, og filer som allerede er lastet inn, hoppes over.
  3. Helseforetakene i site-data/helseforetak.csv: underenheter fra Enhetsregisteret for nye
     foretak og årsregnskapene fra Regnskapsregisteret (src/collectors/foretak.py).
  4. Kommunene og fylkeskommunene: kjøp fra KOSTRA (SSB) og kjøperne i Doffin som hører til dem
     (etater, bydeler og kommunale foretak, slått opp oppover i Enhetsregisteret, src/collectors/kostra.py).
     Statlige og kommunale selskaper blant kjøperne (statsforetak, statlig eide aksjeselskaper, Norges Bank,
     IKS og kommunalt eide aksjeselskaper) legges i `selskaper` ut fra sektoren i Enhetsregisteret, med
     underenheter og årsregnskap (src/collectors/selskaper.py).
  5. Kjente feil i Doffin rettes etter site-data/rettinger.csv (f.eks. feil orgnr på kjøperen).
  6. Årsfordeling, avvik og Doffin-dekning regnes ut på nytt, og tallene eksporteres.

Oppdateringen holder seg til virksomhetene som allerede er på nettsiden. Nye virksomheter trenger
Doffin-historikk og underenheter fra Enhetsregisteret og legges til med scripts/run_phase1.py.

Eksempel (fra repo-roten):
  python scripts/update_site_data.py --fetch-raadata
  python scripts/update_site_data.py --raadata data/raadata_september_2026_bevilgningsregnskap.xlsx
  python scripts/update_site_data.py --max-new 5 --dry-run     # prøvekjøring, skriver ingenting
"""
from __future__ import annotations

import argparse
import csv
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
    DoffinClient, DoffinError, buyer_list, init_db, iter_search_hits, month_ranges, normalize,
    save_buyers, save_contracts, save_winners, winners,
)
from src.collectors import foretak, kostra, selskaper  # noqa: E402
from src.collectors.brreg import load_subunits, main_units, resolve_upward  # noqa: E402
from src.collectors.statsregnskap import load_raadata_xlsx, raadata_orgnrs  # noqa: E402
from src.web.data import source_month  # noqa: E402

log = logging.getLogger("oppdatering")
CORRECTIONS = ROOT / "site-data" / "rettinger.csv"
HEALTH = ROOT / "site-data" / "helseforetak.csv"
CORRECTABLE = {"buyer_orgnr", "buyer_name", "value", "start_date", "end_date", "duration_months"}


def agencies(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT DISTINCT orgnr FROM statsregnskap WHERE orgnr IS NOT NULL")}


def buyers(conn: sqlite3.Connection) -> set[str]:
    """Orgnr som kan stå som kjøper i Doffin: virksomhetene, helseforetakene, de statlige selskapene,
    kommunene og underenhetene deres."""
    return (agencies(conn) | foretak.orgnrs(conn) | selskaper.orgnrs(conn) | kostra.orgnrs(conn)
            | {r[0] for r in conn.execute("SELECT child FROM orgnr_parent")})


CHECKED_SCHEMA = "CREATE TABLE IF NOT EXISTS orgnr_checked (orgnr TEXT PRIMARY KEY)  -- kjøpere slått opp uten treff"


def load_companies(conn: sqlite3.Connection) -> list[str]:
    """Årsregnskapene til de statlige og kommunale selskapene fra Regnskapsregisteret."""
    orgs = sorted(selskaper.orgnrs(conn))
    if not orgs:
        return []
    n = foretak.fetch_accounts(conn, orgs)
    n_state = len(selskaper.orgnrs(conn, state=True))
    return [f"Selskaper: {n_state} statlige og {len(orgs) - n_state} kommunale, "
            f"{n} årsregnskap fra Regnskapsregisteret."]


def resolve_buyers(conn: sqlite3.Connection, orgnrs: set[str]) -> tuple[int, int]:
    """Slå opp ukjente kjøpere i Enhetsregisteret.

    Kjøpere under en kommune eller et fylke føres på kommunen (orgnr_parent). Tidligere kommuner og
    fylkeskommuner og kommunale foretak med kommunen i navnet føres på kommunen de ble en del av
    (kostra.successors). Resten som er statlige eller kommunale selskaper etter sektoren i Enhetsregisteret,
    legges i `selskaper` med underenhetene. Kjøpere som er slått opp uten treff, huskes i orgnr_checked.
    Returnerer (nye enheter under kommuner, nye selskaper)."""
    roots = kostra.orgnrs(conn)
    conn.execute(CHECKED_SCHEMA)
    checked = {r[0] for r in conn.execute("SELECT orgnr FROM orgnr_checked")}
    todo = {o for o in orgnrs if o} - buyers(conn) - checked
    if not todo:
        return 0, 0
    found = resolve_upward(todo, roots) if roots else {}
    if roots:
        found |= kostra.successors(conn, todo - set(found))
    conn.executemany("INSERT OR IGNORE INTO orgnr_parent VALUES (?, ?, ?, ?)",
                     [(child, root, name, kind) for child, (root, name, kind) in found.items()])
    failed: set[str] = set()  # prøves igjen neste gang
    companies = selskaper.find(conn, todo - set(found), failed=failed)
    if companies:
        load_subunits(conn, companies, replace=False)
    conn.executemany("INSERT OR IGNORE INTO orgnr_checked VALUES (?)",
                     [(o,) for o in todo - set(found) - set(companies) - failed])
    conn.commit()
    return len(found), len(companies)


def load_health(conn: sqlite3.Connection, path: Path = HEALTH) -> list[str]:
    """Last helseforetakslisten, underenhetene til foretak som er nye i listen, og årsregnskapene."""
    if not path.exists():
        return []
    orgs = foretak.load_list(conn, path)
    have = {r[0] for r in conn.execute("SELECT DISTINCT parent FROM orgnr_parent")}
    new = sorted(set(orgs) - have)
    out = []
    if new:
        n = load_subunits(conn, new, replace=False)
        out.append(f"Helseforetak: {n} underenheter for {len(new)} foretak uten underenheter fra før.")
    n = foretak.fetch_accounts(conn, orgs)
    out.append(f"Helseforetak: {len(orgs)} foretak, {n} årsregnskap fra Regnskapsregisteret.")
    return out


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
    """Hent tildelinger publisert i `years` som ikke ligger i databasen, med detaljer og vinnere.

    En kunngjøring tas med når en av virksomhetene (eller underenhetene) er blant kjøperne, også
    når den ikke står først (felles anskaffelser). Alle kjøperne lagres i doffin_buyers, også for
    kunngjøringer som allerede ligger i databasen.
    """
    known = {r[0] for r in conn.execute("SELECT id FROM doffin_contracts")}
    today = date.today()
    hits: list[dict] = []
    for year in years:
        to_month = today.month if year == today.year else 12
        for start, end in month_ranges(year, to_month):
            hits += iter_search_hits(client, start, end)
        log.info("Doffin %s: %d tildelinger så langt", year, len(hits))
    found = {o for hit in hits for o, _ in buyer_list(hit)}
    n_units, n_companies = resolve_buyers(conn, found)
    if n_units or n_companies:
        log.info("%d nye kjøpere under kommuner og fylker, %d nye selskaper", n_units, n_companies)
    wanted = buyers(conn)
    new: dict[str, dict] = {}
    for hit in hits:
        found = buyer_list(hit)
        if not any(o in wanted for o, _ in found):
            continue
        if hit["id"] in known:
            save_buyers(conn, hit["id"], found)
        else:
            new.setdefault(hit["id"], hit)
    conn.commit()
    log.info("Doffin: %d nye kunngjøringer", len(new))
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
        save_buyers(conn, notice_id, buyer_list(new[notice_id]) or buyer_list(detail))
        if i % 50 == 0:
            conn.commit()
            log.info("Detaljer: %d/%d", i, len(ids))
    conn.commit()
    return saved


def apply_corrections(conn: sqlite3.Connection, path: Path = CORRECTIONS) -> int:
    """Rett kjente feil i Doffin-kunngjøringene (kontrakt, felt, verdi, begrunnelse). Returnerer antall rettet.

    Rettingene ligger i repoet, så de gjelder også når en kunngjøring hentes på nytt.
    """
    if not path.exists():
        return 0
    n = 0
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            field_, value = row["felt"].strip(), row["verdi"].strip()
            if field_ not in CORRECTABLE:
                raise SystemExit(f"{path.name}: feltet {field_!r} kan ikke rettes")
            n += conn.execute(f"UPDATE doffin_contracts SET {field_} = ? WHERE id = ?",
                              (value or None, row["kontrakt"].strip())).rowcount
            if field_ == "buyer_orgnr":
                conn.execute("UPDATE doffin_buyers SET orgnr = ? WHERE contract_id = ? AND position = 0",
                             (value, row["kontrakt"].strip()))
    conn.commit()
    return n


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
        year, month, n = load_raadata_xlsx(conn, path, main_units(raadata_orgnrs(path)))
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
    ap.add_argument("--raadata", type=Path, nargs="*", default=[],
                    help="Rådatafiler (xlsx) fra statsregnskapet, i tillegg til dem i site-data/raadata/")
    ap.add_argument("--fetch-raadata", action="store_true",
                    help="Prøv å laste ned nyeste rådata fra statsregnskapet.dfo.no (scripts/fetch_raadata.py)")
    ap.add_argument("--years", type=int, nargs="*",
                    help="Publiseringsår som sjekkes i Doffin (standard: i fjor og i år). Eldre år fyller også inn "
                         "kjøperne (doffin_buyers) for kunngjøringer som allerede er lagret")
    ap.add_argument("--max-new", type=int, help="Hent høyst så mange nye kunngjøringer (prøvekjøring)")
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "doffin_cache")
    ap.add_argument("--dry-run", action="store_true", help="Regn ut alt, men ikke skriv site-data/")
    ap.add_argument("--recheck-buyers", action="store_true",
                    help="Slå opp igjen kjøperne i orgnr_checked (etter nye regler for hvem som hører til kommunene); "
                         "bruk sammen med --years for alle årene")
    a = ap.parse_args()
    a.cache_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    today = date.today()
    years = a.years or [today.year - 1, today.year]
    report: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        db = sql_to_db(a.data, tmp)
        conn = init_db(db)
        before = summary_line(conn)
        roots_before = agencies(conn)

        files = sorted((ROOT / "site-data" / "raadata").glob("*.xlsx")) + list(a.raadata)
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

        report += load_health(conn)
        try:
            n_units, n_rows = kostra.load(conn)
            report.append(f"Kommuner og fylker: {n_units} med {n_rows} regnskapstall fra KOSTRA (SSB).")
        except Exception as e:  # SSB nede: behold tallene fra forrige kjøring
            log.warning("Kunne ikke hente KOSTRA fra SSB: %s", e)
            report.append(f"KOSTRA: kunne ikke hentes ({e}), tallene fra forrige kjøring er beholdt.")
        if a.recheck_buyers:
            conn.execute(CHECKED_SCHEMA)
            conn.execute("DELETE FROM orgnr_checked")
        n_new = fetch_new_contracts(conn, years, DoffinClient(cache_dir=a.cache_dir), a.max_new)
        report.append(f"Doffin: {n_new} nye kontrakter ({', '.join(map(str, years))}).")
        report += load_companies(conn)

        n_fixed = apply_corrections(conn)
        if n_fixed:
            report.append(f"Rettet {n_fixed} kjente feil i Doffin ({CORRECTIONS.name}).")

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
