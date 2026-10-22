"""Join Doffin-kontrakter mot statsregnskapet og flagg avvik over 5 %.

Statsregnskapet har ingen leverandørdimensjon, så koblingen går på
*kjøper*: Doffin `buyer_orgnr` <-> statsregnskapets virksomhet (orgnr), per år.
Finnes ikke orgnr i CSV-en, faller vi tilbake på normalisert virksomhetsnavn.

Statsregnskapet.dfo.no stenger for skytilkoblinger, så CSV-en lastes ned
manuelt fra https://statsregnskapet.dfo.no/last-ned og gis inn som filsti.

Forventede kolonner i CSV-en (navnene gjenkjennes automatisk, store/små
bokstaver og æøå spiller ingen rolle; overstyr med `columns=` ved behov):
  år          : "År", "Regnskapsår", "Aar", "Year"
  orgnr       : "Orgnr", "Organisasjonsnummer", "Virksomhetsnummer"  (anbefalt)
  virksomhet  : "Virksomhet", "Virksomhetsnavn", "Navn"               (påkrevd hvis orgnr mangler)
  beløp       : "Beløp", "Belop", "Regnskap", "Sum", "Beløp i kroner"
  artskonto   : "Artskonto", "Art", "Konto"                           (valgfri, for filtrering)
Skilletegn (; , tab) og norsk tallformat ("1 234 567,89") håndteres.

DFØs månedsrapport «Utgifter til staten» (xlsx, kapittel/post uten orgnr)
lastes med src/collectors/statsregnskap.py, som fyller samme tabell.

Flaggingen bygger videre på `flags`-tabellen fra database_loader.py
(flag_type, severity, description, source_id) med flag_type = 'doffin_avvik'.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

THRESHOLD = 0.05  # 5 %: flagg når kontraktsum og regnskap avviker mer enn dette
FLAG_TYPE = "doffin_avvik"

COLUMN_ALIASES: dict[str, list[str]] = {
    "year": ["ar", "regnskapsar", "aar", "year", "periode_ar"],
    "orgnr": ["orgnr", "organisasjonsnummer", "org_nr", "virksomhetsnummer", "orgnummer", "virksomhet_orgnr"],
    "name": ["virksomhet", "virksomhetsnavn", "navn", "virksomhet_navn", "etat"],
    "amount": ["belop", "regnskap", "sum", "belop_i_kroner", "regnskapsbelop", "belop_nok", "amount"],
    "artskonto": ["artskonto", "art", "konto", "artskontonummer"],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS statsregnskap (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    year        INTEGER,
    orgnr       TEXT,
    virksomhet  TEXT,
    artskonto   TEXT,
    amount      REAL,
    source_file TEXT
);
CREATE INDEX IF NOT EXISTS idx_statsregnskap_org_year ON statsregnskap (orgnr, year);

CREATE TABLE IF NOT EXISTS flags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    flag_type   TEXT,
    severity    TEXT,
    description TEXT,
    source_id   TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contract_spend_comparison (
    orgnr          TEXT,
    virksomhet     TEXT,
    year           INTEGER,
    contract_sum   REAL,
    contract_count INTEGER,
    spend_sum      REAL,
    deviation      REAL,     -- (contract_sum - spend_sum) / spend_sum
    match_method   TEXT,     -- 'orgnr' | 'navn'
    flagged        INTEGER,
    PRIMARY KEY (orgnr, year)
);
"""


@dataclass
class Comparison:
    orgnr: str
    virksomhet: str | None
    year: int
    contract_sum: float
    contract_count: int
    spend_sum: float
    match_method: str

    @property
    def deviation(self) -> float | None:
        if not self.spend_sum:
            return None
        return (self.contract_sum - self.spend_sum) / abs(self.spend_sum)


# ---------------------------------------------------------------------------
# Innlasting av statsregnskap-CSV
# ---------------------------------------------------------------------------

def _key(text: str) -> str:
    """'Beløp i kroner' -> 'belop_i_kroner'"""
    t = text.strip().lower().replace("ø", "o").replace("æ", "ae").replace("å", "a")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


