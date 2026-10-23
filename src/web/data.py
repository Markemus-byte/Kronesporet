"""Leser analyseresultatet fra SQLite til enkle objekter som nettsiden bygges fra.

Kilden er databasen som `scripts/run_phase1.py` skriver: tabellene
`contract_spend_comparison`, `statsregnskap`, `doffin_contracts` og (når
kontraktverdien er årsfordelt) `doffin_contract_years`.

Finnes tabellen `doffin_coverage` (src/analysis/coverage.py), tas
Doffin-dekningsgraden og dekningsflagget med, og finnes `doffin_winners`
(scripts/fetch_winners.py), får kontraktene leverandører; ellers bygges
siden uten dem.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class Supplier:
    name: str
    orgnr: str | None


@dataclass
class Contract:
    id: str
    title: str
    url: str | None
    value: float | None          # hele kontraktsverdien slik den står i Doffin
    year_value: float | None     # andelen som faller i regnskapsåret (årsfordelt)
    share: float | None          # 0-1, andel av perioden i regnskapsåret
    published: str | None        # publiseringsdato for tildelingen
    start_date: str | None
    end_date: str | None
    duration_months: float | None
    period_source: str | None    # 'datoer' | 'varighet' | 'tekst' | 'standard'
    first_year: int | None       # første og siste år kontrakten løper (årsfordelt)
    last_year: int | None
    is_framework: bool
    value_type: str | None       # 'awarded' | 'estimated'
    suppliers: list[Supplier] = field(default_factory=list)  # vinnerne, når doffin_winners finnes


@dataclass
class Coverage:
    ratio: float                 # Doffin-årsandel / kjøp
    peer_group: str              # størrelsesgruppe etter kjøp
    peer_count: int
    peer_median: float
    z: float                     # robust z-verdi på log10(dekning)
    flagged: bool


@dataclass
class Agency:
    orgnr: str
    name: str
    year: int
    contract_sum: float
    contract_count: int
    spend_sum: float
    deviation: float             # (contract_sum - spend_sum) / spend_sum
    flagged: bool
    match_method: str | None
    spend_parts: dict[str, float] = field(default_factory=dict)  # artskonto -> beløp
    report_url: str | None = None
    spend_source: str | None = None    # filnavn når kjøpene kommer fra statsregnskapet, ikke Kudos
    awarded_in_year_sum: float = 0.0   # hele verdien av kontrakter tildelt i året
    awarded_in_year_count: int = 0
    coverage: Coverage | None = None   # fylles når doffin_coverage finnes
    contracts: list[Contract] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.orgnr or re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")

    @property
    def direction(self) -> str:
        return "over" if self.deviation > 0 else "under"


@dataclass
class Dataset:
    year: int
    annualized: bool
    threshold: float
    agencies: list[Agency]
    has_coverage: bool = False
    has_suppliers: bool = False
    data_date: str = ""          # siste henting fra Doffin (DD.MM.ÅÅÅÅ), fra doffin_contracts.fetched_at


def display_name(name: str) -> str:
    """'STATENS JERNBANETILSYN' -> 'Statens jernbanetilsyn', men behold forkortelser i parentes."""
    if not name.isupper():
        return name
    text = name.lower()
    text = text[:1].upper() + text[1:]
    return re.sub(r"\(([^)]*)\)", lambda m: f"({m.group(1).upper()})", text)


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return row is not None


def _report_url(source_file: str | None) -> str | None:
    if source_file and source_file.startswith("kudos:"):
        return source_file.removeprefix("kudos:")
    if source_file and source_file.startswith("http"):
        return source_file
    return None


def available_years(db_path: str | Path) -> list[int]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT year FROM contract_spend_comparison WHERE year IS NOT NULL ORDER BY year")]
    finally:
        conn.close()


def load_dataset(db_path: str | Path, year: int | None = None, threshold: float = 0.05) -> Dataset:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if year is None:
            year = conn.execute("SELECT MAX(year) FROM contract_spend_comparison").fetchone()[0]
            if year is None:
                raise SystemExit(f"{db_path}: contract_spend_comparison er tom. Kjør scripts/run_phase1.py først.")

        annualized = _has_table(conn, "doffin_contract_years") and conn.execute(
            "SELECT 1 FROM doffin_contract_years WHERE year = ? LIMIT 1", (year,)).fetchone() is not None

        agencies: list[Agency] = []
        for r in conn.execute(
            f"""SELECT orgnr, virksomhet, year, contract_sum, contract_count, spend_sum,
                       deviation, flagged, match_method
                FROM contract_spend_comparison WHERE year = ?""", (year,)):
            a = Agency(
                orgnr=r["orgnr"] or "", name=display_name(r["virksomhet"] or r["orgnr"]), year=r["year"],
                contract_sum=r["contract_sum"] or 0.0, contract_count=r["contract_count"] or 0,
                spend_sum=r["spend_sum"] or 0.0, deviation=r["deviation"] or 0.0,
                flagged=bool(r["flagged"]), match_method=r["match_method"],
            )
            agencies.append(a)

        has_coverage = _has_table(conn, "doffin_coverage")
        for a in agencies:
            if has_coverage:
                c = conn.execute(
                    "SELECT coverage, peer_group, peer_count, peer_median, z, flagged "
                    "FROM doffin_coverage WHERE orgnr = ? AND year = ?", (a.orgnr, year)).fetchone()
                if c is not None and c["coverage"] is not None:
                    a.coverage = Coverage(ratio=c["coverage"], peer_group=c["peer_group"] or "",
                                          peer_count=c["peer_count"] or 0, peer_median=c["peer_median"] or 0.0,
                                          z=c["z"] or 0.0, flagged=bool(c["flagged"]))
            for s in conn.execute(
                "SELECT artskonto, SUM(amount) AS amount, MAX(source_file) AS source_file "
                "FROM statsregnskap WHERE orgnr = ? AND year = ? GROUP BY artskonto", (a.orgnr, year)):
                a.spend_parts[s["artskonto"] or "sum"] = s["amount"] or 0.0
                a.report_url = a.report_url or _report_url(s["source_file"])
                if s["source_file"] and not _report_url(s["source_file"]):
                    a.spend_source = a.spend_source or s["source_file"]

            awarded = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(value), 0) FROM doffin_contracts WHERE buyer_orgnr = ? AND year = ?",
                (a.orgnr, year)).fetchone()
            a.awarded_in_year_count, a.awarded_in_year_sum = awarded[0], awarded[1]
            a.contracts = _contracts(conn, a.orgnr, year, annualized)

        agencies.sort(key=lambda a: a.name.lower())
        has_suppliers = False
        if _has_table(conn, "doffin_winners"):
            by_contract: dict[str, list[Supplier]] = {}
            for w in conn.execute("SELECT contract_id, name, orgnr FROM doffin_winners ORDER BY contract_id, position"):
                by_contract.setdefault(w["contract_id"], []).append(Supplier(name=w["name"], orgnr=w["orgnr"]))
            for a in agencies:
                for c in a.contracts:
                    c.suppliers = by_contract.get(c.id, [])
                    has_suppliers = has_suppliers or bool(c.suppliers)
        fetched = conn.execute("SELECT MAX(fetched_at) FROM doffin_contracts").fetchone()[0]
        data_date = f"{fetched[8:10]}.{fetched[5:7]}.{fetched[:4]}" if fetched and len(fetched) >= 10 else ""
        return Dataset(year=year, annualized=annualized, threshold=threshold, agencies=agencies,
                       has_coverage=any(a.coverage for a in agencies), has_suppliers=has_suppliers,
                       data_date=data_date)
    finally:
        conn.close()


def _contracts(conn: sqlite3.Connection, orgnr: str, year: int, annualized: bool) -> list[Contract]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(doffin_contracts)")}

    def col(name: str) -> str:
        return f"c.{name}" if name in cols else f"NULL AS {name}"

    base = (f"SELECT c.id, c.title, c.url, c.value, c.value_type, c.publication_date, "
            f"{col('start_date')}, {col('end_date')}, {col('duration_months')}, {col('is_framework')}")
    if annualized:
        rows = conn.execute(
            f"""{base}, y.value_share, y.share, y.period_source,
                       (SELECT MIN(year) FROM doffin_contract_years WHERE contract_id = c.id) AS first_year,
                       (SELECT MAX(year) FROM doffin_contract_years WHERE contract_id = c.id) AS last_year
                FROM doffin_contract_years y JOIN doffin_contracts c ON c.id = y.contract_id
                WHERE y.buyer_orgnr = ? AND y.year = ?
                ORDER BY y.value_share DESC NULLS LAST, c.value DESC NULLS LAST, c.id""", (orgnr, year))
    else:
        rows = conn.execute(
            f"""{base}, c.value AS value_share, 1.0 AS share, NULL AS period_source,
                       NULL AS first_year, NULL AS last_year
                FROM doffin_contracts c WHERE c.buyer_orgnr = ? AND c.year = ?
                ORDER BY c.value DESC NULLS LAST, c.id""", (orgnr, year))
    return [
        Contract(
            id=r["id"], title=r["title"] or "(uten tittel)", url=r["url"], value=r["value"],
            year_value=r["value_share"], share=r["share"], published=r["publication_date"],
            start_date=r["start_date"], end_date=r["end_date"], duration_months=r["duration_months"],
            period_source=r["period_source"], first_year=r["first_year"], last_year=r["last_year"],
            is_framework=bool(r["is_framework"]), value_type=r["value_type"],
        )
        for r in rows
    ]
