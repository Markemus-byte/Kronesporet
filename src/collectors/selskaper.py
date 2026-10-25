"""Offentlige selskaper: kjøpere i Doffin som eies av staten eller kommunene, men ikke står i regnskapene.

Statsforetak og statlig eide aksjeselskaper (Bane NOR SF, Avinor AS, Nye Veier AS, NRK AS),
Norges Bank og særlovsselskaper som Innovasjon Norge kunngjør i Doffin, men føres ikke i
statsregnskapet. Kronesporet finner dem blant kjøperne i Doffin ut fra institusjonell sektor i
Enhetsregisteret (SSBs inndeling), så listen ikke må vedlikeholdes for hånd:

  1120  statlig eide aksjeselskaper og statsforetak
  3100  Norges Bank
  3900  statlige låneinstitutter og særlovsselskaper (Innovasjon Norge)
  6100  statsforvaltningen (selskaper og stiftelser staten kontrollerer, som Nye Veier og NRK)

Kommunale og interkommunale selskaper (Ruter, BIR, IVAR, Kolumbus) finnes på samme måte:

  1510  kommunale foretak med ubegrenset ansvar (blant annet IKS)
  1520  kommunalt eide aksjeselskaper
  6500  kommuneforvaltningen (IKS, kommunale oppgavefellesskap og selskaper kommunene kontrollerer)

De regnes ikke til én kommune, fordi de ofte har flere eiere, og KOSTRA tar ikke med aksjeselskapene.

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
MUNICIPAL_SECTORS = {
    "1510": "Kommunalt foretak",
    "1520": "Kommunalt eid selskap",
    "6500": "Kommuneforvaltningen",
}
SKIP_FORMS = {"ORGL", "STAT", "KOMM", "FYLK", "OPMV"}  # etater og kommunene selv
STATE_SKIP_FORMS = SKIP_FORMS | {"KF", "FKF", "IKS"}  # kommunale former med feil sektorkode
PAUSE_S = 0.1

SCHEMA = """
CREATE TABLE IF NOT EXISTS selskaper (
    orgnr   TEXT PRIMARY KEY,
    name    TEXT,
    form    TEXT,               -- organisasjonsform: AS, SF, ASA, SÆR, STI, ANNA ...
    sector  TEXT                -- institusjonell sektor i Enhetsregisteret (STATE_SECTORS, MUNICIPAL_SECTORS)
);
"""


def is_state(sector: str) -> bool:
    return sector in STATE_SECTORS


def orgnrs(conn: sqlite3.Connection, state: bool | None = None) -> set[str]:
    """Selskapene; state=True bare de statlige, False bare de kommunale."""
    try:
        return {o for o, sector in conn.execute("SELECT orgnr, sector FROM selskaper")
                if state is None or is_state(sector) == state}
    except sqlite3.OperationalError:
        return set()


def find(conn: sqlite3.Connection, candidates: Iterable[str], session: requests.Session | None = None,
         failed: set[str] | None = None) -> list[str]:
    """Slå opp kjøperne i Enhetsregisteret og legg de statlige og kommunale selskapene i `selskaper`.
    Returnerer de nye. Orgnr som ikke kunne slås opp (nettverksfeil), legges i `failed`."""
    import requests

    session = session or requests.Session()
    conn.executescript(SCHEMA)
    found = []
    for orgnr in sorted(set(candidates) - orgnrs(conn)):
        try:
            resp = session.get(BRREG_API.format(orgnr), timeout=30)
        except requests.RequestException as e:
            log.warning("Enhetsregisteret svarte ikke for %s: %s", orgnr, e)
            if failed is not None:
                failed.add(orgnr)
            continue
        time.sleep(PAUSE_S)
        if resp.status_code != 200:
            continue
        data = resp.json()
        form = (data.get("organisasjonsform") or {}).get("kode") or ""
        sector = (data.get("institusjonellSektorkode") or {}).get("kode") or ""
        if data.get("slettedato") or not (
                (sector in STATE_SECTORS and form not in STATE_SKIP_FORMS)
                or (sector in MUNICIPAL_SECTORS and form not in SKIP_FORMS)):
            continue
        name = data.get("navn") or orgnr
        conn.execute("INSERT OR REPLACE INTO selskaper VALUES (?, ?, ?, ?)", (orgnr, _display(name), form, sector))
        found.append(orgnr)
    conn.commit()
    return found


_UPPER = {"AS", "ASA", "SF", "NRK", "STI", "KO", "IKT", "NBIM", "SIVA", "NOR", "NTNU", "LL", "IKS", "KF", "FKF",
          "BIR", "IVAR", "ROAF", "MOVAR", "VEAS", "NDLA", "KS", "AtB"}
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