def normalize_name(name: str | None) -> str:
    """For navnematching: 'Statens vegvesen ' == 'STATENS VEGVESEN'."""
    return _key(name or "").replace("_", " ")


def parse_number(raw: str | None) -> float | None:
    """'1 234 567,89' / '-1234567.89' / '1.234.567,89' -> float."""
    if raw is None:
        return None
    s = re.sub(r"[\s  ]", "", str(raw)).replace("kr", "")
    if s in ("", "-"):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _detect_columns(header: list[str], overrides: dict[str, str] | None) -> dict[str, int]:
    keys = [_key(h) for h in header]
    found: dict[str, int] = {}
    for field, aliases in COLUMN_ALIASES.items():
        if overrides and field in overrides:
            wanted = _key(overrides[field])
            if wanted not in keys:
                raise ValueError(f"Kolonnen '{overrides[field]}' finnes ikke. Kolonner: {header}")
            found[field] = keys.index(wanted)
            continue
        for alias in aliases:
            if alias in keys:
                found[field] = keys.index(alias)
                break
    missing = [f for f in ("year", "amount") if f not in found]
    if "orgnr" not in found and "name" not in found:
        missing.append("orgnr eller virksomhet")
    if missing:
        raise ValueError(
            f"Fant ikke kolonne(r) {missing} i statsregnskap-CSV. Kolonner i filen: {header}. "
            "Bruk --col-year/--col-orgnr/--col-name/--col-amount for å angi dem."
        )
    return found


