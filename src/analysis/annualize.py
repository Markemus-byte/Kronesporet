"""Fordel Doffin-kontraktverdier over årene kontraktene løper.

Doffin oppgir hele kontraktsverdien (ofte en rammeavtale over flere år) i
kunngjøringen om tildeling, mens statsregnskapet og årsrapportene viser
utgifter for ett år. For å sammenligne likt med likt fordeles hver kontrakt
lineært over kontraktsperioden, og et regnskapsår får summen av årsandelene
til alle kontrakter som løper det året, også kontrakter tildelt tidligere år.

Kontraktsperiode (i prioritert rekkefølge, lest av src/collectors/doffin.py):
  1. start- og sluttdato i kunngjøringen               -> duration_source 'datoer'
  2. «Varighet» (f.eks. 48 MONTH), start = tildelingen   -> 'varighet'
  3. varighet i fritekst («1+1 år», «inntil 4 år»)      -> 'tekst'
  4. standardvarighet når ingenting er oppgitt          -> 'standard'
     - rammeavtaler: 48 måneder (anskaffelsesforskriften § 26-2: maks 4 år)
     - andre kontrakter: DEFAULT_CONTRACT_MONTHS
Uten startdato regnes perioden fra publiseringsdatoen for tildelingen.

Resultatet ligger i tabellen `doffin_contract_years` (én rad per kontrakt og år).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

DEFAULT_FRAMEWORK_MONTHS = 48.0
DEFAULT_CONTRACT_MONTHS = 24.0
# Varigheter over dette er nesten alltid feil (dager tolket som måneder o.l.)
MAX_MONTHS = 240.0
DAYS_PER_MONTH = 365.25 / 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS doffin_contract_years (
    contract_id     TEXT,
    buyer_orgnr     TEXT,
    year            INTEGER,
    share           REAL,     -- andel av kontraktsperioden som faller i året (0-1)
    value_share     REAL,     -- value * share
    period_source   TEXT,     -- 'datoer' | 'varighet' | 'tekst' | 'standard'
    PRIMARY KEY (contract_id, year)
);
CREATE INDEX IF NOT EXISTS idx_contract_years_org ON doffin_contract_years (buyer_orgnr, year);
"""


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def contract_period(
    publication_date: str | None,
    start_date: str | None,
    end_date: str | None,
    duration_months: float | None,
    duration_source: str | None,
    is_framework: int | None,
    default_framework_months: float = DEFAULT_FRAMEWORK_MONTHS,
    default_contract_months: float = DEFAULT_CONTRACT_MONTHS,
) -> tuple[date, date, str] | None:
    """(start, slutt, kilde) for én kontrakt, eller None uten noen dato å gå ut fra."""
    start = _parse_date(start_date) or _parse_date(publication_date)
    if start is None:
        return None
    end = _parse_date(end_date)
    if end and end > start and duration_source == "datoer":
        return start, end, "datoer"
    months, source = duration_months, duration_source
    if not months or months <= 0 or months > MAX_MONTHS or source not in ("varighet", "tekst", "datoer"):
        months = default_framework_months if is_framework else default_contract_months
        source = "standard"
    return start, start + timedelta(days=round(months * DAYS_PER_MONTH)), source


def year_shares(start: date, end: date, until: date | None = None) -> dict[int, float]:
    """Andel av [start, end) som faller i hvert kalenderår (summerer til 1).

    until: tell bare dager før denne datoen (for hittil-i-år-regnskap).
    """
    total = (end - start).days
    if total <= 0:
        return {start.year: 1.0} if until is None or start < until else {}
    out: dict[int, float] = {}
    for y in range(start.year, end.year + 1):
        lo, hi = max(start, date(y, 1, 1)), min(end, date(y + 1, 1, 1))
        if until is not None:
            hi = min(hi, until)
        if hi > lo:
            out[y] = (hi - lo).days / total
    return out


def annualize_contracts(
    conn: sqlite3.Connection,
    default_framework_months: float = DEFAULT_FRAMEWORK_MONTHS,
    default_contract_months: float = DEFAULT_CONTRACT_MONTHS,
    currency: str = "NOK",
    until: date | None = None,
) -> dict[str, int]:
    """Bygg `doffin_contract_years` på nytt fra `doffin_contracts`. Returnerer antall per periodekilde.

    until: første dag som ikke skal telles, f.eks. date(2026, 9, 1) for regnskap januar-august 2026.
    """
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM doffin_contract_years")
    rows = conn.execute(
        """
        SELECT id, buyer_orgnr, value, publication_date, start_date, end_date,
               duration_months, duration_source, is_framework
        FROM doffin_contracts
        WHERE value IS NOT NULL AND buyer_orgnr IS NOT NULL
          AND (currency = ? OR currency IS NULL)
        """,
        (currency,),
    ).fetchall()
    counts: dict[str, int] = {}
    out = []
    for cid, org, value, pub, s, e, months, src, fw in rows:
        period = contract_period(pub, s, e, months, src, fw, default_framework_months, default_contract_months)
        if period is None:
            continue
        start, end, source = period
        counts[source] = counts.get(source, 0) + 1
        for year, share in year_shares(start, end, until).items():
            out.append((cid, org, year, share, value * share, source))
    conn.executemany("INSERT OR REPLACE INTO doffin_contract_years VALUES (?, ?, ?, ?, ?, ?)", out)
    conn.commit()
    return counts
