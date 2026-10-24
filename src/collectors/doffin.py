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
- Kontraktsperioden står også bare i detaljvisningen: eForms-kunngjøringer
  (fra 2023) har «Varighet» («48 MONTH») eller start-/sluttdato per delkontrakt.
  Eldre kunngjøringer (TED-format, engelske etiketter) har ofte bare fritekst
  («Opsjon på 1+1 år, total varighet inntil 4 år»), som vi leser så godt det går.

Kjør alene:
  python -m src.collectors.doffin --year 2025 --db data/offentlig_forbruk.db
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import re
import sqlite3
import time
from dataclasses import astuple, dataclass, fields
from datetime import date, datetime, timedelta
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
    # Kontraktsperiode, brukes til å fordele verdien over år (src/analysis/annualize.py).
    is_framework: int | None = None      # 1 = rammeavtale
    start_date: str | None = None
    end_date: str | None = None
    duration_months: float | None = None
    duration_source: str | None = None   # "varighet" | "datoer" | "tekst" | None


# ---------------------------------------------------------------------------
# HTTP-klient med rate limiting og retry
# ---------------------------------------------------------------------------

class DoffinClient:
    """Tynn klient som holder minst `min_interval` sekunder mellom kall."""

    def __init__(
        self,
        min_interval: float = 1.2,
        max_retries: int = 5,
        timeout: float = 60.0,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.min_interval = min_interval
        # Rå detaljvisninger lagres her (gzip), så parseren kan forbedres uten nye kall.
        self.cache_dir = Path(cache_dir) if cache_dir else None
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
        cached = self.cache_dir / f"{notice_id}.json.gz" if self.cache_dir else None
        if cached and cached.exists():
            with gzip.open(cached, "rt", encoding="utf-8") as f:
                return json.load(f)
        data = self.request("GET", NOTICE_URL.format(notice_id))
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(cached, "wt", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        return data


# ---------------------------------------------------------------------------
# Søk med paginering og automatisk oppdeling av datoperioder
# ---------------------------------------------------------------------------

def month_ranges(year: int, to_month: int = 12) -> Iterator[tuple[date, date]]:
    for m in range(1, to_month + 1):
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


_TOTAL_LABELS = ("verdien av alle kontrakter tildelt", "value of all contracts awarded",
                 "total value of all contracts awarded")
_FRAMEWORK_MAX_LABELS = ("maksimal verdi av rammeavtalene i denne prosedyren",
                         "maximum value of the framework agreements in this procedure")
_WINNER_LABELS = ("verdien av resultatet", "value of the result")


def awarded_value(notice: dict[str, Any]) -> tuple[float | None, str | None]:
    """Tildelt verdi fra detaljvisningen.

    Prioritet: total for prosedyren > maksimal verdi av rammeavtalene > summen av
    vinnernes verdier. Rammeavtaler oppgir ofte 1 NOK per vinner, eller samme
    rammeverdi for hver vinner, så vinnersummen er siste utvei.
    Eldre kunngjøringer (TED-format) har engelske etiketter.
    """
    total: tuple[float | None, str | None] = (None, None)
    framework_max: tuple[float | None, str | None] = (None, None)
    winner_sum, winner_cur = 0.0, None
    for node in _walk_eform(notice.get("eform")):
        label = str(node.get("label") or "").strip().lower()
        if label.startswith(_TOTAL_LABELS) and total[0] is None:
            total = parse_amount(node.get("value"))
        elif label.startswith(_FRAMEWORK_MAX_LABELS) and framework_max[0] is None:
            framework_max = parse_amount(node.get("value"))
        elif label in _WINNER_LABELS:
            v, c = parse_amount(node.get("value"))
            if v is not None:
                winner_sum += v
                winner_cur = winner_cur or c
    if total[0]:
        return total
    if framework_max[0]:
        return framework_max
    return (winner_sum, winner_cur) if winner_cur or winner_sum else total


# --- Kontraktsperiode -------------------------------------------------------

_DURATION_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(DAY|MONTH|YEAR)", re.I)
_UNIT_MONTHS = {"DAY": 12 / 365.25, "MONTH": 1.0, "YEAR": 12.0}
_NUMBER_WORDS = {"ett": 1, "en": 1, "to": 2, "tre": 3, "fire": 4, "fem": 5, "seks": 6,
                 "sju": 7, "syv": 7, "åtte": 8, "ni": 9, "ti": 10}
_N = r"(\d+|ett|en|to|tre|fire|fem|seks|sju|syv|åtte|ni|ti)"
# «2+1+1 år», «2 år + 1 år + 1 år»: summen er varigheten med opsjoner
_PLUS_RE = re.compile(r"\b(\d+)(?:\s*år)?((?:\s*\+\s*\d+(?:\s*år)?)+)\s*(år|måned(?:er)?)", re.I)
# «total varighet inntil 4 år», «varighet på 24 måneder», «avtalen gjelder i 3 år», «4 års varighet»
_TEXT_RES = [
    re.compile(r"(?:samlet|total|maksimal)e?\s+(?:avtale|kontrakts?)?(?:varighet|lengde|periode)\s+"
               r"(?:er\s+|blir\s+)?(?:på\s+|av\s+)?(?:inntil\s+|opp til\s+|maks(?:imalt|\.)?\s+)?" + _N
               + r"\s*(år|måned(?:er)?)", re.I),
    re.compile(r"(?:varighet|avtaleperiode|kontraktsperiode|løpetid):?\s+(?:er\s+|blir\s+)?(?:på\s+|av\s+)?"
               r"(?:inntil\s+|opp til\s+|maks(?:imalt|\.)?\s+)?" + _N + r"(?:\s*\([^)]*\))?\s*(år|måned(?:er)?)", re.I),
    re.compile(r"totalt\s+(?:inntil\s+|opptil\s+|opp til\s+|maks(?:imalt)?\s+)" + _N
               + r"(?:\s*\([^)]*\))?\s*(år|måned(?:er)?)", re.I),
    re.compile(r"(?:for|i)\s+en\s+periode\s+på\s+" + _N + r"(?:\s*\([^)]*\))?\s*(år|måned(?:er)?)", re.I),
    re.compile(r"(?:gjelder|varer|løper)\s+(?:i\s+)?(?:inntil\s+)?" + _N + r"\s*(år|måned(?:er)?)", re.I),
    re.compile(r"\b" + _N + r"\s*(års|måneders)\s+varighet", re.I),
]
_FRAMEWORK_LABELS = (
    "rammeavtale uten", "rammeavtale med", "rammeavtale. delvis", "framework agreement, with",
    "framework agreement. partly", "rammeavtalens maksimale verdi", "maksimal verdi av rammeavtalene",
    "maximum value of the framework", "reestimert verdi av rammeavtalen", "re-estimated value of the framework",
)


def _months(n: str, unit: str) -> float | None:
    v = _NUMBER_WORDS.get(n.lower()) if not n[0].isdigit() else float(n)
    if not v:
        return None
    return float(v) * (12.0 if unit.lower().startswith("år") else 1.0)


def duration_from_text(text: str) -> float | None:
    """Varighet i måneder fra fritekst, inkludert opsjoner («2+1+1 år» -> 48)."""
    total_rx, *other_rxs = _TEXT_RES
    m = total_rx.search(text)
    if m:
        return _months(m.group(1), m.group(2))
    m = _PLUS_RE.search(text)
    if m:
        total = float(m.group(1)) + sum(float(x) for x in re.findall(r"\d+", m.group(2)))
        return total * (12.0 if m.group(3).lower() == "år" else 1.0)
    for rx in other_rxs:
        m = rx.search(text)
        if m:
            return _months(m.group(1), m.group(2))
    return None


def _iso(raw: Any) -> str | None:
    """'22.04.2025' / '2025-04-22+02:00' -> '2025-04-22'."""
    s = str(raw or "").strip()
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None


@dataclass
class ContractPeriod:
    is_framework: int
    start_date: str | None = None
    end_date: str | None = None
    duration_months: float | None = None
    duration_source: str | None = None


def contract_period(notice: dict[str, Any]) -> ContractPeriod:
    """Rammeavtale ja/nei og kontraktsperiode fra detaljvisningen.

    Ved flere delkontrakter brukes tidligste start, seneste slutt og lengste varighet.
    Prioritet: start- og sluttdato > «Varighet» > fritekst. Opsjoner/fornyelser
    («Maksimal fornyelse») er ikke med i «Varighet», men er med i «2+1+1 år».
    """
    framework = False
    starts: list[str] = []
    ends: list[str] = []
    months: list[float] = []
    renewals = 0
    texts = [str(notice.get("heading") or ""), str(notice.get("description") or "")]
    for node in _walk_eform(notice.get("eform")):
        label = str(node.get("label") or "").strip().lower()
        value = node.get("value")
        if label.startswith(_FRAMEWORK_LABELS):
            framework = True
        if label in ("varighet", "duration"):
            m = _DURATION_RE.search(str(value or ""))
            if m:
                months.append(float(m.group(1).replace(",", ".")) * _UNIT_MONTHS[m.group(2).upper()])
        elif label.startswith(("varighet,", "duration,")) and re.fullmatch(r"\d+", str(value or "").strip()):
            # «Varighet, År» = 2, «Varighet, Måned» = 12, «Varighet, Dag» = 90
            unit = 12.0 if ("år" in label or "year" in label) else 1.0 if ("måned" in label or "month" in label) \
                else 12 / 365.25
            months.append(float(value) * unit)
        elif label in ("maksimal fornyelse", "maximum number of renewals") and str(value or "").strip().isdigit():
            renewals = max(renewals, int(value))
        elif label in ("startdato", "start date") and _iso(value):
            starts.append(_iso(value))  # type: ignore[arg-type]
        elif label in ("sluttdato", "end date") and _iso(value):
            ends.append(_iso(value))  # type: ignore[arg-type]
        elif value and isinstance(value, str) and len(value) > 20:
            texts.append(value)
    text = "\n".join(texts)
    if not framework and re.search(r"\brammeavtale", text, re.I):
        framework = True

    period = ContractPeriod(is_framework=int(framework))
    if starts:
        period.start_date = min(starts)
    if starts and ends and max(ends) > min(starts):
        period.end_date = max(ends)
        d0, d1 = date.fromisoformat(min(starts)), date.fromisoformat(max(ends))
        period.duration_months = round((d1 - d0).days / (365.25 / 12), 1)
        period.duration_source = "datoer"
    elif months:
        period.duration_months, period.duration_source = max(months), "varighet"
    else:
        m = duration_from_text(text)
        if m:
            period.duration_months, period.duration_source = m, "tekst"
    if renewals and period.duration_source in ("datoer", "varighet"):
        # Fornyelser er ikke med i oppgitt periode, men verdien omfatter dem som regel.
        # Regn 12 måneder per fornyelse, men ikke lenger enn 48 måneder totalt
        # (eller grunnperioden hvis den alene er lengre).
        base = period.duration_months or 0.0
        period.duration_months = round(min(base + 12.0 * renewals, max(base, 48.0)), 1)
        if period.end_date:
            period.end_date = None  # sluttdato gjelder bare grunnperioden
            period.duration_source = "varighet"
    return period


def _clean_orgnr(raw: Any) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits if len(digits) == 9 else None


def _first_buyer_orgnr(hit: dict[str, Any]) -> str | None:
    buyers = hit.get("buyer") or []
    return _clean_orgnr(buyers[0].get("organizationId")) if buyers else None


def buyer_list(notice: dict[str, Any]) -> list[tuple[str | None, str]]:
    """(orgnr, navn) for alle kjøperne i et søketreff eller en detaljvisning, i rekkefølge."""
    return [(_clean_orgnr(b.get("organizationId") or b.get("id")), re.sub(r"\s+", " ", str(b.get("name") or "")).strip())
            for b in notice.get("buyer") or []]


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
    period = contract_period(detail) if detail else None
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
        is_framework=period.is_framework if period else None,
        start_date=period.start_date if period else None,
        end_date=period.end_date if period else None,
        duration_months=period.duration_months if period else None,
        duration_source=period.duration_source if period else None,
    )


def collect(
    year: int,
    fetch_details: bool = True,
    max_notices: int | None = None,
    client: DoffinClient | None = None,
    to_month: int = 12,
    buyers: set[str] | None = None,
) -> list[DoffinContract]:
    """Hent alle kontraktstildelinger publisert i `year` (januar til og med `to_month`).

    to_month lar oss matche statsregnskapets hittil-i-år-tall, f.eks. 8 for januar-august.
    buyers: bare kunngjøringer der (første) kjøper har et av disse orgnr. Søket er
    billig, så filteret sparer først og fremst detaljkall (brukes for historikk).

    fetch_details=True gir faktisk tildelt verdi, men koster ett ekstra kall per
    kunngjøring (~3 200 kall for 2025, ca. en time med 1,2 s mellom kall).
    fetch_details=False bruker anslått verdi fra søket (få kall, mange tomme verdier).
    """
    client = client or DoffinClient()
    hits: dict[str, dict[str, Any]] = {}
    for start, end in month_ranges(year, to_month):
        for hit in iter_search_hits(client, start, end):
            if buyers is not None and _first_buyer_orgnr(hit) not in buyers:
                continue
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


# --- Leverandører (vinnere) ---------------------------------------------------

_ORG_SECTION_LABELS = ("virksomheter", "organisations")
_RESULT_SECTION_LABELS = ("resultater", "results")
_WINNER_LABELS_NODE = ("vinner", "winner")
_NAME_LABELS = ("offisielt navn", "official name")
_ORGNR_LABELS = ("registreringsnummer", "registration number", "national registration number")


@dataclass
class Winner:
    name: str
    orgnr: str | None


def _label(node: dict[str, Any]) -> str:
    return str(node.get("label") or "").strip().lower()


def _clean_name(raw: Any) -> str:
    # TED-kunngjøringer pakker navnet inn som "[ PAS Ingeniører AS ] --- "
    text = re.sub(r"^\[|\]?\s*-*\s*$", "", str(raw or "").strip()).strip(" []")
    text = re.sub(r"\*[^*]*\*|\binvitasjon ønskes her\b", "", text, flags=re.I)  # "NORCONSULT AS *INVITASJON ØNSKES HER*"
    return re.sub(r"\s+", " ", text).strip()


def winners(notice: dict[str, Any]) -> list[Winner]:
    """Leverandørene som vant kontrakten, med orgnr når kunngjøringen har det.

    Navnene tas fra `awardedNames` i detaljvisningen, ellers fra «Vinner» under
    «Resultater». Rollene i virksomhetslisten kan ikke brukes: når en kunngjøring
    har flere virksomheter, får alle (også kjøperen og tingretten) alle rollene.
    Orgnr hentes derfor ved å slå opp vinnernavnet i virksomhetslisten.
    """
    names = [_clean_name(n) for n in notice.get("awardedNames") or []]
    if not any(names):
        names = []
        for section in notice.get("eform") or []:
            if _label(section) not in _RESULT_SECTION_LABELS:
                continue
            for node in _walk_eform(section.get("sections")):
                if _label(node) in _WINNER_LABELS_NODE:
                    names += [_clean_name(n.get("value")) for n in node.get("sections") or []
                              if _label(n) in _NAME_LABELS]

    orgnr_by_name: dict[str, str] = {}
    for section in notice.get("eform") or []:
        if _label(section) not in _ORG_SECTION_LABELS:
            continue
        for org in section.get("sections") or []:
            fields_ = {_label(n): n.get("value") for n in org.get("sections") or []}
            name = next((_clean_name(fields_[k]) for k in _NAME_LABELS if fields_.get(k)), None)
            orgnr = next((_clean_orgnr(fields_[k]) for k in _ORGNR_LABELS if fields_.get(k)), None)
            if name and orgnr:
                orgnr_by_name.setdefault(name.lower(), orgnr)

    found: dict[str, Winner] = {}
    for name in names:
        if name:
            found.setdefault(name.lower(), Winner(name=name, orgnr=orgnr_by_name.get(name.lower())))
    return list(found.values())


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
    fetched_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    is_framework     INTEGER,
    start_date       TEXT,
    end_date         TEXT,
    duration_months  REAL,
    duration_source  TEXT
);
CREATE INDEX IF NOT EXISTS idx_doffin_buyer_year ON doffin_contracts (buyer_orgnr, year);

