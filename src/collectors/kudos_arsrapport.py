"""Hent kjøp av varer og tjenester per virksomhet fra årsrapportene i Kudos.

Alternativ kilde når statsregnskapet.dfo.no ikke svarer. Hver statlig
virksomhet har en standard «Oppstilling av artskontorapportering» i
årsregnskapet (kontantprinsippet, beløp i kroner):

    Lønn og personalutgifter                           2     5 124 524 322   4 696 908 086
    Andre utbetalinger til drift                       3    34 410 024 656  32 548 524 811
    Utbetaling til investering i bygninger, tomter ... 5       134 772 598     136 439 111

Kjøp = «Andre utbetalinger til drift» + «Utbetaling(er) til investering(er) ...».
Lønn, tilskudd og finansutgifter holdes utenfor.

Kudos-API (ingen nøkkel): https://kudos.dfo.no/api/v0/documents
PDF-tekst hentes med `pdftotext -layout` (poppler-utils), som ligger på de
fleste Linux-maskiner og i Git for Windows / `choco install poppler`.
"""
from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from src.analysis.join_and_flag import SCHEMA as ANALYSIS_SCHEMA

log = logging.getLogger(__name__)

KUDOS_API = "https://kudos.dfo.no/api/v0/documents"
SOURCE_PREFIX = "kudos:"
DRIFT_RE = re.compile(r"^andre utbetalinger til drift\b", re.I)
INVEST_RE = re.compile(r"^utbetalinge?r? til investering", re.I)
# Under dette er beløpet trolig oppgitt i 1000 kr uten at enheten ble funnet
MIN_PLAUSIBLE_DRIFT = 5_000_000
NUMBER_RE = re.compile(r"^-?\d{1,3}(?:[  ]\d{3})*$")


@dataclass
class Purchases:
    orgnr: str
    name: str
    year: int
    drift: float
    investering: float
    document_url: str

    @property
    def total(self) -> float:
        return self.drift + self.investering


def _amounts(line: str) -> list[float]:
    """Tallkolonnene på en linje i -layout-tekst (kolonner skilles av 2+ mellomrom)."""
    cells = [c.strip() for c in re.split(r"\s{2,}", line.strip())]
    return [float(c.replace(" ", "").replace(" ", "")) for c in cells[1:] if NUMBER_RE.match(c)]


def parse_artskontorapportering(text: str, year: int) -> tuple[float, float] | None:
    """Finn (andre utbetalinger til drift, investeringer) for `year` i årsrapport-tekst.

    Ankeret er linjen «Andre utbetalinger til drift» med tall, under overskriften
    «Driftsutgifter rapportert til bevilgningsregnskapet» (skiller den fra noter og
    f.eks. Svalbardregnskapet). Beløpsenhet («Tall i 1000 kroner», «hele tusen»)
    leses fra linjene over tabellen.
    """
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        nums = _amounts(ln)
        if not (DRIFT_RE.match(ln.strip()) and nums):
            continue
        above = "\n".join(lines[max(0, i - 40): i])
        if not re.search(r"driftsutgifter rapportert til bevilgningsregnskapet", above, re.I):
            continue
        scale = _unit_scale(above)
        if scale == 1.0 and _pick(nums, 0) < 50_000_000:
            # Små tall: enheten («Tall i 1000 kr») kan stå lenger opp, over bevilgningsoppstillingen
            scale = _unit_scale("\n".join(lines[max(0, i - 150): i]))
        col = _year_column(above, year)
        drift = _pick(nums, col) * scale

        invest = 0.0
        for nxt in lines[i + 1: i + 40]:
            label = nxt.strip()
            if re.match(r"innkrevingsvirksomhet|tilskuddsforvaltning", label, re.I):
                break
            if INVEST_RE.match(label) and _amounts(nxt):
                invest += _pick(_amounts(nxt), col) * scale
        return drift, invest
    return None


def _unit_scale(text: str) -> float:
    if re.search(r"hele tusen|tusen (kr|nok)|1 ?000 (kr|nok)|nok 1 ?000|i 1 ?000\b", text, re.I):
        return 1000.0
    if re.search(r"(tall|beløp) i (hele )?mill", text, re.I):
        return 1e6
    return 1.0


def _year_column(header: str, year: int) -> int:
    """0 hvis inneværende år står først (vanlig), 1 hvis fjoråret står først."""
    cur = [m.start() for m in re.finditer(rf"\b(?:{year}|31\.12\.{year % 100:02d})\b", header)]
    prev = [m.start() for m in re.finditer(rf"\b(?:{year - 1}|31\.12\.{(year - 1) % 100:02d})\b", header)]
    return 1 if cur and prev and prev[-1] < cur[-1] else 0


def _pick(nums: list[float], col: int) -> float:
    """Tallene for de to årene står sist på linjen; foran kan det stå et notenummer."""
    pair = nums[-2:] if len(nums) >= 2 else nums
    return pair[min(col, len(pair) - 1)]


def pdf_text(pdf: bytes) -> str:
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext mangler. Installer poppler-utils.")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "r.pdf"
        src.write_bytes(pdf)
        subprocess.run(["pdftotext", "-layout", str(src), str(src.with_suffix(".txt"))],
                       check=True, capture_output=True, timeout=300)
        return src.with_suffix(".txt").read_text(encoding="utf-8", errors="replace")


