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

Noen virksomheter er registrert med et overordnet ledd som har samme navn, og det er
det leddet som kunngjør i Doffin. Tolletaten står i regnskapet på 974761343, mens
kunngjøringene står på 880455702 «TOLLETATEN», som er det overordnede leddet og ligger
under Finansdepartementet. Et slikt navnelikt overordnet ledd, og treet under det,
føres på virksomheten og ikke på departementet.
"""
from __future__ import annotations

import logging
import re
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


def _norm_name(name: str) -> str:
    return re.sub(r"[^0-9a-zæøå]", "", (name or "").lower())


def namesake_parents(session: requests.Session, roots: Iterable[str]) -> dict[str, tuple[str, str]]:
    """virksomhet -> (orgnr, navn) for det overordnede leddet når det har samme navn som virksomheten."""
    import requests

    out: dict[str, tuple[str, str]] = {}
    for root in sorted(set(roots)):
        try:
            resp = session.get(f"{API}/enheter/{root}", timeout=30)
            time.sleep(PAUSE_S)
            if resp.status_code != 200 or not resp.json().get("overordnetEnhet"):
                continue
            up = resp.json()["overordnetEnhet"]
            parent = session.get(f"{API}/enheter/{up}", timeout=30)
            time.sleep(PAUSE_S)
        except requests.RequestException as e:
            log.warning("Enhetsregisteret svarte ikke for %s: %s", root, e)
            continue
        if parent.status_code == 200 and _norm_name(parent.json().get("navn", "")) == _norm_name(resp.json().get("navn", "")):
            out[root] = (up, parent.json().get("navn", ""))
    return out


def load_subunits(conn: sqlite3.Connection, orgnrs: Iterable[str], session: requests.Session | None = None,
                  replace: bool = True) -> int:
    """Fyll `orgnr_parent` for regnskapsvirksomhetene i orgnrs. Returnerer antall barn.

    replace=False legger til trærne for orgnrs og beholder dem som allerede er lagret
    (brukes for helseforetakene, se src/collectors/foretak.py).
    """
    import requests

    roots = set(orgnrs)
    session = session or requests.Session()
    conn.executescript(SCHEMA)
    known: set[str] = set()
    if replace:
        conn.execute("DELETE FROM orgnr_parent")
    else:
        known = {o for row in conn.execute("SELECT child, parent FROM orgnr_parent") for o in row}
    namesakes = {r: up for r, up in namesake_parents(session, roots).items() if up[0] not in roots | known}
    stop = roots | known | {up for up, _ in namesakes.values()}
    rows: list[tuple[str, str, str, str]] = []
    for root, (up, name) in sorted(namesakes.items()):
        log.info("%s føres på %s (overordnet ledd med samme navn)", up, root)
        rows.append((up, root, name, "enhet"))
    for root in sorted(roots):
        start = [root] + [namesakes[root][0]] if root in namesakes else [root]
        queue, seen = list(start), set(start)
        while queue:
            node = queue.pop()
            try:
                children = _children(session, node)
            except requests.RequestException as e:
                log.warning("Enhetsregisteret svarte ikke for %s: %s", node, e)
                continue
            for child, name, kind in children:
                if child in stop or child in seen:
                    continue
                seen.add(child)
                rows.append((child, root, name, kind))
                if kind == "enhet":
                    queue.append(child)
    conn.executemany(f"INSERT OR {'REPLACE' if replace else 'IGNORE'} INTO orgnr_parent VALUES (?, ?, ?, ?)", rows)
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
