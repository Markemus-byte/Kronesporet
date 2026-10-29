"""Varselmotoren: hvilke kontrakter hver abonnent skal ha beskjed om etter en oppdatering.

Abonnentene ligger i Cloudflare D1 (worker/schema.sql) og hentes som JSON fra Workeren
(GET /api/abonnenter), eller fra en lokal JSON-fil med samme form ved testing:

  [{"email": "...", "token": "...", "status": "active", "plan": "standard", "horisonter": "3,6,12",
    "csv": 1, "sist_sendt": "2026-09-30" | null, "sist_sendt_tid": "2026-10-01 07:02:11" | null,
    "utvalg": [{"type": "kjoper", "verdi": "974760673"}, {"type": "cpv", "verdi": "72"}]}]

Utvalget: en kontrakt treffer når den treffer en av kjøperne eller leverandørene (eller ingen er
valgt), og en av CPV-kodene eller søkeordene (eller ingen er valgt). En kjøper tar med underenhetene
(Politiet tar med politidistriktene). CPV-koder sammenlignes på prefiks: 72 er alle IT-tjenester.

Hva som sendes:
- Nye tildelinger: kontrakter som er lagt inn i databasen (doffin_contracts.fetched_at) etter abonnentens
  forrige varsel. Første varsel til en ny abonnent tar med tildelinger fra siste måned. Jobben kan
  derfor kjøres hver dag: nye abonnenter får første varsel dagen etter, og de andre får bare noe når
  den månedlige oppdateringen har lagt inn nye data.
- Avtaler som løper ut: en avtale kommer med når sluttdatoen krysser en av horisontene (3, 6 eller
  12 måneder) siden abonnentens forrige varsel, så hver avtale nevnes én gang per horisont. Første
  varsel tar med alle som slutter innen den lengste horisonten. Sluttdatoen er fra Doffin når den er
  oppgitt, ellers et anslag ut fra oppgitt varighet eller fritekst. Det står i varselet. Avtaler
  uten noen opplysning om varighet (standardvarighet) er ikke med.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from src.analysis.annualize import DAYS_PER_MONTH, contract_period
from src.web.data import display_name

SENDING = {"active", "gratis"}            # statusene som får varsler (resten venter, betaler ikke eller er avmeldt)
HORIZONS = (3, 6, 12)                     # måneder
EXPIRY_SOURCES = {"datoer": "sluttdato i Doffin", "varighet": "anslag fra oppgitt varighet",
                  "tekst": "anslag fra varighet i teksten"}
WELCOME_DAYS = 31                         # første varsel: tildelinger publisert så mange dager før
BACKFILL_DAYS = 92                        # nye i databasen, men eldre enn dette før forrige ref: etterslep, ikke nytt
SELECTION_TYPES = ("kjoper", "leverandor", "cpv", "sokeord")


@dataclass
class Subscriber:
    email: str
    token: str
    status: str = "active"
    plan: str = "standard"
    horizons: tuple[int, ...] = HORIZONS
    csv: bool = True
    last_sent: date | None = None         # referansedatoen (kjøringen) for forrige varsel
    last_sent_at: str | None = None       # når forrige varsel ble sendt (UTC, 'ÅÅÅÅ-MM-DD TT:MM:SS')
    buyers: set[str] = field(default_factory=set)
    suppliers: set[str] = field(default_factory=set)      # orgnr eller del av navnet (små bokstaver)
    cpv: set[str] = field(default_factory=set)            # prefikser, f.eks. '72', '4521'
    keywords: set[str] = field(default_factory=set)       # små bokstaver

    @property
    def receives(self) -> bool:
        return self.status in SENDING and self.has_selection

    @property
    def has_selection(self) -> bool:
        return bool(self.buyers or self.suppliers or self.cpv or self.keywords)


def cpv_prefix(raw: str) -> str | None:
    """'72000000-1' -> '72', '45210000' -> '4521', '722' -> '722'. For kort eller ugyldig -> None."""
    digits = re.sub(r"\D", "", raw.split("-")[0])[:8]
    if len(digits) == 8:
        digits = digits.rstrip("0").ljust(2, "0")  # '30000000' -> '3' -> '30'
    return digits if len(digits) >= 2 else None


def parse_subscriber(d: dict) -> Subscriber:
    sub = Subscriber(email=str(d["email"]).strip().lower(), token=str(d.get("token") or ""),
                     status=str(d.get("status") or "venter"), plan=str(d.get("plan") or "standard"),
                     csv=bool(int(d.get("csv", 1) or 0)))
    horizons = sorted({int(h) for h in re.findall(r"\d+", str(d.get("horisonter") or "")) if int(h) in HORIZONS})
    sub.horizons = tuple(horizons) or HORIZONS
    if d.get("sist_sendt"):
        sub.last_sent = date.fromisoformat(str(d["sist_sendt"])[:10])
        sub.last_sent_at = str(d.get("sist_sendt_tid") or d["sist_sendt"])[:19].replace("T", " ")
    for sel in d.get("utvalg") or []:
        kind, value = sel.get("type"), str(sel.get("verdi") or "").strip()
        if kind == "kjoper" and re.fullmatch(r"\d{9}", value.replace(" ", "")):
            sub.buyers.add(value.replace(" ", ""))
        elif kind == "leverandor" and len(value) >= 3:
            compact = value.replace(" ", "")
            sub.suppliers.add(compact if re.fullmatch(r"\d{9}", compact) else value.lower())
        elif kind == "cpv" and (p := cpv_prefix(value)):
            sub.cpv.add(p)
        elif kind == "sokeord" and len(value) >= 3:
            sub.keywords.add(value.lower())
    return sub


def load_subscribers(source: str, token: str | None = None) -> list[Subscriber]:
    """Abonnentene fra Workeren (http(s)-adresse, med token) eller en lokal JSON-fil."""
    if source.startswith(("http://", "https://")):
        import requests
        r = requests.get(source, headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=60)
        r.raise_for_status()
        rows = r.json()
    else:
        rows = json.loads(Path(source).read_text(encoding="utf-8"))
    return [parse_subscriber(d) for d in rows]


@dataclass
class Contract:
    id: str
    title: str
    url: str | None
    buyer: str
    buyers: tuple[str, ...]               # alle kjøpernes orgnr
    value: float | None
    currency: str | None
    value_type: str | None
    published: date | None
    is_framework: bool
    start: date | None
    end: date | None
    end_source: str | None                # nøkkel i EXPIRY_SOURCES, eller 'standard'
    suppliers: list[tuple[str, str | None]]
    cpv: tuple[str, ...]
    fetched_at: str = ""                  # når kontrakten ble lagt inn (UTC)


def _date(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw[:10]) if raw else None
    except ValueError:
        return None


def load_contracts(conn: sqlite3.Connection) -> dict[str, Contract]:
    """Alle kontraktene i databasen med kjøpere, leverandører, CPV og (anslått) sluttdato."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(doffin_contracts)")}
    buyers: dict[str, list[str]] = {}
    if "doffin_buyers" in tables:
        for cid, orgnr in conn.execute("SELECT contract_id, orgnr FROM doffin_buyers WHERE orgnr IS NOT NULL "
                                       "ORDER BY contract_id, position"):
            buyers.setdefault(cid, []).append(orgnr)
    winners: dict[str, list[tuple[str, str | None]]] = {}
    if "doffin_winners" in tables:
        for cid, name, orgnr in conn.execute("SELECT contract_id, name, orgnr FROM doffin_winners "
                                             "ORDER BY contract_id, position"):
            winners.setdefault(cid, []).append((name or "", orgnr or None))
    cpv = "cpv" if "cpv" in cols else "NULL"
    out = {}
    for (cid, org, name, title, url, value, currency, vtype, pub, fw, s, e, months, src, codes, fetched) in conn.execute(
            f"""SELECT id, buyer_orgnr, buyer_name, title, url, value, currency, value_type, publication_date,
                       is_framework, start_date, end_date, duration_months, duration_source, {cpv}, fetched_at
                FROM doffin_contracts"""):
        period = contract_period(pub, s, e, months, src, fw)
        out[cid] = Contract(
            id=cid, title=title or "(uten tittel)", url=url, buyer=display_name(name or ""),
            buyers=tuple(dict.fromkeys([o for o in [org, *buyers.get(cid, [])] if o])),
            value=value, currency=currency, value_type=vtype, published=_date(pub), is_framework=bool(fw),
            start=period[0] if period else None, end=period[1] if period else None,
            end_source=period[2] if period else None, suppliers=winners.get(cid, []),
            cpv=tuple((codes or "").split()), fetched_at=fetched or "")
    return out


