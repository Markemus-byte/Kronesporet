"""Les DFØs månedsrapport «Utgifter til staten» (xlsx) inn i tabellen `statsregnskap`.

Filen lastes ned manuelt fra https://statsregnskapet.dfo.no/last-ned, f.eks.
utgifter_til_staten_august_2026.xlsx. Den viser hittil-i-år-regnskap per
kapittel og post (beløp i 1000 kr), uten organisasjonsnummer:

    Kap.  Post  (navn)                         ...  Regnskap 1000 kr
    1320        Statens vegvesen:
          1     Driftsutgifter                 ...  4 512 345
          30    Riksveginvesteringer           ...  ...
                Sum kap 1320

Kapittelnavnet er som regel virksomheten. Vi slår opp orgnr slik:
  1) samme normaliserte navn som en kjøper i doffin_contracts (som har orgnr)
  2) eksakt navnetreff i Enhetsregisteret (data.brreg.no, ingen nøkkel)
Treffene lagres i tabellen `kapittel_orgnr`, som kan rettes for hånd
(kilde = 'manuell' overskrives aldri).

xlsx leses med standardbiblioteket (zip + XML), så vi trenger bare requests.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from src.analysis.join_and_flag import SCHEMA as ANALYSIS_SCHEMA
from src.analysis.join_and_flag import normalize_name

log = logging.getLogger(__name__)

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
BRREG_URL = "https://data.brreg.no/enhetsregisteret/api/enheter"
MONTHS = ["januar", "februar", "mars", "april", "mai", "juni", "juli",
          "august", "september", "oktober", "november", "desember"]
# Post 01-49 = drifts- og investeringsutgifter (der innkjøp føres).
# Post 50-99 er overføringer (tilskudd, lån, fond) og har ikke Doffin-motstykke.
DEFAULT_POSTS = range(1, 50)

KAPITTEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS kapittel_orgnr (
    kapittel  INTEGER PRIMARY KEY,
    navn      TEXT,
    orgnr     TEXT,     -- NULL = ingen virksomhet funnet (f.eks. «Flytransport»)
    kilde     TEXT      -- 'doffin' | 'brreg' | 'manuell' | 'ingen'
);
"""


@dataclass
class PostRow:
    kapittel: int
    kapittel_navn: str
    post: int
    post_navn: str
    regnskap_nok: float


# ---------------------------------------------------------------------------
# Minimal xlsx-leser
# ---------------------------------------------------------------------------

def _col_letters(ref: str) -> str:
    return re.match(r"[A-Z]+", ref).group(0)


def read_xlsx(path: str | Path, sheet_index: int = 0) -> list[dict[str, str]]:
    """Returner radene i ett ark som {kolonnebokstav: tekstverdi}. Tomme celler utelates."""
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        sheet = wb.findall("m:sheets/m:sheet", NS)[sheet_index]
        target = next(r.get("Target") for r in rels if r.get("Id") == sheet.get(REL_NS))
        sheet_xml = z.read("xl/" + target.lstrip("/").removeprefix("xl/"))

    rows: list[dict[str, str]] = []
    for row in ET.fromstring(sheet_xml).iterfind(".//m:sheetData/m:row", NS):
        out: dict[str, str] = {}
        for c in row.findall("m:c", NS):
            v = c.find("m:v", NS)
            if c.get("t") == "s" and v is not None:
                val = shared[int(v.text)]
            elif c.get("t") == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
            else:
                val = v.text if v is not None else ""
            if val and val.strip():
                out[_col_letters(c.get("r"))] = val.strip()
        rows.append(out)
    return rows


# ---------------------------------------------------------------------------
# Tolkning av rapporten
# ---------------------------------------------------------------------------

def parse_utgifter_report(path: str | Path) -> tuple[int, int, list[PostRow]]:
    """Returner (år, til-og-med-måned, poster) fra «Utgifter til staten»-arket."""
    rows = read_xlsx(path)

    year = month = None
    header_idx = None
    for i, r in enumerate(rows[:20]):
        text = " ".join(r.values()).lower()
        m = re.search(r"utgifter\s+(\w+)\s+(\d{4})", text)
        if m and m.group(1) in MONTHS:
            month, year = MONTHS.index(m.group(1)) + 1, int(m.group(2))
        if any(v.lower().startswith("kap") for v in r.values()):
            header_idx = i
            break
    if header_idx is None or year is None:
        raise ValueError(f"{Path(path).name} ser ikke ut som DFØs «Utgifter til staten»-rapport")

    header = rows[header_idx]
    col = {
        "kap": next(k for k, v in header.items() if v.lower().startswith("kap")),
        "post": next(k for k, v in header.items() if v.lower().startswith("post")),
        "regnskap": next(k for k, v in header.items() if v.lower().startswith("regnskap")),
    }
    # Navnekolonnen har ingen overskrift; den ligger rett etter Post.
    name_col = chr(ord(col["post"]) + 1)

    posts: list[PostRow] = []
    kap: int | None = None
    kap_name = ""
    for r in rows[header_idx + 1:]:
        kap_cell, post_cell = r.get(col["kap"], ""), r.get(col["post"], "")
        name = r.get(name_col, "")
        if kap_cell.isdigit():
            kap, kap_name = int(kap_cell), name.rstrip(":").strip()
        elif post_cell.isdigit() and kap is not None and col["regnskap"] in r:
            posts.append(PostRow(kap, kap_name, int(post_cell), name,
                                 float(r[col["regnskap"]]) * 1000))
    return year, month, posts


