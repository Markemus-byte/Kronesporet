"""Doffin-innsamler for Kronesporet (fase 1).

Henter kunngjøringer om inngåtte kontrakter fra Doffin og lagrer dem i SQLite.

API (åpent, ingen nøkkel), samme som doffin.no-nettsiden bruker:
  POST https://api.doffin.no/webclient/api/v2/search-api/search
  GET  https://api.doffin.no/webclient/api/v2/notices-api/notices/{id}

Viktige egenskaper ved API-et (testet oktober 2026):
- Søket gir maks 1000 tilgjengelige treff per spørring (page * numHitsPerPage),
  så vi deler opp i måneder og halverer datoperioden ved behov.
- Søketreffet har kjøperens orgnr (buyer[].organizationId) og bare *anslått*
  verdi. Faktisk tildelt verdi ("Verdien av alle kontrakter tildelt i denne
  prosedyren") finnes kun i detaljvisningen (eform-treet), med tall som
  "297 996 154 NOK" (mellomrom/hardt mellomrom som tusenskille).

Kjør alene:
  python -m src.collectors.doffin --year 2025 --db data/offentlig_forbruk.db
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import time
from dataclasses import astuple, dataclass, fields
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

import requests

SEARCH_URL = "https://api.doffin.no/webclient/api/v2/search-api/search"
NOTICE_URL = "https://api.doffin.no/webclient/api/v2/notices-api/notices/{}"
PUBLIC_URL = "https://doffin.no/notices/{}"

AWARD_TYPE = "ANNOUNCEMENT_OF_CONCLUSION_OF_CONTRACT"
MAX_ACCESSIBLE_HITS = 1000
PAGE_SIZE = 100
USER_AGENT = "Kronesporet.no/0.1 (forskning paa offentlig pengebruk)"

log = logging.getLogger(__name__)


class DoffinError(RuntimeError):
    """Doffin svarte ikke som forventet etter alle forsøk."""


@dataclass
class DoffinContract:
    id: str
    buyer_orgnr: str | None
    buyer_name: str | None
    title: str | None
    value: float | None
    year: int | None
    url: str
    # Ekstra felt som gjør join-en tryggere (valuta og hvor verdien kom fra).
    currency: str | None = None
    value_type: str | None = None  # "awarded" | "estimated" | None
    publication_date: str | None = None


# ---------------------------------------------------------------------------
# HTTP-klient med rate limiting og retry
# ---------------------------------------------------------------------------

class DoffinClient:
    """Tynn klient som holder minst `min_interval` sekunder mellom kall."""

    def __init__(self, min_interval: float = 1.2, max_retries: int = 5, timeout: float = 60.0) -> None:
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_call = 0.0
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "User-Agent": USER_AGENT})

    def _wait_for_slot(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()

    def request(self, method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error = ""
        for attempt in range(self.max_retries):
            self._wait_for_slot()
            try:
                r = self.session.request(method, url, json=body, timeout=self.timeout)
            except requests.RequestException as e:
                last_error = str(e)
            else:
                if r.status_code == 200:
                    return r.json()
                last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code == 429 or r.status_code >= 500:
                    retry_after = r.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        time.sleep(int(retry_after))
                else:
                    # 4xx (unntatt 429) blir ikke bedre av å prøve igjen.
                    break
            backoff = 2 ** attempt
            log.warning("Doffin-kall feilet (%s), prøver igjen om %ss: %s", url, backoff, last_error)
            time.sleep(backoff)
        raise DoffinError(f"{method} {url} feilet: {last_error}")

    def search(self, date_from: date, date_to: date, page: int, notice_type: str = AWARD_TYPE) -> dict[str, Any]:
        body = {
            "numHitsPerPage": PAGE_SIZE,
            "page": page,
            "searchString": "",
            "sortBy": "PUBLICATION_DATE_DESC",
            "facets": {
                "type": {"checkedItems": [notice_type]},
                "publicationDate": {"from": date_from.isoformat(), "to": date_to.isoformat()},
            },
        }
        return self.request("POST", SEARCH_URL, body)

    def notice(self, notice_id: str) -> dict[str, Any]:
        return self.request("GET", NOTICE_URL.format(notice_id))


# ---------------------------------------------------------------------------
# Søk med paginering og automatisk oppdeling av datoperioder
# ---------------------------------------------------------------------------

def month_ranges(year: int) -> Iterator[tuple[date, date]]:
    for m in range(1, 13):
        start = date(year, m, 1)
        end = date(year + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        yield start, end


def iter_search_hits(client: DoffinClient, date_from: date, date_to: date) -> Iterator[dict[str, Any]]:
    """Alle treff i perioden. Halverer perioden hvis den har over 1000 treff."""
    first = client.search(date_from, date_to, page=1)
    total = first.get("numHitsTotal", 0)
    if total > first.get("numHitsAccessible", total):
        if date_from == date_to:
            log.warning("%s har %d treff, kan bare hente de første %d", date_from, total, MAX_ACCESSIBLE_HITS)
        else:
            mid = date_from + (date_to - date_from) // 2
            yield from iter_search_hits(client, date_from, mid)
            yield from iter_search_hits(client, mid + timedelta(days=1), date_to)
            return

    data, page, seen = first, 1, 0
    while True:
        hits = data.get("hits") or []
        yield from hits
        seen += len(hits)
        if not hits or seen >= min(total, MAX_ACCESSIBLE_HITS):
            return
        page += 1
        data = client.search(date_from, date_to, page=page)


# ---------------------------------------------------------------------------
# Normalisering
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"-?\d[\d\s  .,]*")
_CURRENCY_RE = re.compile(r"\b([A-Z]{3})\b")


def parse_amount(text: Any) -> tuple[float | None, str | None]:
    """'297 996 154 NOK' -> (297996154.0, 'NOK'). Tåler også '1,234,567.50 EUR' og '1 234,50'."""
    if text is None:
        return None, None
    if isinstance(text, (int, float)):
        return float(text), None
    s = str(text)
    m = _AMOUNT_RE.search(s)
    if not m:
        return None, None
    num = re.sub(r"[\s  ]", "", m.group(0)).rstrip(".,")
    if "," in num and "." in num:
        # Den siste av , og . er desimalskilletegn.
        dec = "," if num.rfind(",") > num.rfind(".") else "."
        num = num.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in num:
        # "1,234,567" (tusenskille) eller "1234,50" (desimal).
        num = num.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+", num) else num.replace(",", ".")
    elif num.count(".") > 1:
        num = num.replace(".", "")
    try:
        value = float(num)
    except ValueError:
        return None, None
    cur = _CURRENCY_RE.search(s[m.end():] + " " + s[: m.start()])
    return value, (cur.group(1) if cur else None)


def _walk_eform(sections: Iterable[dict[str, Any]] | None) -> Iterator[dict[str, Any]]:
    for node in sections or []:
        yield node
        yield from _walk_eform(node.get("sections"))


def awarded_value(notice: dict[str, Any]) -> tuple[float | None, str | None]:
    """Total tildelt verdi fra detaljvisningen, eller summen av vinnernes verdier."""
    total: tuple[float | None, str | None] = (None, None)
    winner_sum, winner_cur = 0.0, None
    for node in _walk_eform(notice.get("eform")):
        label = str(node.get("label") or "")
        if label.startswith("Verdien av alle kontrakter tildelt") and total[0] is None:
            total = parse_amount(node.get("value"))
        elif label == "Verdien av resultatet":
            v, c = parse_amount(node.get("value"))
            if v is not None:
                winner_sum += v
                winner_cur = winner_cur or c
    if total[0] is not None:
        return total
    return (winner_sum, winner_cur) if winner_cur or winner_sum else (None, None)


def _clean_orgnr(raw: Any) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits if len(digits) == 9 else None


def normalize(hit: dict[str, Any], detail: dict[str, Any] | None = None) -> DoffinContract:
    """Gjør et søketreff (+ ev. detaljvisning) om til én rad.

    Ved flere kjøpere (felles innkjøp) brukes den første.
    """
    buyers = hit.get("buyer") or []
    buyer = buyers[0] if buyers else {}
    orgnr = _clean_orgnr(buyer.get("organizationId"))
    if detail and not orgnr:
        orgnr = _clean_orgnr(((detail.get("buyer") or [{}])[0]).get("id"))

    value, currency, value_type = None, None, None
    if detail:
        value, currency = awarded_value(detail)
        value_type = "awarded" if value is not None else None
    if value is None:
        est = hit.get("estimatedValue") or {}
        if est.get("amount") is not None:
            value, currency, value_type = float(est["amount"]), est.get("currencyCode") or est.get("code"), "estimated"

    pub = hit.get("publicationDate") or (detail or {}).get("publicationDate")
    return DoffinContract(
        id=hit["id"],
        buyer_orgnr=orgnr,
        buyer_name=buyer.get("name"),
        title=hit.get("heading"),
        value=value,
        year=int(pub[:4]) if pub else None,
        url=PUBLIC_URL.format(hit["id"]),
        currency=currency,
        value_type=value_type,
        publication_date=pub,
    )


def collect(
    year: int,
    fetch_details: bool = True,
    max_notices: int | None = None,
    client: DoffinClient | None = None,
) -> list[DoffinContract]:
    """Hent alle kontraktstildelinger publisert i `year`.

    fetch_details=True gir faktisk tildelt verdi, men koster ett ekstra kall per
    kunngjøring (~3 200 kall for 2025, ca. en time med 1,2 s mellom kall).
    fetch_details=False bruker anslått verdi fra søket (få kall, mange tomme verdier).
    """
    client = client or DoffinClient()
    hits: dict[str, dict[str, Any]] = {}
    for start, end in month_ranges(year):
        for hit in iter_search_hits(client, start, end):
            hits.setdefault(hit["id"], hit)
            if max_notices and len(hits) >= max_notices:
                break
        log.info("%s: %d kunngjøringer så langt", start.strftime("%Y-%m"), len(hits))
        if max_notices and len(hits) >= max_notices:
            break

    contracts: list[DoffinContract] = []
    for i, (notice_id, hit) in enumerate(sorted(hits.items()), 1):
        detail = None
        if fetch_details:
            try:
                detail = client.notice(notice_id)
            except DoffinError as e:
                log.warning("Hopper over detaljer for %s: %s", notice_id, e)
        contracts.append(normalize(hit, detail))
        if fetch_details and i % 100 == 0:
            log.info("Detaljer: %d/%d", i, len(hits))
    return contracts


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS doffin_contracts (
    id               TEXT PRIMARY KEY,
    buyer_orgnr      TEXT,
    buyer_name       TEXT,
    title            TEXT,
    value            REAL,
    year             INTEGER,
    url              TEXT,
    currency         TEXT,
    value_type       TEXT,
    publication_date TEXT,
    fetched_at       TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_doffin_buyer_year ON doffin_contracts (buyer_orgnr, year);
"""


def init_db(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def save_contracts(conn: sqlite3.Connection, contracts: Iterable[DoffinContract]) -> int:
    """Upsert på id, så innsamlingen kan kjøres flere ganger uten duplikater."""
    cols = [f.name for f in fields(DoffinContract)]
    sql = (
        f"INSERT OR REPLACE INTO doffin_contracts ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})"
    )
    rows = [astuple(c) for c in contracts]
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Hent Doffin-kontrakter til SQLite")
    ap.add_argument("--year", type=int, default=date.today().year - 1)
    ap.add_argument("--db", default="data/offentlig_forbruk.db")
    ap.add_argument("--no-details", action="store_true", help="Raskt: bruk anslått verdi fra søket")
    ap.add_argument("--max-notices", type=int, help="Stopp etter N kunngjøringer (for testing)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    contracts = collect(a.year, fetch_details=not a.no_details, max_notices=a.max_notices)
    conn = init_db(a.db)
    try:
        n = save_contracts(conn, contracts)
    finally:
        conn.close()
    print(f"Lagret {n} kontrakter for {a.year} i {a.db}")


if __name__ == "__main__":
    main()