def children(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Hovedenhet -> underenheter og organisasjonsledd (orgnr_parent)."""
    out: dict[str, set[str]] = {}
    try:
        for child, parent in conn.execute("SELECT child, parent FROM orgnr_parent"):
            out.setdefault(parent, set()).add(child)
    except sqlite3.OperationalError:
        pass
    return out


def ref_date(conn: sqlite3.Connection) -> date | None:
    """Referansedatoen for dataene: siste publiseringsdato i Doffin (som varsler.html)."""
    return _date(conn.execute("SELECT MAX(publication_date) FROM doffin_contracts").fetchone()[0])


class Matcher:
    """Avgjør om en kontrakt treffer utvalget til en abonnent."""

    def __init__(self, sub: Subscriber, units: dict[str, set[str]]) -> None:
        self.sub = sub
        self.buyers = set(sub.buyers).union(*(units.get(b, set()) for b in sub.buyers))
        self.supplier_orgnr = {s for s in sub.suppliers if s.isdigit()}
        self.supplier_names = {s for s in sub.suppliers if not s.isdigit()}

    def __call__(self, c: Contract) -> bool:
        sub = self.sub
        if self.buyers or sub.suppliers:
            hit = bool(self.buyers.intersection(c.buyers)) or any(
                (o and o in self.supplier_orgnr) or any(n in name.lower() for n in self.supplier_names)
                for name, o in c.suppliers)
            if not hit:
                return False
        if sub.cpv or sub.keywords:
            title = c.title.lower()
            return (any(code.startswith(p) for code in c.cpv for p in sub.cpv)
                    or any(k in title for k in sub.keywords))
        return True


def months_later(d: date, months: int) -> date:
    return d + timedelta(days=round(months * DAYS_PER_MONTH))


@dataclass
class Alert:
    """Ett varsel til én abonnent."""
    sub: Subscriber
    ref: date
    first: bool
    awards: list[Contract]
    expiring: dict[int, list[Contract]]   # horisont (måneder) -> avtaler, tidligst slutt først
    history: list[Contract]               # alle treff (CSV-uttrekket), nyeste først

    @property
    def n_expiring(self) -> int:
        return sum(len(v) for v in self.expiring.values())

    @property
    def empty(self) -> bool:
        return not self.awards and not self.n_expiring


def _by_value(cs: Iterable[Contract]) -> list[Contract]:
    return sorted(cs, key=lambda c: (-(c.value or 0), c.id))


def is_new(c: Contract, sub: Subscriber) -> bool:
    """Lagt inn etter forrige varsel. Kunngjøringer som er mye eldre enn forrige referansedato (etterslep
    fra en ny kjøper eller en omkjøring av eldre år), regnes ikke som nye."""
    return bool(sub.last_sent and sub.last_sent_at and c.fetched_at > sub.last_sent_at and c.published
                and c.published >= sub.last_sent - timedelta(days=BACKFILL_DAYS))


def build_alert(sub: Subscriber, contracts: dict[str, Contract], ref: date, units: dict[str, set[str]]) -> Alert:
    """Tildelingene og utløpene som skal med i varselet til `sub` for kjøringen med referansedato `ref`."""
    match = Matcher(sub, units)
    hits = [c for c in contracts.values() if match(c)]
    first = sub.last_sent is None
    if first:
        since = ref - timedelta(days=WELCOME_DAYS)
        awards = [c for c in hits if c.published and since < c.published <= ref]
    else:
        awards = [c for c in hits if is_new(c, sub)]
    expiring: dict[int, list[Contract]] = {}
    last = sub.last_sent or ref
    for c in hits:
        if not c.end or c.end_source not in EXPIRY_SOURCES or c.end < ref:
            continue
        for h in sub.horizons:  # stigende: korteste horisont som passer
            lo, hi = months_later(last, h), months_later(ref, h)
            if (ref <= c.end <= hi) if first else (lo < c.end <= hi):
                expiring.setdefault(h, []).append(c)
                break
    expiring = {h: sorted(v, key=lambda c: (c.end, -(c.value or 0), c.id)) for h, v in sorted(expiring.items())}
    history = sorted(hits, key=lambda c: (c.published or date.min, c.id), reverse=True)
    return Alert(sub=sub, ref=ref, first=first, awards=_by_value(awards), expiring=expiring, history=history)

