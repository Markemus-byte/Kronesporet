"""Hent kjøp av varer og tjenester per virksomhet fra årsrapportene i Kudos.

Alternativ kilde når statsregnskapet.dfo.no ikke svarer. Hver statlig
virksomhet har en standard «Oppstilling av artskontorapportering» i
årsregnskapet (kontantprinsippet, beløp i kroner):

    Lønn og personalutgifter                           2     5 124 524 322   4 696 908 086
    Andre utbetalinger til drift                       3    34 410 024 656  32 548 524 811
    Utbetaling til investering i bygninger, tomter ... 5       134 772 598     136 439 111

Kjøp = «Andre utbetalinger til drift» + «Utbetaling(er) til investering(er) ...».
Lønn, tilskudd og finansutgifter holdes utenfor.

Universiteter og andre nettobudsjetterte virksomheter fører regnskap etter SRS og
har ikke denne oppstillingen; der brukes kontantstrømmens «utbetalinger for kjøp av
varer og tjenester» (KS.12) + investeringer (KS.17), se parse_srs().

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
# Linjen kan stå i høyre spalte på en side med to spalter, så den søkes hvor som helst i linjen
DRIFT_RE = re.compile(r"(?<!sum )andre utbetalinger til drift\b", re.I)
INVEST_RE = re.compile(r"^utbetal(?:inge?r?|t) til investering", re.I)


def _loose(phrase: str) -> str:
    """Regex som tåler at PDF-en har delt ord («bevilgningsregnsk apet») og linjeskift."""
    return r"\s*".join(re.escape(ch) for ch in phrase.replace(" ", ""))


# Overskrifter som viser at vi er i selve artskontorapporteringen (ikke noter eller Svalbard)
ANCHOR_RE = re.compile(_loose("rapportert til bevilgningsregnskapet") + "|"
                       + _loose("oppstilling av artskontorapportering"), re.I)
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


def _line_amounts(lines: list[str], i: int, start: int = 0) -> list[float]:
    """Tallene etter etiketten på linje i (fra posisjon start).

    Smale kolonner kan bryte et tall over to linjer («1 065 611» over «037»);
    står etiketten alene, settes tallene sammen fra linjen over og under.
    """
    nums = _amounts("x  " + lines[i][start:])
    only_note = len(nums) == 1 and nums[0] < 100  # «Andre utbetalinger til drift   3»
    if nums and not only_note:
        if len(nums) > 2 and nums[0] < 100:
            nums = nums[1:]  # notenummer foran beløpene
        # Beløpene for de to årene står først; tall lenger ut hører til en spalte til høyre
        return nums[:2]
    if not (0 < i < len(lines) - 1):
        return nums
    above = [c for c in re.split(r"\s{2,}", lines[i - 1].strip()) if c]
    below = [c for c in re.split(r"\s{2,}", lines[i + 1].strip()) if c]
    if above and len(above) == len(below) and all(NUMBER_RE.match(c) for c in above + below) \
            and all(re.fullmatch(r"\d{1,3}", c) for c in below):
        return [float((a + b).replace(" ", "").replace(" ", "")) for a, b in zip(above, below)]
    return nums


def parse_artskontorapportering(text: str, year: int) -> tuple[float, float] | None:
    """Finn (andre utbetalinger til drift, investeringer) for `year` i årsrapport-tekst.

    Ankeret er linjen «Andre utbetalinger til drift» med tall, med «... rapportert til
    bevilgningsregnskapet» eller «Oppstilling av artskontorapporteringen» over seg
    (skiller den fra noter og f.eks. Svalbardregnskapet). Beløpsenhet («Tall i 1000
    kroner», «hele tusen») leses fra linjene over tabellen.
    """
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = DRIFT_RE.search(ln)
        if not m:
            continue
        nums = _line_amounts(lines, i, m.end())
        if not nums:
            continue
        above = "\n".join(lines[max(0, i - 40): i])
        if not ANCHOR_RE.search(above):
            continue
        scale = _unit_scale(above)
        if scale == 1.0 and _pick(nums, 0) < 50_000_000:
            # Små tall: enheten («Tall i 1000 kr») kan stå lenger opp, over bevilgningsoppstillingen
            scale = _unit_scale("\n".join(lines[max(0, i - 150): i]))
        if scale == 1.0 and _pick(nums, 0) * 1000 >= MIN_PLAUSIBLE_DRIFT > _pick(nums, 0) \
                and re.search(r"\bAR\.0\d\d\b", ln):
            # DBH-oppsett med referansekoder (AR.025) er alltid i 1000 kr
            scale = 1000.0
        col = _year_column(above, year)
        drift = _pick(nums, col) * scale

        invest = 0.0
        for j in range(i + 1, min(i + 40, len(lines))):
            label = lines[j].strip()
            if re.match(r"innkrevingsvirksomhet|tilskuddsforvaltning", label, re.I):
                break
            if INVEST_RE.match(label):
                inv = _line_amounts(lines, j, lines[j].lower().index("investering"))
                if inv:
                    invest += _pick(inv, col) * scale
        return drift, invest
    return None


# Statlige regnskapsstandarder (SRS): universiteter og andre nettobudsjetterte
# virksomheter har ikke artskontorapportering. Mange bruker faste linjekoder i
# kontantstrømoppstillingen (KS) og resultatregnskapet (RE); ellers leses linjene
# «Andre driftskostnader» og «utbetalinger ved kjøp av ... varige driftsmidler».
_SRS_LINE = re.compile(r"\s(KS\.12|KS\.17|RE\.10)\s*$")
_SRS_DRIFT_RE = re.compile(r"^\s*andre driftskostnader\s{2,}", re.I)
_SRS_INVEST_RE = re.compile(r"kjøp av (?:immaterielle eiendeler og )?varige(?: driftsmidler)?", re.I)
_SRS_NUMBER_RE = re.compile(r"^\(?-? ?\d{1,3}(?:[  ]\d{3})*\)?$")


def _srs_value(text: str) -> float | None:
    """Første beløp i teksten (inneværende år); notenumre (1-2 sifre, S3A) hoppes over.

    Negative tall kan stå som «-294 333», «- 16 333» eller «(495 574)»; beløpet returneres positivt.
    """
    cells = [c.strip() for c in re.split(r"\s{2,}", _SRS_LINE.sub("", text).strip())]
    for c in cells:
        if _SRS_NUMBER_RE.match(c) and not re.fullmatch(r"\d{1,2}", c):
            return abs(float(re.sub(r"[^\d]", "", c)))
    return None


def parse_srs(text: str, year: int) -> tuple[float, float] | None:
    """(kjøp av varer og tjenester, investeringer) fra et SRS-regnskap.

    Drift: «utbetalinger for kjøp av varer og tjenester» (KS.12), ellers «Andre
    driftskostnader» (RE.10 / resultatregnskapet). Investering: «utbetalinger ved
    kjøp av immaterielle eiendeler og varige driftsmidler» (KS.17). Første
    tallkolonne antas å være `year`.
    """
    lines = text.splitlines()
    found: dict[str, tuple[float, int]] = {}

    def remember(key: str, value: float | None, i: int) -> None:
        if value and key not in found:
            found[key] = (value, i)

    for i, ln in enumerate(lines):
        m = _SRS_LINE.search(ln)
        if m:
            label_end = len(re.split(r"\s{2,}", ln.strip())[0])
            value = _srs_value(ln.strip()[label_end:])
            if value is None and m.group(1) == "KS.17" and i > 0:
                value = _srs_value(lines[i - 1].strip()[len(re.split(r"\s{2,}", lines[i - 1].strip())[0]):])
            remember(m.group(1), value, i)
        elif _SRS_DRIFT_RE.match(ln):
            remember("drift", _srs_value(_SRS_DRIFT_RE.sub("", ln)), i)
        elif _SRS_INVEST_RE.search(ln) and re.match(r"^\s*utbetaling", ln, re.I):
            rest = ln[_SRS_INVEST_RE.search(ln).end():]
            value = _srs_value(re.sub(r"^[^\d(-]*", "", rest))
            if value is None and i + 1 < len(lines):
                value = _srs_value(lines[i + 1].strip())  # etiketten brytes over to linjer
            remember("invest", value, i)

    drift = found.get("KS.12") or found.get("RE.10") or found.get("drift")
    if not drift:
        return None
    invest = (found.get("KS.17") or found.get("invest") or (0.0, 0))[0]
    scale = _unit_scale("\n".join(lines[max(0, drift[1] - 150): drift[1]]))
    if scale == 1.0 and drift[0] < MIN_PLAUSIBLE_DRIFT <= drift[0] * 1000 \
            and re.search(r"(?:oppgitt|beløp) i 1 ?000 kr", text, re.I):
        scale = 1000.0
    return drift[0] * scale, invest * scale


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
            parsed = parse_artskontorapportering(text, year) or parse_srs(text, year)
            if parsed and parsed[0] < MIN_PLAUSIBLE_DRIFT:
                log.warning("%s: %.0f kr i andre driftsutgifter virker for lavt (enhet?), hopper over",
                            name, parsed[0])
                continue
            if parsed:
                return Purchases(orgnr, name, year, parsed[0], parsed[1], f["url"])
    return None


def _norm_name(text: str) -> str:
    text = re.sub(r"\s*\(.*?\)", "", text.lower())
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-")).strip()


def _title_matches(title: str, owner: dict) -> bool:
    """Eget navn, offisielt navn eller forkortelse, ikke registreringsaliaser.

    Godtar også at de fleste ordene i navnet står i tittelen
    («NORGES TEKNISK-NATURVITENSKAPELIGE UNIVERSITET NTNU» mot «Årsrapport Norges
    teknisk-naturvitenskapelige universitet 2025»), og navn med skråstrek
    («Samediggi / Sametinget») deles opp.
    """
    names = [owner.get("name"), owner.get("entity_name")]
    names += [a.get("name") for a in owner.get("alternative_names") or []
              if a.get("type") in ("official", "display", "abbreviation")]
    title = _norm_name(title)
    title_words = set(re.findall(r"[\wæøå-]+", title))
    for raw in filter(None, names):
        for name in (_norm_name(n) for n in raw.split("/")):
            if not name:
                continue
            if name in title:
                return True
            words = [w for w in re.findall(r"[\wæøå-]+", name) if len(w) > 2]
            if len(words) >= 2 and sum(w in title_words for w in words) / len(words) >= 0.6:
                return True
    return False


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