def load_statsregnskap_csv(
    conn: sqlite3.Connection,
    csv_path: str | Path,
    columns: dict[str, str] | None = None,
    artskonto_prefixes: tuple[str, ...] = (),
) -> int:
    """Les statsregnskap-CSV inn i tabellen `statsregnskap` (erstatter rader fra samme fil).

    artskonto_prefixes: ta bare med rader der artskonto starter med et av disse
    (f.eks. ("6", "7") for driftsutgifter til kjøp av varer og tjenester).
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Fant ikke {path}. Last ned CSV fra https://statsregnskapet.dfo.no/last-ned først."
        )
    conn.executescript(SCHEMA)

    raw = path.read_bytes()
    text = raw.decode("utf-8-sig") if _is_utf8(raw) else raw.decode("cp1252")
    sample = text[:20000]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";"  # DFØ-standard
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next(reader, None)
    if not header:
        raise ValueError(f"{path} er tom")
    col = _detect_columns(header, columns)

    def cell(row: list[str], field: str) -> str | None:
        i = col.get(field)
        return row[i].strip() if i is not None and i < len(row) else None

    rows = []
    for row in reader:
        if not any(row):
            continue
        art = cell(row, "artskonto")
        if artskonto_prefixes and not (art and art.startswith(artskonto_prefixes)):
            continue
        year_raw = re.sub(r"\D", "", cell(row, "year") or "")[:4]
        amount = parse_number(cell(row, "amount"))
        if not year_raw or amount is None:
            continue
        orgnr = re.sub(r"\D", "", cell(row, "orgnr") or "") or None
        rows.append((int(year_raw), orgnr, cell(row, "name"), art, amount, path.name))

    conn.execute("DELETE FROM statsregnskap WHERE source_file = ?", (path.name,))
    conn.executemany(
        "INSERT INTO statsregnskap (year, orgnr, virksomhet, artskonto, amount, source_file) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def _is_utf8(raw: bytes) -> bool:
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

def build_comparisons(conn: sqlite3.Connection, currency: str = "NOK") -> list[Comparison]:
    """Summer kontrakter og regnskap per virksomhet og år og koble dem.

    1) orgnr-match: doffin_contracts.buyer_orgnr = statsregnskap.orgnr
    2) navne-match for statsregnskap-rader uten orgnr: normalisert buyer_name = virksomhet
    Kun kontrakter med verdi i valgt valuta (eller ukjent valuta) tas med.
    """
    contracts = conn.execute(
        """
        SELECT buyer_orgnr, MAX(buyer_name), year, SUM(value), COUNT(*)
        FROM doffin_contracts
        WHERE value IS NOT NULL AND buyer_orgnr IS NOT NULL
          AND (currency = ? OR currency IS NULL)
        GROUP BY buyer_orgnr, year
        """,
        (currency,),
    ).fetchall()
    by_org = {(org, yr): (name, total, n) for org, name, yr, total, n in contracts}
    by_name = {(normalize_name(name), yr): (org, total, n) for org, name, yr, total, n in contracts}

    spend_org = conn.execute(
        "SELECT orgnr, MAX(virksomhet), year, SUM(amount) FROM statsregnskap "
        "WHERE orgnr IS NOT NULL GROUP BY orgnr, year"
    ).fetchall()
    spend_name = conn.execute(
        "SELECT virksomhet, year, SUM(amount) FROM statsregnskap "
        "WHERE orgnr IS NULL AND virksomhet IS NOT NULL GROUP BY virksomhet, year"
    ).fetchall()

    out: list[Comparison] = []
    for org, name, yr, spend in spend_org:
        if (org, yr) in by_org:
            dname, total, n = by_org[(org, yr)]
            out.append(Comparison(org, name or dname, yr, total, n, spend, "orgnr"))
    matched = {(c.orgnr, c.year) for c in out}
    for name, yr, spend in spend_name:
        hit = by_name.get((normalize_name(name), yr))
        if hit and (hit[0], yr) not in matched:
            org, total, n = hit
            out.append(Comparison(org, name, yr, total, n, spend, "navn"))
            matched.add((org, yr))
    return out


# ---------------------------------------------------------------------------
# Flagging
# ---------------------------------------------------------------------------

def _fmt_nok(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ") + " kr"


def flag_deviations(conn: sqlite3.Connection, threshold: float = THRESHOLD) -> int:
    """Kjør join og skriv flagg der |avvik| > threshold. Returnerer antall flagg.

    Idempotent: tidligere 'doffin_avvik'-flagg og sammenligninger slettes først.
    """
    conn.executescript(SCHEMA)
    comparisons = build_comparisons(conn)

    conn.execute("DELETE FROM flags WHERE flag_type = ?", (FLAG_TYPE,))
    conn.execute("DELETE FROM contract_spend_comparison")
    n_flags = 0
    for c in comparisons:
        dev = c.deviation
        flagged = dev is not None and abs(dev) > threshold
        conn.execute(
            "INSERT OR REPLACE INTO contract_spend_comparison VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (c.orgnr, c.virksomhet, c.year, c.contract_sum, c.contract_count,
             c.spend_sum, dev, c.match_method, int(flagged)),
        )
        if not flagged:
            continue
        direction = "høyere" if dev > 0 else "lavere"
        conn.execute(
            "INSERT INTO flags (flag_type, severity, description, source_id) VALUES (?, ?, ?, ?)",
            (
                FLAG_TYPE,
                "warning",
                f"{c.virksomhet or c.orgnr} {c.year}: Doffin-kontrakter {_fmt_nok(c.contract_sum)} "
                f"({c.contract_count} stk) er {abs(dev):.1%} {direction} enn statsregnskapet "
                f"{_fmt_nok(c.spend_sum)} (match på {c.match_method})",
                f"{c.orgnr}:{c.year}",
            ),
        )
        n_flags += 1
    conn.commit()
    return n_flags


def summary(conn: sqlite3.Connection) -> dict[str, int]:
    """Nøkkeltall for utskrift i run_phase1.py."""
    conn.executescript(SCHEMA)
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "contracts": q("SELECT COUNT(*) FROM doffin_contracts"),
        "contracts_with_value": q("SELECT COUNT(*) FROM doffin_contracts WHERE value IS NOT NULL"),
        "statsregnskap_rows": q("SELECT COUNT(*) FROM statsregnskap"),
        "matched_agency_years": q("SELECT COUNT(*) FROM contract_spend_comparison"),
        "flags": q(f"SELECT COUNT(*) FROM flags WHERE flag_type = '{FLAG_TYPE}'"),
    }
