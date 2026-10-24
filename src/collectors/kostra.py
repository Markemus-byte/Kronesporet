"""Kommuner og fylkeskommuner: kjøp av varer og tjenester fra KOSTRA (SSB, ingen nøkkel).

Kommunene og fylkeskommunene står ikke i statsregnskapet, men regnskapene deres rapporteres til
SSB (KOSTRA) etter art. Kronesporet bruker konserntallene, som tar med kommunale foretak og
interkommunale selskaper, og regner som kjøp av varer og tjenester:

  AG17   kjøp av varer og tjenester som inngår i egen tjenesteproduksjon
  AGD51  kjøp av tjenester som erstatter egen tjenesteproduksjon (f.eks. private barnehager, IKS)
  AGI39  investeringer i varige driftsmidler (bygg, anlegg, utstyr)

Det tilsvarer omtrent «kjøp av varer og tjenester» i statsregnskapet (varer, tjenester og
investeringer, uten lønn, tilskudd og finans). Tabellene:
  13551 / 13552  drift / investering, kommunekonsern, etter art (K)
  13547 / 13548  drift / investering, fylkeskommunekonsern, etter art (F)

Kommunene kobles til organisasjonsnummer via Enhetsregisteret (organisasjonsform KOMM og FYLK).
Regionkodene er dagens kommunenummer, så bare år med dagens kommuneinndeling (fra 2024) tas med;
fylkene ble delt opp igjen fra 2024.
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

SSB_API = "https://data.ssb.no/api/v0/no/table/{}"
BRREG_API = "https://data.brreg.no/enhetsregisteret/api/enheter"
FIRST_YEAR = 2024  # dagens kommune- og fylkesinndeling
PURCHASE_ARTS = {"AG17": "drift", "AGD51": "erstatter", "AGI39": "investering"}
TABLES = {  # (nivå, tabell, regionvariabel, arter)
    "kommune": [("13551", "KOKkommuneregion0000", ("AG17", "AGD51")), ("13552", "KOKkommuneregion0000", ("AGI39",))],
    "fylke": [("13547", "KOKfylkesregion0000", ("AG17", "AGD51")), ("13548", "KOKfylkesregion0000", ("AGI39",))],
}
PAUSE_S = 1.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS kommuner (
    orgnr   TEXT PRIMARY KEY,
    name    TEXT,
    kind    TEXT,               -- 'kommune' | 'fylke'
    region  TEXT                -- KOSTRA-regionkoden (kommunenummer, eller fylkesnummer + '00')
);
CREATE TABLE IF NOT EXISTS kommune_regnskap (
    orgnr   TEXT,
    year    INTEGER,
    art     TEXT,               -- 'drift' (AG17) | 'erstatter' (AGD51) | 'investering' (AGI39)
    amount  REAL,               -- kroner
    PRIMARY KEY (orgnr, year, art)
);
"""


def _name_key(name: str) -> str:
    """'TRØNDELAG FYLKESKOMMUNE' og 'Trøndelag - Trööndelage fylkeskommune' -> 'trøndelag'."""
    name = re.sub(r"\(.*?\)", "", name.lower())
    return re.split(r"\s+-\s+|\s+fylkeskommune|\s+kommune", name)[0].strip()


_LOWER_WORDS = {"kommune", "fylkeskommune", "herad", "og", "i", "suohkan", "gielda", "komuuni", "tjïelte"}


def pretty_name(name: str) -> str:
    """'MØRE OG ROMSDAL FYLKESKOMMUNE' -> 'Møre og Romsdal fylkeskommune', 'VOSS HERAD' -> 'Voss herad'."""
    words = re.split(r"(\s+|-|/)", name.lower())
    return "".join(w if w in _LOWER_WORDS or not w.strip() else w[:1].upper() + w[1:] for w in words)


