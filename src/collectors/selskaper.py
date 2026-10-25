"""Statlige selskaper: kjøpere i Doffin som er statlige, men ikke står i statsregnskapet.

Statsforetak og statlig eide aksjeselskaper (Bane NOR SF, Avinor AS, Nye Veier AS, NRK AS),
Norges Bank og særlovsselskaper som Innovasjon Norge kunngjør i Doffin, men føres ikke i
statsregnskapet. Kronesporet finner dem blant kjøperne i Doffin ut fra institusjonell sektor i
Enhetsregisteret (SSBs inndeling), så listen ikke må vedlikeholdes for hånd:

  1120  statlig eide aksjeselskaper og statsforetak
  3100  Norges Bank
  3900  statlige låneinstitutter og særlovsselskaper (Innovasjon Norge)
  6100  statsforvaltningen (selskaper og stiftelser staten kontrollerer, som Nye Veier og NRK)

Etater (organisasjonsledd) og enheter som allerede hører til en virksomhet, et helseforetak eller
en kommune, er ikke med. Årsregnskapene hentes fra Regnskapsregisteret som for helseforetakene
(src/collectors/foretak.py), og underenhetene fra Enhetsregisteret.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import requests

log = logging.getLogger(__name__)

BRREG_API = "https://data.brreg.no/enhetsregisteret/api/enheter/{}"
STATE_SECTORS = {
    "1120": "Statlig eid selskap",
    "3100": "Norges Bank",
    "3900": "Statlig låneinstitutt",
    "6100": "Statsforvaltningen",
}
SKIP_FORMS = {"ORGL", "STAT", "KOMM", "FYLK", "KF", "FKF", "IKS", "OPMV"}  # etater og kommunal sektor
PAUSE_S = 0.1

SCHEMA = """
CREATE TABLE IF NOT EXISTS selskaper (
    orgnr   TEXT PRIMARY KEY,
    name    TEXT,
    form    TEXT,               -- organisasjonsform: AS, SF, ASA, SÆR, STI, ANNA ...
    sector  TEXT                -- institusjonell sektor i Enhetsregisteret (STATE_SECTORS)
);
"""


def orgnrs(conn: sqlite3.Connection) -> set[str]:
    try:
        return {r[0] for r in conn.execute("SELECT orgnr FROM selskaper")}
    except sqlite3.OperationalError:
        return set()


def find(conn: sqlite3.Connection, candidates: Iterable[str], session: requests.Session | None = None) -> list[str]:
    """Slå opp kjøperne i Enhetsregisteret og legg de statlige selskapene i `selskaper`. Returnerer de nye."""
    import requests

    session = session or requests.Session()
    conn.executescript(SCHEMA)
    found = []
    for orgnr in sorted(set(candidates) - orgnrs(conn)):
        try:
            resp = session.get(BRREG_API.format(orgnr), timeout=30)
        except requests.RequestException as e:
            log.warning("Enhetsregisteret svarte ikke for %s: %s", orgnr, e)
            continue
        time.sleep(PAUSE_S)
        if resp.status_code != 200:
            continue
        data = resp.json()
        form = (data.get("organisasjonsform") or {}).get("kode") or ""
        sector = (data.get("institusjonellSektorkode") or {}).get("kode") or ""
        if sector not in STATE_SECTORS or form in SKIP_FORMS or data.get("slettedato"):
            continue
        name = data.get("navn") or orgnr
        conn.execute("INSERT OR REPLACE INTO selskaper VALUES (?, ?, ?, ?)", (orgnr, _display(name), form, sector))
        found.append(orgnr)
    conn.commit()
    return found


_UPPER = {"AS", "ASA", "SF", "NRK", "STI", "KO", "IKT", "NBIM", "SIVA", "NOR", "NTNU", "LL"}
_NAMES = {  # egennavn der bare første ord har stor forbokstav ser feil ut
    "NORGES BANK": "Norges Bank", "NYE VEIER AS": "Nye Veier AS", "INNOVASJON NORGE": "Innovasjon Norge",
    "NORSK TIPPING AS": "Norsk Tipping AS", "NORSK HELSENETT SF": "Norsk Helsenett SF",
    "NORSK RIKSKRINGKASTING AS": "Norsk rikskringkasting AS (NRK)", "LL DET NORSKE TEATRET": "Det Norske Teatret",
    "AS DEN NATIONALE SCENE": "Den Nationale Scene AS",
}


def _display(name: str) -> str:
    """'BANE NOR SF' -> 'Bane NOR SF', 'AVINOR AS' -> 'Avinor AS'."""
    if name in _NAMES:
        return _NAMES[name]
    if name != name.upper():
        return name
    words = name.split()
    out = []
    for i, w in enumerate(words):
        if w in _UPPER:
            out.append(w)
        elif i == 0:
            out.append(w[:1] + w[1:].lower())
        else:
            out.append(w.lower())
    return " ".join(out)
