"""Prospektliste for varsler: leverandørene som har mest å vinne på å følge offentlige avtaler.

Rangerer leverandørene i doffin_winners etter
  - avtaler de har i dag som løper ut de neste 12 månedene (ny konkurranse kommer, de må følge med),
  - hvor mange offentlige kontrakter de har vunnet de siste tre årene (de byr jevnlig),
  - hvor mange ulike offentlige kjøpere de har vunnet hos.
Sluttdatoene er de samme som i varslene (src/alerts/engine.py): fra Doffin, eller anslått ut fra
oppgitt varighet. Avtaler uten noen opplysning om varighet teller ikke som «løper ut».

Kontaktinformasjon (e-post, telefon, nettside, daglig leder) hentes fra Enhetsregisteret
(data.brreg.no, ingen nøkkel). Enkeltpersonforetak, offentlige enheter, foretak under avvikling
og foretak med flere enn --max-ansatte ansatte tas ikke med: de store har egne anbudsavdelinger.

Lista inneholder kontaktinformasjon og skal ikke legges i det offentlige repoet. Skriv den til en
privat mappe.

Eksempel (fra repo-roten):
  python scripts/prospects.py --ut /mnt/project-files/kronesporet/salg/prospekter.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_site import sql_to_db  # noqa: E402
from src.alerts.engine import EXPIRY_SOURCES, Contract, load_contracts, months_later  # noqa: E402

log = logging.getLogger("prospekter")

BRREG = "https://data.brreg.no/enhetsregisteret/api/enheter"
SITE = "https://kronesporet.no"
SKIP_FORMS = {"ENK", "STAT", "ORGL", "KOMM", "FYLK", "IKS", "KF", "SF", "HF", "RHF", "FKF", "KIRK", "SÆR", "ADOS"}
PUBLIC_SECTORS = ("1", "3", "6")          # institusjonell sektorkode: stat, kommune, offentlige foretak


def norm(name: str) -> str:
    """'Atea AS (hovedenhet)' -> 'atea as'."""
    name = re.sub(r"\((hovedenhet|underenhet)\)", "", name.lower())
    return re.sub(r"[^a-z0-9æøå]+", " ", name).strip()


@dataclass
class Prospect:
    orgnr: str
    name: str
    wins_recent: int = 0
    wins_total: int = 0
    buyers: set[str] = field(default_factory=set)
    expiring: list[Contract] = field(default_factory=list)
    cpv: Counter = field(default_factory=Counter)        # CPV-grupper (3 sifre) i kontraktene de har vunnet

    @property
    def score(self) -> float:
        return 3 * len(self.expiring) + self.wins_recent + 0.5 * len(self.buyers)


def collect(contracts: dict[str, Contract], today: date, recent_from: int) -> dict[str, Prospect]:
    """Leverandørene med orgnr, med navn uten orgnr slått sammen når navnet ellers bare har ett orgnr."""
    by_name: dict[str, set[str]] = defaultdict(set)
    for c in contracts.values():
        for name, orgnr in c.suppliers:
            if orgnr:
                by_name[norm(name)].add(orgnr)
    horizon = months_later(today, 12)
    out: dict[str, Prospect] = {}
    for c in contracts.values():
        expiring = bool(c.end and c.end_source in EXPIRY_SOURCES and today <= c.end <= horizon)
        seen = set()
        for name, orgnr in c.suppliers:
            if not orgnr and len(by_name.get(norm(name), ())) == 1:
                orgnr = next(iter(by_name[norm(name)]))
            if not orgnr or orgnr in seen or not re.fullmatch(r"\d{9}", orgnr):
                continue
            seen.add(orgnr)
            p = out.setdefault(orgnr, Prospect(orgnr=orgnr, name=re.sub(r"\s*\(hovedenhet\)", "", name)))
            p.wins_total += 1
            if c.published and c.published.year >= recent_from:
                p.wins_recent += 1
            p.buyers.add(c.buyers[0] if c.buyers else c.buyer)
            if expiring:
                p.expiring.append(c)
            p.cpv.update({code[:3] for code in c.cpv if len(code) >= 3})
    return out


def expiring_contracts(contracts: dict[str, Contract], today: date) -> list[Contract]:
    horizon = months_later(today, 12)
    return sorted((c for c in contracts.values()
                   if c.end and c.end_source in EXPIRY_SOURCES and today <= c.end <= horizon), key=lambda c: c.end)


def market(p: Prospect, expiring: list[Contract], groups: int = 3) -> tuple[list[str], list[Contract]]:
    """CPV-gruppene leverandøren oftest vinner i, og avtalene i de gruppene som løper ut innen 12 måneder."""
    top = [code for code, _ in p.cpv.most_common(groups)]
    hits = [c for c in expiring if any(code.startswith(tuple(top)) for code in c.cpv)] if top else []
    return top, hits


def write_list(path: Path, p: Prospect, hits: list[Contract]) -> None:
    """Gratisliste til prospektet: avtalene i bransjen deres som løper ut, sortert på sluttdato."""
    own = {c.id for c in p.expiring}
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["slutter", "anslag_eller_dato", "kjoper", "tittel", "verdi_nok", "leverandor_na",
                    "deres_avtale", "doffin"])
        for c in hits:
            w.writerow([c.end.isoformat(), EXPIRY_SOURCES[c.end_source], c.buyer, c.title,
                        round(c.value) if c.value and (c.currency or "NOK") == "NOK" else "",
                        ", ".join(n for n, _ in c.suppliers), "ja" if c.id in own else "", c.url])


def brreg(session, orgnr: str) -> dict | None:
    """Enheten fra Enhetsregisteret, med daglig leder fra rollene. None når den ikke finnes."""
    for attempt in range(3):
        try:
            r = session.get(f"{BRREG}/{orgnr}", timeout=20)
            if r.status_code in (404, 410):
                return None
            r.raise_for_status()
            d = r.json()
            break
        except Exception as exc:  # noqa: BLE001 - nettverksfeil: prøv igjen
            log.warning("brreg %s: %s", orgnr, exc)
            time.sleep(2 * (attempt + 1))
    else:
        return None
    leader = ""
    try:
        roles = session.get(f"{BRREG}/{orgnr}/roller", timeout=20).json()
        for group in roles.get("rollegrupper", []):
            if group.get("type", {}).get("kode") == "DAGL":
                person = (group.get("roller") or [{}])[0].get("person", {}).get("navn", {})
                leader = " ".join(x for x in (person.get("fornavn"), person.get("mellomnavn"),
                                               person.get("etternavn")) if x)
    except Exception as exc:  # noqa: BLE001
        log.warning("roller %s: %s", orgnr, exc)
    d["_daglig_leder"] = leader
    return d


def eligible(d: dict | None, max_employees: int) -> bool:
    if not d or any(d.get(k) for k in ("slettedato", "konkurs", "underAvvikling",
                                       "underTvangsavviklingEllerTvangsopplosning")):
        return False
    if d.get("organisasjonsform", {}).get("kode") in SKIP_FORMS:
        return False
    if str(d.get("institusjonellSektorkode", {}).get("kode", "")).startswith(PUBLIC_SECTORS):
        return False
    return (d.get("antallAnsatte") or 0) <= max_employees


def row(p: Prospect, d: dict, rank: int, cpv: list[str], hits: list[Contract]) -> dict:
    nearest = min(p.expiring, key=lambda c: c.end) if p.expiring else None
    addr = d.get("forretningsadresse") or {}
    site = d.get("hjemmeside") or ""
    return {
        "rang": rank,
        "orgnr": p.orgnr,
        "navn": d.get("navn") or p.name,
        "avtaler_ut_12_mnd": len(p.expiring),
        "i_bransjen_ut_12_mnd": len(hits),
        "cpv_grupper": " ".join(cpv),
        "vunnet_siste_3_ar": p.wins_recent,
        "vunnet_totalt": p.wins_total,
        "ulike_kjopere": len(p.buyers),
        "ansatte": d.get("antallAnsatte") or "",
        "bransje": (d.get("naeringskode1") or {}).get("beskrivelse", ""),
        "sted": addr.get("poststed", ""),
        "epost": d.get("epostadresse") or "",
        "telefon": d.get("telefon") or "",
        "mobil": d.get("mobil") or "",
        "nettside": site,
        "daglig_leder": d.get("_daglig_leder", ""),
        "neste_avtale_ut": nearest.end.isoformat() if nearest else "",
        "neste_avtale_kjoper": nearest.buyer if nearest else "",
        "neste_avtale_tittel": nearest.title if nearest else "",
        "neste_avtale_lenke": nearest.url if nearest else "",
        "kronesporet_side": f"{SITE}/leverandor/{p.orgnr}.html",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sql", default=str(ROOT / "site-data" / "kronesporet.sql"))
    ap.add_argument("--ut", required=True, help="CSV-fil (privat mappe, ikke repoet)")
    ap.add_argument("--antall", type=int, default=100)
    ap.add_argument("--max-ansatte", type=int, default=1000)
    ap.add_argument("--lister", help="mappe for én gratisliste per prospekt (avtaler i bransjen som løper ut)")
    ap.add_argument("--dato", default=date.today().isoformat(), help="«i dag» for 12-månedersvinduet")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import requests

    today = date.fromisoformat(args.dato)
    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(sql_to_db(Path(args.sql), tmp))
        contracts = load_contracts(conn)
        conn.close()
    expiring = expiring_contracts(contracts, today)
    prospects = sorted(collect(contracts, today, today.year - 2).values(), key=lambda p: -p.score)
    log.info("%d leverandører med orgnr, %d med avtaler som løper ut innen 12 mnd",
             len(prospects), sum(1 for p in prospects if p.expiring))

    session = requests.Session()
    session.headers["User-Agent"] = "Kronesporet.no prospektliste"
    rows, skipped = [], Counter()
    for p in prospects:
        if len(rows) >= args.antall:
            break
        d = brreg(session, p.orgnr)
        time.sleep(0.15)
        if not eligible(d, args.max_ansatte):
            skipped["ikke aktuell"] += 1
            continue
        cpv, hits = market(p, expiring)
        rows.append(row(p, d, len(rows) + 1, cpv, hits))
        if args.lister and hits:
            Path(args.lister).mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", norm(rows[-1]["navn"])).strip("-")
            write_list(Path(args.lister) / f"{rows[-1]['rang']:03d}-{slug}.csv", p, hits)
    out = Path(args.ut)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig: æøå vises riktig i Excel
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    log.info("Skrev %d prospekter til %s (hoppet over %d). Med e-post: %d, med telefon: %d",
             len(rows), out, sum(skipped.values()), sum(1 for r in rows if r["epost"]),
             sum(1 for r in rows if r["telefon"] or r["mobil"]))


if __name__ == "__main__":
    main()