def fetch_units(session: requests.Session) -> list[tuple[str, str, str, str]]:
    """(orgnr, navn, kind, kommunenummer fra adressen) for alle kommuner og fylkeskommuner."""
    out = []
    for form, kind in (("KOMM", "kommune"), ("FYLK", "fylke")):
        page = 0
        while True:
            resp = session.get(BRREG_API, params={"organisasjonsform": form, "size": 200, "page": page}, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            for e in data.get("_embedded", {}).get("enheter", []):
                addr = e.get("forretningsadresse") or e.get("postadresse") or {}
                out.append((e["organisasjonsnummer"], e["navn"], kind, addr.get("kommunenummer") or ""))
            page += 1
            if page >= data.get("page", {}).get("totalPages", 0):
                break
    return out


def _regions(session: requests.Session, table: str, var: str) -> dict[str, str]:
    meta = session.get(SSB_API.format(table), timeout=60).json()
    v = next(v for v in meta["variables"] if v["code"] == var)
    return dict(zip(v["values"], v["valueTexts"]))


def match_regions(units: Iterable[tuple[str, str, str, str]], kommune_regions: dict[str, str],
                  fylke_regions: dict[str, str]) -> list[tuple[str, str, str, str]]:
    """(orgnr, navn, kind, regionkode). Kommunene kobles på kommunenummeret i adressen når navnet stemmer,
    ellers på navnet; fylkeskommunene på navnet (adressen kan ligge i en annen kommune, som Akershus i Oslo)."""
    by_name: dict[str, list[str]] = {}
    for code, text in kommune_regions.items():
        if code.isdigit() and len(code) == 4:
            by_name.setdefault(_name_key(text), []).append(code)
    fylker = {_name_key(t): c for c, t in fylke_regions.items() if c.isdigit() and "(20" not in t and c != "0300"}
    out = []
    for orgnr, name, kind, knr in units:
        key = _name_key(name)
        if kind == "fylke":
            code = fylker.get(key)
        elif knr in kommune_regions and any(
                part.split()[0] in name.lower()
                for part in re.split(r"\s+-\s+", re.sub(r"\(.*?\)", "", kommune_regions[knr].lower())) if part.strip()):
            code = knr  # samiske og nynorske navn: «Deatnu - Tana» og «Deanu gielda / Tana kommune», «Voss herad»
        else:
            codes = [c for c in by_name.get(key, []) if "-20" not in kommune_regions[c]]
            code = codes[0] if len(codes) == 1 else None
        if code:
            out.append((orgnr, pretty_name(name), kind, code))
        else:
            log.warning("KOSTRA: fant ikke regionkode for %s %s (%s)", orgnr, name, knr)
    return out


def _query(session: requests.Session, table: str, var: str, regions: list[str], arts: Iterable[str],
           years: list[int]) -> dict[tuple[str, str, int], float]:
    body = {"query": [
        {"code": var, "selection": {"filter": "item", "values": regions}},
        {"code": "KOKart0000", "selection": {"filter": "item", "values": list(arts)}},
        {"code": "ContentsCode", "selection": {"filter": "item", "values": ["KOSbelop0000"]}},
        {"code": "Tid", "selection": {"filter": "item", "values": [str(y) for y in years]}},
    ], "response": {"format": "json-stat2"}}
    resp = session.post(SSB_API.format(table), json=body, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    dims = data["id"]
    sizes = data["size"]
    index = [list(data["dimension"][d]["category"]["index"]) if isinstance(data["dimension"][d]["category"]["index"], dict)
             else data["dimension"][d]["category"]["index"] for d in dims]
    out = {}
    values = data["value"]
    for i, value in enumerate(values):
        if value is None:
            continue
        pos, rest = [], i
        for size in reversed(sizes):
            pos.append(rest % size)
            rest //= size
        pos.reverse()
        cell = {d: index[k][pos[k]] for k, d in enumerate(dims)}
        out[(cell[var], cell["KOKart0000"], int(cell["Tid"]))] = value * 1000.0  # tabellene er i 1000 kr
    return out


def load(conn: sqlite3.Connection, session: requests.Session | None = None,
         years: list[int] | None = None) -> tuple[int, int]:
    """Hent kommunene, fylkeskommunene og kjøpene deres. Returnerer (enheter, regnskapsrader)."""
    import requests

    session = session or requests.Session()
    conn.executescript(SCHEMA)
    kreg = _regions(session, "13551", "KOKkommuneregion0000")
    freg = _regions(session, "13547", "KOKfylkesregion0000")
    units = match_regions(fetch_units(session), kreg, freg)
    available = sorted(int(y) for y in _regions(session, "13551", "Tid"))
    years = years or [y for y in available if y >= FIRST_YEAR]
    conn.execute("DELETE FROM kommuner")
    conn.executemany("INSERT INTO kommuner VALUES (?, ?, ?, ?)", units)
    orgnr = {(kind, code): o for o, _, kind, code in units}
    rows = []
    for kind, tables in TABLES.items():
        regions = sorted(code for k, code in orgnr if k == kind)
        for table, var, arts in tables:
            for i in range(0, len(regions), 120):  # SSB begrenser antall celler per spørring
                for (code, art, year), amount in _query(session, table, var, regions[i:i + 120], arts, years).items():
                    rows.append((orgnr[(kind, code)], year, PURCHASE_ARTS[art], amount))
                time.sleep(PAUSE_S)
    conn.execute(f"DELETE FROM kommune_regnskap WHERE year IN ({', '.join('?' for _ in years)})", years)
    conn.executemany("INSERT OR REPLACE INTO kommune_regnskap VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    return len(units), len(rows)


def orgnrs(conn: sqlite3.Connection) -> set[str]:
    try:
        return {r[0] for r in conn.execute("SELECT orgnr FROM kommuner")}
    except sqlite3.OperationalError:
        return set()
