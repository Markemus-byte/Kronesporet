"""Helseforetakene: statlige innkjøpere som ikke er med i statsregnskapet.

De regionale helseforetakene (RHF) og helseforetakene (HF) under dem eies av staten, men
føres ikke i statsregnskapet. De leverer årsregnskap etter regnskapsloven, og det ligger
åpent i Regnskapsregisteret (data.brreg.no, ingen nøkkel, de siste tre årene).

Listen over foretakene ligger i site-data/helseforetak.csv (orgnr, navn, eier). Doffin-
kontraktene deres hentes som for virksomhetene i statsregnskapet, og underenhetene
(sykehusene og avdelingene) kobles via `orgnr_parent` (src/collectors/brreg.py).

Regnskapet gir ikke et tall for kjøp av varer og tjenester. Nærmeste tall er driftskostnader
utenom lønn, som også tar med avskrivninger og gjestepasientoppgjør, så det er en øvre grense
for kjøpene og ikke direkte sammenlignbart med statsregnskapets artskontoer.
"""
from __future__ import annotations

import csv
import logging
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import requests

log = logging.getLogger(__name__)

ACCOUNTS_API = "https://data.brreg.no/regnskapsregisteret/regnskap/{}"
ACCOUNTS_URL = "https://virksomhet.brreg.no/nb/oppslag/enheter/{}"
PAUSE_S = 0.2

SCHEMA = """
CREATE TABLE IF NOT EXISTS foretak (
    orgnr   TEXT PRIMARY KEY,
    name    TEXT,
    owner   TEXT                -- eieren: departementet for RHF-ene, RHF-et for HF-ene
);
CREATE TABLE IF NOT EXISTS foretak_regnskap (
    orgnr               TEXT,
    year                INTEGER,
    operating_income    REAL,   -- sum driftsinntekter
    operating_costs     REAL,   -- sum driftskostnader
    payroll             REAL,   -- lønnskostnader
    PRIMARY KEY (orgnr, year)
);
"""


def load_list(conn: sqlite3.Connection, path: Path) -> list[str]:
    """Les foretakslisten (orgnr, navn, eier) inn i `foretak`. Returnerer orgnr."""
    conn.executescript(SCHEMA)
    with path.open(encoding="utf-8", newline="") as f:
        rows = [(r["orgnr"].strip(), r["navn"].strip(), r["eier"].strip()) for r in csv.DictReader(f)]
    conn.execute("DELETE FROM foretak")
    conn.executemany("INSERT INTO foretak VALUES (?, ?, ?)", rows)
    conn.commit()
    return [r[0] for r in rows]


def orgnrs(conn: sqlite3.Connection) -> set[str]:
    try:
        return {r[0] for r in conn.execute("SELECT orgnr FROM foretak")}
    except sqlite3.OperationalError:
        return set()


def accounts(data: list[dict]) -> list[tuple[int, float | None, float | None, float | None]]:
    """(år, driftsinntekter, driftskostnader, lønn) fra Regnskapsregisterets svar, ett per regnskapsår."""
    out = {}
    for r in data:
        if r.get("regnskapstype") != "SELSKAP" or r.get("avviklingsregnskap"):
            continue
        period = (r.get("regnskapsperiode") or {}).get("tilDato") or ""
        if not period[:4].isdigit():
            continue
        drift = ((r.get("resultatregnskapResultat") or {}).get("driftsresultat") or {})
        income = (drift.get("driftsinntekter") or {}).get("sumDriftsinntekter")
        costs = drift.get("driftskostnad") or {}
        out[int(period[:4])] = (int(period[:4]), income, costs.get("sumDriftskostnad"), costs.get("loennskostnad"))
    return [out[y] for y in sorted(out)]


UNIT_ERROR = 100  # et år med over 100 ganger så store tall som de andre årene er ført i kroner i stedet for 1000 kr


def fix_units(rows: list[tuple[int, float | None, float | None, float | None]]
              ) -> list[tuple[int, float | None, float | None, float | None]]:
    """Del på 1000 for år der tallene åpenbart er ført i feil enhet.

    Helse Nord-Trøndelag HF har 4 081 mrd. kr i driftskostnader for 2023 i Regnskapsregisteret,
    mot 4 mrd. de andre årene. Sammenligner med medianen av driftskostnadene de andre årene.
    """
    out = []
    for i, row in enumerate(rows):
        others = sorted(r[2] for j, r in enumerate(rows) if j != i and r[2])
        if row[2] and others and row[2] > UNIT_ERROR * others[len(others) // 2]:
            log.warning("Regnskapsregisteret: tallene for %s ser ut til å være i feil enhet, deles på 1000", row[0])
            row = (row[0], *(v / 1000 if v is not None else None for v in row[1:]))
        out.append(row)
    return out


def fetch_accounts(conn: sqlite3.Connection, orgs: Iterable[str], session: requests.Session | None = None) -> int:
    """Hent årsregnskapene fra Regnskapsregisteret. Eldre år som allerede er lagret, beholdes.
    Returnerer antall foretaksår lagret."""
    import requests

    session = session or requests.Session()
    conn.executescript(SCHEMA)
    n = 0
    for orgnr in sorted(set(orgs)):
        try:
            resp = session.get(ACCOUNTS_API.format(orgnr), timeout=30)
        except requests.RequestException as e:
            log.warning("Regnskapsregisteret svarte ikke for %s: %s", orgnr, e)
            continue
        time.sleep(PAUSE_S)
        if resp.status_code != 200:
            log.warning("Regnskapsregisteret: %s for %s", resp.status_code, orgnr)
            continue
        rows = fix_units(accounts(resp.json()))
        conn.executemany("INSERT OR REPLACE INTO foretak_regnskap VALUES (?, ?, ?, ?, ?)",
                         [(orgnr, *r) for r in rows])
        n += len(rows)
    conn.commit()
    return n