# ---------------------------------------------------------------------------
# Kapittel -> orgnr
# ---------------------------------------------------------------------------

def _brreg_lookup(session: requests.Session, name: str) -> str | None:
    """Eksakt (normalisert) navnetreff i Enhetsregisteret, helst statlige enheter."""
    try:
        resp = session.get(BRREG_URL, params={"navn": name, "size": 20}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Enhetsregisteret feilet for %r: %s", name, e)
        return None
    hits = [e for e in resp.json().get("_embedded", {}).get("enheter", [])
            if normalize_name(e.get("navn")) == normalize_name(name) and not e.get("slettedato")]
    hits.sort(key=lambda e: e.get("organisasjonsform", {}).get("kode") not in ("ORGL", "STAT", "SF"))
    return hits[0]["organisasjonsnummer"] if hits else None


def resolve_orgnr(
    conn: sqlite3.Connection,
    kapitler: dict[int, str],
    use_brreg: bool = True,
    pause: float = 0.2,
) -> dict[int, str | None]:
    """Finn orgnr per kapittel og lagre i `kapittel_orgnr`. Manuelle rader beholdes."""
    conn.executescript(KAPITTEL_SCHEMA)
    known = {k: (o, s) for k, o, s in conn.execute("SELECT kapittel, orgnr, kilde FROM kapittel_orgnr")}
    doffin = {}
    for org, name in conn.execute(
        "SELECT buyer_orgnr, buyer_name FROM doffin_contracts WHERE buyer_orgnr IS NOT NULL"
    ):
        doffin.setdefault(normalize_name(name), org)

    session = requests.Session()
    session.headers["User-Agent"] = "Kronesporet/0.1 (+https://kronesporet.no)"
    result: dict[int, str | None] = {}
    for kap, name in sorted(kapitler.items()):
        if kap in known and (known[kap][0] or known[kap][1] == "manuell"):
            result[kap] = known[kap][0]
            continue
        orgnr, source = doffin.get(normalize_name(name)), "doffin"
        if orgnr is None and use_brreg:
            # «Politiets sikkerhetstjeneste (PST)» -> også prøv uten parentes
            for candidate in dict.fromkeys([name, re.sub(r"\s*\(.*?\)", "", name).strip()]):
                orgnr, source = _brreg_lookup(session, candidate), "brreg"
                time.sleep(pause)
                if orgnr:
                    break
        source = source if orgnr else "ingen"
        conn.execute("INSERT OR REPLACE INTO kapittel_orgnr VALUES (?, ?, ?, ?)", (kap, name, orgnr, source))
        result[kap] = orgnr
    conn.commit()
    return result


# ---------------------------------------------------------------------------
# Innlasting
# ---------------------------------------------------------------------------

def load_utgifter_xlsx(
    conn: sqlite3.Connection,
    path: str | Path,
    posts: range | tuple[int, ...] = DEFAULT_POSTS,
    use_brreg: bool = True,
) -> tuple[int, int, int]:
    """Last rapporten inn i `statsregnskap`. Returnerer (år, måned, antall rader).

    artskonto-feltet får «kap.post», f.eks. «1320.30».
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Fant ikke {path}. Last ned fra https://statsregnskapet.dfo.no/last-ned først.")
    year, month, rows = parse_utgifter_report(path)
    rows = [r for r in rows if r.post in posts]
    orgnr = resolve_orgnr(conn, {r.kapittel: r.kapittel_navn for r in rows}, use_brreg)

    conn.executescript(ANALYSIS_SCHEMA)
    conn.execute("DELETE FROM statsregnskap WHERE source_file = ?", (path.name,))
    conn.executemany(
        "INSERT INTO statsregnskap (year, orgnr, virksomhet, artskonto, amount, source_file) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(year, orgnr.get(r.kapittel), r.kapittel_navn, f"{r.kapittel:04d}.{r.post:02d}",
          r.regnskap_nok, path.name) for r in rows],
    )
    conn.commit()
    return year, month, len(rows)


# ---------------------------------------------------------------------------
# Rådata (bevilgningsregnskap per virksomhet og artskonto)
# ---------------------------------------------------------------------------
#
# «Rådata <år> <måned> (bevilgningsregnskap)» fra statsregnskapet.dfo.no har én rad
# per kapittel, post, artskonto og virksomhet, med orgnr (kolonne J) og beløp i
# kroner hittil i år (kolonne N, «Regnskap HIÅ»). Desemberfilen (periode 13 =
# årsoppgjøret) gir hele året. Bare bruttobudsjetterte virksomheter er med;
# universitetene finnes bare som tilskudd fra departementet.
#
# Kjøp av varer og tjenester etter standard kontoplan for staten:
#   drift:       40-45 (varer, fremmedytelser), 61-69 (unntatt 60x avskrivninger),
#                70-77 (unntatt 71x godtgjørelser til ansatte)
#   investering: 47-49 (anskaffelse av anleggsmidler i kontantregnskapet)
# Lønn (5), tilskudd (8), finans, tap og erstatninger (78) holdes utenfor.

RAADATA_REQUIRED = {"Orgnr", "Artskonto", "Regnskap HIÅ"}


def purchase_category(artskonto: str) -> str | None:
    """'drift', 'investering' eller None for en artskonto (tre sifre)."""
    if not re.fullmatch(r"\d{3}", artskonto or ""):
        return None
    k2 = int(artskonto[:2])
    if 47 <= k2 <= 49:
        return "investering"
    if 40 <= k2 <= 45 or 61 <= k2 <= 69 or (70 <= k2 <= 77 and k2 != 71):
        return "drift"
    return None


def is_raadata(path: str | Path) -> bool:
    """Har filen rådata-oppsettet (Orgnr, Artskonto, Regnskap HIÅ)?"""
    for row in read_xlsx(path)[:10]:
        if RAADATA_REQUIRED <= set(row.values()):
            return True
    return False


def _raadata_period(header_rows: list[dict[str, str]]) -> tuple[int, int]:
    """(år, til-og-med-måned) fra topptekstene: «periode 202501 til: 202513» eller «01.01.- 31.12.2023»."""
    text = " ".join(" ".join(r.values()) for r in header_rows)
    m = re.search(r"til:?\s*(20\d\d)(\d\d)\b", text)
    if m:
        return int(m.group(1)), min(int(m.group(2)), 12)
    m = re.search(r"\d\d\.(\d\d)\.(20\d\d)", text.split("-")[-1]) or re.search(r"\d\d\.(\d\d)\.(20\d\d)", text)
    if m:
        return int(m.group(2)), int(m.group(1))
    raise ValueError("Fant ikke regnskapsperioden i toppteksten")


def load_raadata_xlsx(conn: sqlite3.Connection, path: str | Path) -> tuple[int, int, int]:
    """Last kjøp av varer og tjenester per virksomhet inn i `statsregnskap`.

    Én rad per orgnr og artskonto (summert over kapittel og post), source_file = filnavnet.
    Tidligere rader for samme år fra Kudos-årsrapportene fjernes, så tallene ikke telles dobbelt.
    Returnerer (år, måned, antall virksomheter).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Fant ikke {path}. Last ned fra https://statsregnskapet.dfo.no/last-ned først.")
    rows = read_xlsx(path)
    head_i = next(i for i, r in enumerate(rows[:20]) if RAADATA_REQUIRED <= set(r.values()))
    col = {v: k for k, v in rows[head_i].items()}
    year, month = _raadata_period(rows[:head_i])

    sums: dict[tuple[str, str], float] = {}
    names: dict[str, str] = {}
    org_names: dict[str, str] = {}
    unit_orgs: dict[str, set[str]] = {}
    for r in rows[head_i + 1:]:
        art = r.get(col["Artskonto"], "")
        orgnr = re.sub(r"\D", "", r.get(col["Orgnr"], ""))
        if purchase_category(art) is None or len(orgnr) != 9:
            continue
        try:
            amount = float(r.get(col["Regnskap HIÅ"], "0"))
        except ValueError:
            continue
        sums[(orgnr, art)] = sums.get((orgnr, art), 0.0) + amount
        org_names.setdefault(orgnr, r.get(col.get("Orgnavn", ""), ""))
        unit = r.get(col.get("Virksomhet", ""), "")
        if unit:
            unit_orgs.setdefault(unit, set()).add(orgnr)
            names.setdefault(orgnr, unit)
    # «Virksomhet» er den rapporterende virksomheten og kan samle flere orgnr (Helsedirektoratet
    # fører også HELFO). Bruk da navnet på orgnr («Orgnavn») så de skilles.
    for orgnr in org_names:
        if len(unit_orgs.get(names.get(orgnr, ""), ())) != 1:
            names[orgnr] = org_names[orgnr] or names.get(orgnr, "")

    conn.executescript(ANALYSIS_SCHEMA)
    conn.execute("DELETE FROM statsregnskap WHERE source_file = ?", (path.name,))
    # Rådata er regnskapet selv: erstatter tall lest fra årsrapporter (Kudos) for de samme virksomhetene.
    # Nettobudsjetterte virksomheter (universiteter m.fl.) finnes ikke i rådata og beholder Kudos-tallene.
    conn.executemany("DELETE FROM statsregnskap WHERE year = ? AND orgnr = ? AND source_file LIKE 'kudos:%'",
                     [(year, org) for org in names])
    conn.executemany(
        "INSERT INTO statsregnskap (year, orgnr, virksomhet, artskonto, amount, source_file) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(year, org, names[org], art, amount, path.name) for (org, art), amount in sums.items()],
    )
    conn.commit()
    return year, month, len(names)