class KudosClient:
    def __init__(self, min_interval: float = 0.5) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Kronesporet/0.1 (+https://kronesporet.no)"
        self.min_interval = min_interval
        self._last = 0.0

    def get(self, url: str, **params: object) -> requests.Response:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            try:
                resp = self.session.get(url, params=params or None, timeout=120)
                self._last = time.monotonic()
                if resp.status_code < 500 and resp.status_code != 429:
                    resp.raise_for_status()
                    return resp
            except requests.ConnectionError as e:
                log.warning("Kudos: %s (forsøk %d)", e, attempt + 1)
            time.sleep(2 ** attempt)
        raise RuntimeError(f"Kudos svarer ikke: {url}")

    def annual_reports(self, orgnr: str, year: int) -> list[dict]:
        return self.get(KUDOS_API, type="Årsrapport", actor_org_number=orgnr,
                        concerned_year_from=year, concerned_year_to=year).json().get("data", [])


def fetch_purchases(orgnr: str, name: str, year: int, client: KudosClient) -> Purchases | None:
    """Last ned årsrapporten for (orgnr, år) og les artskontorapporteringen."""
    for doc in client.annual_reports(orgnr, year):
        # Søket treffer også underliggende virksomheters rapporter (f.eks. departementet
        # som eier av etatens rapport); bruk bare rapporter virksomheten selv eier.
        owner = next((o for o in doc.get("owners") or [] if str(o.get("org_number")) == orgnr), None)
        if doc.get("owners") and owner is None:
            continue
        # Flere virksomheter kan dele orgnr i Kudos (f.eks. Arbeidsretten under
        # Domstolene i Norge). Krev at tittelen nevner virksomhetens eget navn.
        if owner and not _title_matches(doc.get("title", ""), owner):
            log.warning("%s: hopper over «%s» (annen virksomhet med samme orgnr)", name, doc.get("title"))
            continue
        for f in doc.get("files", []):
            if f.get("mimetype") != "application/pdf":
                continue
            text = pdf_text(client.get(f["url"]).content)
            parsed = parse_artskontorapportering(text, year)
            if parsed and parsed[0] < MIN_PLAUSIBLE_DRIFT:
                log.warning("%s: %.0f kr i andre driftsutgifter virker for lavt (enhet?), hopper over",
                            name, parsed[0])
                continue
            if parsed:
                return Purchases(orgnr, name, year, parsed[0], parsed[1], f["url"])
    return None


def _title_matches(title: str, owner: dict) -> bool:
    """Eget navn, offisielt navn eller forkortelse, ikke registreringsaliaser."""
    names = [owner.get("name"), owner.get("entity_name")]
    names += [a.get("name") for a in owner.get("alternative_names") or []
              if a.get("type") in ("official", "display", "abbreviation")]
    title = title.lower()
    return any(n and re.sub(r"\s*\(.*?\)", "", n).strip().lower() in title for n in names)


def load_kudos_purchases(
    conn: sqlite3.Connection,
    year: int,
    top: int | None = None,
    client: KudosClient | None = None,
    refresh: bool = False,
) -> tuple[int, int]:
    """Hent kjøpstall for Doffin-kjøperne (sortert etter kontraktsverdi) i `year`.

    Kommuner, selskaper og helseforetak har ikke årsrapport i Kudos og hoppes over
    etter ett raskt API-kall. Kjøpere som allerede er lastet hentes ikke på nytt
    med mindre refresh=True.

    Lagres i `statsregnskap` (artskonto = 'drift' / 'investering', source_file = 'kudos:<url>').
    Returnerer (antall kjøpere forsøkt, antall med tall).
    """
    conn.executescript(ANALYSIS_SCHEMA)
    client = client or KudosClient()
    buyers = conn.execute(
        "SELECT buyer_orgnr, MAX(buyer_name), SUM(value) FROM doffin_contracts "
        "WHERE year = ? AND buyer_orgnr IS NOT NULL AND value IS NOT NULL "
        "GROUP BY buyer_orgnr ORDER BY 3 DESC" + (" LIMIT ?" if top else ""),
        (year, top) if top else (year,),
    ).fetchall()

    done = {o for (o,) in conn.execute(
        "SELECT DISTINCT orgnr FROM statsregnskap WHERE year = ? AND source_file LIKE ?",
        (year, SOURCE_PREFIX + "%"))}
    found = 0
    for orgnr, name, _ in buyers:
        if orgnr in done and not refresh:
            found += 1
            continue
        try:
            p = fetch_purchases(orgnr, name, year, client)
        except (RuntimeError, requests.RequestException, subprocess.SubprocessError) as e:
            log.warning("%s (%s): %s", name, orgnr, e)
            continue
        if p is None:
            log.info("Ingen årsrapport/artskontorapportering for %s (%s)", name, orgnr)
            continue
        conn.execute("DELETE FROM statsregnskap WHERE orgnr = ? AND year = ? AND source_file LIKE ?",
                     (orgnr, year, SOURCE_PREFIX + "%"))
        conn.executemany(
            "INSERT INTO statsregnskap (year, orgnr, virksomhet, artskonto, amount, source_file) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(year, orgnr, name, "drift", p.drift, SOURCE_PREFIX + p.document_url),
             (year, orgnr, name, "investering", p.investering, SOURCE_PREFIX + p.document_url)],
        )
        conn.commit()
        found += 1
        log.info("%s: kjøp %.0f mill. kr (drift %.0f + inv. %.0f)", name, p.total / 1e6,
                 p.drift / 1e6, p.investering / 1e6)
    return len(buyers), found
