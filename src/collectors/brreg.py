"""Underenheter fra Enhetsregisteret (data.brreg.no, ingen nøkkel).

Statsregnskapet fører en virksomhet på ett orgnr, mens kunngjøringene i Doffin
ofte står på et organisasjonsledd eller en underenhet. Eksempler:
  - Politiet (915429785) i regnskapet, Politiets fellestjenester og politidistriktene i Doffin
  - Forsvaret (986105174) i regnskapet, Forsvarets logistikkorganisasjon (FLO) i Doffin

`load_subunits` går nedover i Enhetsregisteret fra hver regnskapsvirksomhet
(organisasjonsledd via overordnetEnhet, og underenheter) og lagrer
barn -> virksomhet i tabellen `orgnr_parent`. Et barn som selv er en
regnskapsvirksomhet (f.eks. HELFO under Helsedirektoratet) beholder sitt eget
orgnr, og treet under det hører til det.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # requests lastes først ved oppslag, så nettsidebyggingen (parent_map) klarer seg uten
    import requests

log = logging.getLogger(__name__)

API = "https://data.brreg.no/enhetsregisteret/api"
PAGE_SIZE = 100
PAUSE_S = 0.2

SCHEMA = """
CREATE TABLE IF NOT EXISTS orgnr_parent (
    child       TEXT PRIMARY KEY,   -- orgnr i Doffin (organisasjonsledd eller underenhet)
    parent      TEXT,               -- regnskapsvirksomheten i statsregnskapet
    child_name  TEXT,
    kind        TEXT                -- 'enhet' (organisasjonsledd) | 'underenhet'
);
"""


def _children(session: requests.Session, orgnr: str) -> list[tuple[str, str, str]]:
    """(orgnr, navn, kind) for alle enheter og underenheter rett under orgnr."""
    out = []
    for kind, path, key in (("enhet", "enheter", "enheter"), ("underenhet", "underenheter", "underenheter")):
        page = 0
        while True:
            resp = session.get(f"{API}/{path}", params={"overordnetEnhet": orgnr, "size": PAGE_SIZE, "page": page},
                               timeout=30)
            resp.raise_for_status()
            data = resp.json()
            out += [(e["organisasjonsnummer"], e.get("navn", ""), kind) for e in data.get("_embedded", {}).get(key, [])]
            time.sleep(PAUSE_S)
            page += 1
            if page >= data.get("page", {}).get("totalPages", 0):
                break
    return out


def load_subunits(conn: sqlite3.Connection, orgnrs: Iterable[str], session: requests.Session | None = None) -> int:
    """Fyll `orgnr_parent` for regnskapsvirksomhetene i orgnrs. Returnerer antall barn."""
    import requests

    roots = set(orgnrs)
    session = session or requests.Session()
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM orgnr_parent")
    rows: list[tuple[str, str, str, str]] = []
    for root in sorted(roots):
        queue, seen = [root], {root}
        while queue:
            node = queue.pop()
            try:
                children = _children(session, node)
            except requests.RequestException as e:
                log.warning("Enhetsregisteret svarte ikke for %s: %s", node, e)
                continue
            for child, name, kind in children:
                if child in roots or child in seen:
                    continue
                seen.add(child)
                rows.append((child, root, name, kind))
                if kind == "enhet":
                    queue.append(child)
    conn.executemany("INSERT OR REPLACE INTO orgnr_parent VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    return len(rows)


def main_units(orgnrs: Iterable[str], session: requests.Session | None = None) -> dict[str, str]:
    """underenhet -> hovedenhet for orgnr som er underenheter (en avdeling), ikke enheter.

    Rådata fra statsregnskapet fører et fåtall virksomheter på en underenhet, mens Doffin og
    årsrapportene bruker hovedenheten: Nasjonalbiblioteket står som 979530730 (avd. Oslo) i
    rådata og 976029100 i Doffin. Uten denne koblingen kommer virksomheten ikke med.
    """
    import requests

    session = session or requests.Session()
    out: dict[str, str] = {}
    for orgnr in sorted(set(orgnrs)):
        try:
            if session.get(f"{API}/enheter/{orgnr}", timeout=30).status_code == 200:
                continue
            resp = session.get(f"{API}/underenheter/{orgnr}", timeout=30)
        except requests.RequestException as e:
            log.warning("Enhetsregisteret svarte ikke for %s: %s", orgnr, e)
            continue
        time.sleep(PAUSE_S)
        if resp.status_code == 200 and resp.json().get("overordnetEnhet"):
            out[orgnr] = resp.json()["overordnetEnhet"]
    return out


def parent_map(conn: sqlite3.Connection) -> dict[str, str]:
    """barn -> regnskapsvirksomhet, tom hvis `load_subunits` ikke er kjørt."""
    try:
        return dict(conn.execute("SELECT child, parent FROM orgnr_parent"))
    except sqlite3.OperationalError:
        return {}