CREATE TABLE IF NOT EXISTS doffin_winners (
    contract_id  TEXT,
    position     INTEGER,  -- rekkefølgen i kunngjøringen
    name         TEXT,
    orgnr        TEXT,     -- NULL når kunngjøringen bare har navn
    PRIMARY KEY (contract_id, position)
);
CREATE INDEX IF NOT EXISTS idx_doffin_winners_orgnr ON doffin_winners (orgnr);

-- Alle kjøperne i kunngjøringen. doffin_contracts.buyer_orgnr er den første; ved felles
-- anskaffelser (f.eks. Politiets fellestjenester med Tolletaten og Kriminalomsorgen) står resten her.
CREATE TABLE IF NOT EXISTS doffin_buyers (
    contract_id  TEXT,
    position     INTEGER,  -- 0 = første kjøper
    orgnr        TEXT,
    name         TEXT,
    PRIMARY KEY (contract_id, position)
);
CREATE INDEX IF NOT EXISTS idx_doffin_buyers_orgnr ON doffin_buyers (orgnr);
"""
# Kolonner lagt til etter fase 1; eldre databaser får dem med ALTER TABLE.
_ADDED_COLUMNS = {
    "is_framework": "INTEGER", "start_date": "TEXT", "end_date": "TEXT",
    "duration_months": "REAL", "duration_source": "TEXT",
}


def init_db(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(doffin_contracts)")}
    for col, typ in _ADDED_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE doffin_contracts ADD COLUMN {col} {typ}")
    conn.commit()
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


def save_winners(conn: sqlite3.Connection, contract_id: str, found: Iterable[Winner]) -> int:
    conn.execute("DELETE FROM doffin_winners WHERE contract_id = ?", (contract_id,))
    rows = [(contract_id, i, w.name, w.orgnr) for i, w in enumerate(found)]
    conn.executemany("INSERT INTO doffin_winners (contract_id, position, name, orgnr) VALUES (?, ?, ?, ?)", rows)
    return len(rows)


def save_buyers(conn: sqlite3.Connection, contract_id: str, found: list[tuple[str | None, str]]) -> int:
    conn.execute("DELETE FROM doffin_buyers WHERE contract_id = ?", (contract_id,))
    conn.executemany("INSERT INTO doffin_buyers (contract_id, position, orgnr, name) VALUES (?, ?, ?, ?)",
                     [(contract_id, i, o, n) for i, (o, n) in enumerate(found)])
    return len(found)


def collect_winners(
    conn: sqlite3.Connection,
    client: DoffinClient | None = None,
    buyers: set[str] | None = None,
    year_from: int | None = None,
    running_in: int | None = None,
    refresh: bool = False,
) -> tuple[int, int]:
    """Hent vinnerne for kontraktene i databasen (ett detaljkall per kunngjøring, med cache).

    buyers/year_from begrenser hvilke kontrakter som hentes; running_in tar bare
    kontrakter som løper i det året (krever doffin_contract_years). Uten refresh hoppes
    kontrakter som allerede har vinnere over, så kjøringen kan avbrytes og fortsettes.
    Returnerer (antall kunngjøringer behandlet, antall vinnere lagret).
    """
    client = client or DoffinClient()
    sql = "SELECT id FROM doffin_contracts c WHERE 1 = 1"
    params: list[Any] = []
    marks = ", ".join("?" for _ in buyers or ())
    if buyers is not None and running_in is None:
        sql += f" AND buyer_orgnr IN ({marks})"
        params += sorted(buyers)
    if year_from is not None:
        sql += " AND year >= ?"
        params.append(year_from)
    if running_in is not None:
        # Kjøperen i doffin_contract_years er regnskapsvirksomheten, også når en underenhet
        # (f.eks. et politidistrikt) står som kjøper i Doffin.
        sql += " AND id IN (SELECT contract_id FROM doffin_contract_years WHERE year = ?"
        params.append(running_in)
        if buyers is not None:
            sql += f" AND buyer_orgnr IN ({marks})"
            params += sorted(buyers)
        sql += ")"
    if not refresh:
        sql += " AND NOT EXISTS (SELECT 1 FROM doffin_winners w WHERE w.contract_id = c.id)"
    ids = [r[0] for r in conn.execute(sql + " ORDER BY id", params)]
    done = saved = 0
    for i, notice_id in enumerate(ids, 1):
        try:
            detail = client.notice(notice_id)
        except DoffinError as e:
            log.warning("Hopper over vinnere for %s: %s", notice_id, e)
            continue
        saved += save_winners(conn, notice_id, winners(detail))
        save_buyers(conn, notice_id, buyer_list(detail))
        done += 1
        if i % 100 == 0:
            conn.commit()
            log.info("Vinnere: %d/%d", i, len(ids))
    conn.commit()
    return done, saved


def main() -> None:
    ap = argparse.ArgumentParser(description="Hent Doffin-kontrakter til SQLite")
    ap.add_argument("--year", type=int, default=date.today().year - 1)
    ap.add_argument("--db", default="data/offentlig_forbruk.db")
    ap.add_argument("--no-details", action="store_true", help="Raskt: bruk anslått verdi fra søket")
    ap.add_argument("--max-notices", type=int, help="Stopp etter N kunngjøringer (for testing)")
    ap.add_argument("--to-month", type=int, default=12, help="Hent bare januar til og med denne måneden")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    contracts = collect(a.year, fetch_details=not a.no_details, max_notices=a.max_notices, to_month=a.to_month)
    conn = init_db(a.db)
    try:
        n = save_contracts(conn, contracts)
    finally:
        conn.close()
    print(f"Lagret {n} kontrakter for {a.year} i {a.db}")


if __name__ == "__main__":
    main()
