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

Nedlagte IKS og kommunale foretak har mistet sektorkoden i Enhetsregisteret og tas med ut fra
organisasjonsformen (MUNICIPAL_FORMS), når de ikke kan føres på en kommune.

Etater (organisasjonsledd) og enheter som allerede hører til en virksomhet, et helseforetak eller
en kommune, er ikke med. Årsregnskapene hentes fra Regnskapsregisteret som for helseforetakene
(src/collectors/foretak.py), og underenhetene fra Enhetsregisteret.
"""
from __future__ import annotations

import logging
import re
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
MUNICIPAL_FORMS = {"IKS", "KF", "FKF"}  # alltid kommunale; slettede enheter har ingen sektorkode
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
        if data.get("slettedato"):
            if form not in MUNICIPAL_FORMS:  # nedlagte IKS og foretak (Envina IKS, Askerbadet KF) er med
                continue
        elif not ((sector in STATE_SECTORS and form not in STATE_SKIP_FORMS)
                  or (sector in MUNICIPAL_SECTORS and form not in SKIP_FORMS)):
            continue
        name = re.sub(r"\s+under (sletting|avvikling).*$", "", data.get("navn") or orgnr, flags=re.I)
        conn.execute("INSERT OR REPLACE INTO selskaper VALUES (?, ?, ?, ?)", (orgnr, _display(name), form, sector))
        found.append(orgnr)
    fix_place_names(conn)
    conn.commit()
    return found


# Regioner og distrikter som ikke er kommunenavn, men står i navnene på interkommunale selskaper
REGIONS = {"follo", "romerike", "hordaland", "nordhordland", "sunnhordland", "fjordane", "sogn", "ryfylke", "dalane",
           "nordmøre", "sunnmøre", "romsdal", "drammensregionen", "ålesundregionen", "vestviken", "hadeland", "valdres",
           "gudbrandsdal", "midt-gudbrandsdal", "haugaland", "jæren", "lofoten", "vesterålen", "salten", "helgeland",
           "innherred", "namdal", "fosen", "glåmdal", "hallingdal", "grenland", "setesdal", "hardanger", "eiker"}
_NOT_PLACES = {"kommune", "fylkeskommune", "herad", "og", "i", "ja", "suohkan", "gielda", "komuuni", "tjïelte", "hole",
               "nes", "lund", "sund", "vik", "bø", "os", "hå", "ål", "sola"}


def _places(conn: sqlite3.Connection) -> set[str]:
    out = set(REGIONS)
    try:
        for (name,) in conn.execute("SELECT name FROM kommuner"):
            for w in re.split(r"[\s/]+", re.sub(r"\(.*?\)", "", name)):
                if len(w) > 2 and w[:1].isupper():
                    out.add(w.lower())
    except sqlite3.OperationalError:
        pass
    return out - _NOT_PLACES


def fix_place_names(conn: sqlite3.Connection) -> int:
    """Stor forbokstav på steder i navn som kom med store bokstaver fra Enhetsregisteret:
    'Asker og bærum vannverk IKS' -> 'Asker og Bærum vannverk IKS', 'nord-gudbrandsdal' -> 'Nord-Gudbrandsdal'.
    Returnerer antall navn som ble rettet."""
    places = _places(conn)
    fixed = 0
    for orgnr, name in conn.execute("SELECT orgnr, name FROM selskaper").fetchall():
        words = re.split(r"(\s+|/)", name)
        for i, w in enumerate(words):
            if not w.strip() or w == "/":
                continue
            if w.upper() in _UPPER:
                words[i] = _UPPER[w.upper()]
                continue
            parts = w.split("-")
            if any(p.islower() and p in places for p in parts):  # «nord-gudbrandsdal», «sør-trøndelag»
                words[i] = "-".join(p[:1].upper() + p[1:] if p in places or p in _DIRECTIONS else p for p in parts)
        for i in range(len(words) - 2):  # «søre sunnmøre»
            if words[i] in _DIRECTIONS and words[i + 2][:1].isupper() and words[i + 2].lower() in places:
                words[i] = words[i][:1].upper() + words[i][1:]
        new = "".join(words)
        if new != name:
            conn.execute("UPDATE selskaper SET name = ? WHERE orgnr = ?", (new, orgnr))
            fixed += 1
    return fixed


_DIRECTIONS = {"nord", "sør", "øst", "vest", "midt", "indre", "ytre", "søre", "nordre", "søndre", "øvre", "nedre",
               "midtre", "aust"}
_UPPER = {w.upper(): w for w in (
    "AS", "ASA", "SF", "NRK", "STI", "KO", "IKT", "NBIM", "SIVA", "NOR", "NTNU", "LL", "IKS", "KF", "FKF", "BIR", "IVAR",
    "ROAF", "MOVAR", "VEAS", "NDLA", "KS", "AtB", "AFK", "RTA", "LIBIR", "HÅR", "MIA")}
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
            out.append(_UPPER[w])
        elif i == 0:
            out.append(w[:1] + w[1:].lower())
        else:
            out.append(w.lower())
    return " ".join(out)
