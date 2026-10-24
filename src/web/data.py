"""Leser analyseresultatet fra SQLite til enkle objekter som nettsiden bygges fra.

Kilden er databasen som `scripts/run_phase1.py` skriver: tabellene
`contract_spend_comparison`, `statsregnskap`, `doffin_contracts` og (når
kontraktverdien er årsfordelt) `doffin_contract_years`.

Helseforetakene (tabellene `foretak` og `foretak_regnskap`, src/collectors/foretak.py) er ikke
i statsregnskapet. De lastes som egne Agency-objekter (section 'helseforetak') i Dataset.health,
med driftskostnader utenom lønn fra årsregnskapet i stedet for kjøp fra statsregnskapet.

Finnes tabellen `doffin_coverage` (src/analysis/coverage.py), tas
Doffin-dekningsgraden og dekningsflagget med, og finnes `doffin_winners`
(scripts/fetch_winners.py), får kontraktene leverandører; ellers bygges
siden uten dem.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class Supplier:
    name: str
    orgnr: str | None


@dataclass
class Contract:
    id: str
    title: str
    url: str | None
    value: float | None          # hele kontraktsverdien slik den står i Doffin
    year_value: float | None     # andelen som faller i regnskapsåret (årsfordelt)
    share: float | None          # 0-1, andel av perioden i regnskapsåret
    published: str | None        # publiseringsdato for tildelingen
    start_date: str | None
    end_date: str | None
    duration_months: float | None
    period_source: str | None    # 'datoer' | 'varighet' | 'tekst' | 'standard'
    first_year: int | None       # første og siste år kontrakten løper (årsfordelt)
    last_year: int | None
    is_framework: bool
    value_type: str | None       # 'awarded' | 'estimated'
    suppliers: list[Supplier] = field(default_factory=list)  # vinnerne, når doffin_winners finnes
    buyer_unit: str | None = None  # underenheten som står som kjøper i Doffin (f.eks. et politidistrikt)
    lead_buyer: str | None = None  # første kjøper, for felles anskaffelser der virksomheten ikke står først
    n_buyers: int = 1              # antall kjøpere i kunngjøringen


@dataclass
class Coverage:
    ratio: float                 # Doffin-årsandel / kjøp
    peer_group: str              # størrelsesgruppe etter kjøp
    peer_count: int
    peer_median: float
    z: float                     # robust z-verdi på log10(dekning)
    flagged: bool                # vedvarende avvik (flere av de siste årene)
    outlier: bool = False        # avvik dette året
    years_outlier: int = 0       # år med avvik blant de siste tre
    category: str = ""           # 'flagg' | 'observasjon' | 'tilskudd' | 'avtaler_for_andre' | ''


@dataclass
class Agency:
    orgnr: str
    name: str
    year: int
    contract_sum: float
    contract_count: int
    spend_sum: float
    deviation: float             # (contract_sum - spend_sum) / spend_sum
    flagged: bool
    match_method: str | None
    spend_parts: dict[str, float] = field(default_factory=dict)  # artskonto -> beløp
    report_url: str | None = None
    spend_source: str | None = None    # filnavn når kjøpene kommer fra statsregnskapet, ikke Kudos
    awarded_in_year_sum: float = 0.0   # hele verdien av kontrakter tildelt i året
    awarded_in_year_count: int = 0
    coverage: Coverage | None = None   # fylles når doffin_coverage finnes
    contracts: list[Contract] = field(default_factory=list)
    joint: list[Contract] = field(default_factory=list)  # felles anskaffelser der virksomheten ikke står først
    section: str = "virksomhet"        # mappen siden ligger i: 'virksomhet' | 'helseforetak'
    owner: str = ""                    # eieren, for helseforetakene (RHF-et eller departementet)

    @property
    def slug(self) -> str:
        return self.orgnr or re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")

    @property
    def path(self) -> str:
        return f"{self.section}/{self.slug}.html"

    @property
    def direction(self) -> str:
        return "over" if self.deviation > 0 else "under"


@dataclass
class Unmatched:
    """Virksomhet med kjøp i regnskapet, men uten Doffin-kontrakter i året (eget orgnr eller underenheter)."""
    orgnr: str
    name: str
    spend_sum: float
    notices: int = 0                 # kunngjøringer i Doffin alle år, som første kjøper
    joint: int = 0                   # felles anskaffelser der virksomheten er med, men ikke står først
    last_notice: int | None = None   # siste tildelingsår
    note: str = ""


# Kjente forklaringer, vist ved siden av virksomheten når den ikke har kontrakter i Doffin.
UNMATCHED_NOTES = {
    "980977269": "Statens eierandeler i olje- og gassfeltene. Kjøpene gjøres av operatørene på feltene.",
    "921455313": "Regnskapet for skatte- og avgiftsinntektene, ikke Skatteetatens egne kjøp.",
}
STATSFORVALTER_NOTE = "Statsforvalterens fellestjenester (STAF) gjør mange av innkjøpene for embetene."
UNMATCHED_MIN_SPEND = 1e6


@dataclass
class Dataset:
    year: int
    annualized: bool
    threshold: float
    agencies: list[Agency]
    has_coverage: bool = False
    has_suppliers: bool = False
    data_date: str = ""          # siste henting fra Doffin (DD.MM.ÅÅÅÅ), fra doffin_contracts.fetched_at
    through_month: int = 12      # siste måned regnskapstallene dekker; under 12 betyr delår (f.eks. jan–aug)
    unmatched: list[Unmatched] = field(default_factory=list)  # med regnskap, uten kontrakter i året
    health: list[Agency] = field(default_factory=list)        # helseforetakene, utenfor statsregnskapet

    @property
    def partial(self) -> bool:
        return self.through_month < 12


MONTHS = ("januar", "februar", "mars", "april", "mai", "juni", "juli", "august",
          "september", "oktober", "november", "desember")


def source_month(source_file: str | None) -> int | None:
    """Måneden i et rådatafilnavn fra statsregnskapet: 'raadata_august_2026_….xlsx' -> 8."""
    m = re.search(r"(?i)r(?:aa|å)data[ _-]+([a-zæøå]+)[ _-]+\d{4}", source_file or "")
    if m and m.group(1).lower() in MONTHS:
        return MONTHS.index(m.group(1).lower()) + 1
    return None


def display_name(name: str) -> str:
    """'STATENS JERNBANETILSYN' -> 'Statens jernbanetilsyn', men behold forkortelser i parentes."""
    if not name.isupper():
        return name
    text = name.lower()
    text = text[:1].upper() + text[1:]
    return re.sub(r"\(([^)]*)\)", lambda m: f"({m.group(1).upper()})", text)


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return row is not None


def _report_url(source_file: str | None) -> str | None:
    if source_file and source_file.startswith("kudos:"):
        return source_file.removeprefix("kudos:")
    if source_file and source_file.startswith("http"):
        return source_file
    return None


def _state(conn: sqlite3.Connection) -> str:
    """SELECT med orgnr for alle statlige kjøpere: virksomhetene i regnskapet, helseforetakene og underenhetene."""
    sql = "SELECT orgnr FROM statsregnskap"
    if _has_table(conn, "foretak"):
        sql += " UNION SELECT orgnr FROM foretak"
    if _has_table(conn, "orgnr_parent"):
        sql += " UNION SELECT child FROM orgnr_parent"
    return sql


def available_years(db_path: str | Path) -> list[int]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT year FROM contract_spend_comparison WHERE year IS NOT NULL ORDER BY year")]
    finally:
        conn.close()


@dataclass
class Breadth:
    """Hvor mye som er hentet inn, på tvers av alle år: tallene forsiden viser om datagrunnlaget."""
    contracts: int = 0                                         # Doffin-kontrakter (kunngjøringer med tildeling)
    contracts_by_year: dict[int, int] = field(default_factory=dict)  # etter tildelingsår
    buyers: int = 0                                            # statlige kjøpere (orgnr) i Doffin
    suppliers: int = 0                                         # leverandører med orgnr blant vinnerne
    winners: int = 0                                           # vinnerposisjoner (en kontrakt kan ha flere)
    units: int = 0                                             # enheter og underenheter fra Enhetsregisteret
    subunits: int = 0
    ledger_rows: int = 0                                       # linjer fra statsregnskapet (virksomhet × artskonto × år)
    ledger_accounts: int = 0                                   # ulike artskontoer
    ledger_years: list[int] = field(default_factory=list)
    report_rows: int = 0                                       # tall hentet fra årsrapporter i Kudos
    agencies: int = 0                                          # virksomheter med både regnskap og Doffin
    joint: int = 0                                             # kunngjøringer med flere statlige kjøpere
    ledger_agencies: int = 0                                   # virksomheter i regnskapet
    health: int = 0                                            # helseforetak med årsregnskap
    health_years: list[int] = field(default_factory=list)      # år med årsregnskap fra Regnskapsregisteret

    @property
    def contract_years(self) -> list[int]:
        return sorted(self.contracts_by_year)


@dataclass
class Expiring:
    """En avtale hos en statlig kjøper der grunnperioden slutter snart (fra datoer eller varighet i Doffin)."""
    id: str
    title: str
    url: str | None
    buyer: str
    value: float | None
    end: str                     # ÅÅÅÅ-MM-DD
    source: str                  # 'datoer' | 'varighet'
    is_framework: bool


def load_expiring(db_path: str | Path, months: int = 12) -> tuple[str, list[Expiring]]:
    """(referansedato, avtaler som slutter innen `months` måneder etter den), største verdi først.

    Referansedatoen er siste publiseringsdato i Doffin-dataene, så bygget gir samme resultat for
    samme data. Bare kontrakter med sluttdato eller oppgitt varighet tas med; anslag fra fritekst
    eller standardvarighet er for usikre til å si når en avtale løper ut.
    """
    from datetime import date, timedelta
    from src.analysis.annualize import contract_period

    conn = sqlite3.connect(db_path)
    try:
        if not _has_table(conn, "doffin_contracts"):
            return "", []
        ref = conn.execute("SELECT MAX(substr(publication_date, 1, 10)) FROM doffin_contracts").fetchone()[0]
        if not ref:
            return "", []
        today = date.fromisoformat(ref)
        horizon = today + timedelta(days=round(months * 365.25 / 12))
        state = _state(conn)
        out = []
        for r in conn.execute(
                f"""SELECT id, title, url, buyer_name, value, publication_date, start_date, end_date,
                           duration_months, duration_source, is_framework
                    FROM doffin_contracts WHERE buyer_orgnr IN ({state})
                      AND duration_source IN ('datoer', 'varighet')"""):
            period = contract_period(r[5], r[6], r[7], r[8], r[9], r[10])
            if period and period[2] in ("datoer", "varighet") and today <= period[1] <= horizon:
                out.append(Expiring(id=r[0], title=r[1] or "(uten tittel)", url=r[2],
                                    buyer=display_name(r[3] or ""), value=r[4], end=period[1].isoformat(),
                                    source=period[2], is_framework=bool(r[10])))
        return ref, sorted(out, key=lambda e: (-(e.value or 0), e.end, e.id))
    finally:
        conn.close()


def load_breadth(db_path: str | Path) -> Breadth:
    conn = sqlite3.connect(db_path)
    try:
        b = Breadth()
        one = lambda sql: conn.execute(sql).fetchone()[0] or 0  # noqa: E731
        if _has_table(conn, "doffin_contracts"):
            b.contracts_by_year = {y: n for y, n in conn.execute(
                "SELECT year, COUNT(*) FROM doffin_contracts WHERE year IS NOT NULL GROUP BY year")}
            b.contracts = sum(b.contracts_by_year.values())
            state = _state(conn)
            b.buyers = one(f"SELECT COUNT(DISTINCT buyer_orgnr) FROM doffin_contracts WHERE buyer_orgnr IN ({state})")
            if _has_table(conn, "doffin_buyers"):
                b.joint = one(f"SELECT COUNT(*) FROM (SELECT contract_id FROM doffin_buyers WHERE orgnr IN ({state}) "
                              "GROUP BY contract_id HAVING COUNT(DISTINCT orgnr) > 1)")
        if _has_table(conn, "doffin_winners"):
            b.winners = one("SELECT COUNT(*) FROM doffin_winners")
            b.suppliers = one("SELECT COUNT(DISTINCT orgnr) FROM doffin_winners WHERE orgnr IS NOT NULL AND orgnr != ''")
        if _has_table(conn, "orgnr_parent"):
            b.units = one("SELECT COUNT(*) FROM orgnr_parent")
            b.subunits = one("SELECT COUNT(*) FROM orgnr_parent WHERE kind = 'underenhet'")
        if _has_table(conn, "statsregnskap"):
            ledger = "source_file IS NULL OR source_file NOT LIKE 'kudos:%'"
            b.ledger_rows = one(f"SELECT COUNT(*) FROM statsregnskap WHERE {ledger}")
            b.ledger_accounts = one(f"SELECT COUNT(DISTINCT artskonto) FROM statsregnskap WHERE {ledger}")
            b.ledger_years = [r[0] for r in conn.execute(
                f"SELECT DISTINCT year FROM statsregnskap WHERE {ledger} ORDER BY year")]
            b.report_rows = one("SELECT COUNT(*) FROM statsregnskap WHERE source_file LIKE 'kudos:%'")
            b.ledger_agencies = one("SELECT COUNT(DISTINCT orgnr) FROM statsregnskap WHERE orgnr IS NOT NULL")
        if _has_table(conn, "foretak_regnskap"):
            b.health = one("SELECT COUNT(DISTINCT orgnr) FROM foretak_regnskap")
            b.health_years = [r[0] for r in conn.execute("SELECT DISTINCT year FROM foretak_regnskap ORDER BY year")]
        b.agencies = one("SELECT COUNT(DISTINCT orgnr) FROM contract_spend_comparison")
        return b
    finally:
        conn.close()


def load_dataset(db_path: str | Path, year: int | None = None, threshold: float = 0.05) -> Dataset:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if year is None:
            year = conn.execute("SELECT MAX(year) FROM contract_spend_comparison").fetchone()[0]
            if year is None:
                raise SystemExit(f"{db_path}: contract_spend_comparison er tom. Kjør scripts/run_phase1.py først.")

        annualized = _has_table(conn, "doffin_contract_years") and conn.execute(
            "SELECT 1 FROM doffin_contract_years WHERE year = ? LIMIT 1", (year,)).fetchone() is not None

        agencies: list[Agency] = []
        for r in conn.execute(
            f"""SELECT orgnr, virksomhet, year, contract_sum, contract_count, spend_sum,
                       deviation, flagged, match_method
                FROM contract_spend_comparison WHERE year = ?""", (year,)):
            a = Agency(
                orgnr=r["orgnr"] or "", name=display_name(r["virksomhet"] or r["orgnr"]), year=r["year"],
                contract_sum=r["contract_sum"] or 0.0, contract_count=r["contract_count"] or 0,
                spend_sum=r["spend_sum"] or 0.0, deviation=r["deviation"] or 0.0,
                flagged=bool(r["flagged"]), match_method=r["match_method"],
            )
            agencies.append(a)

        has_coverage = _has_table(conn, "doffin_coverage")
        cov_cols = {r[1] for r in conn.execute("PRAGMA table_info(doffin_coverage)")} if has_coverage else set()
        extra = ", ".join(c if c in cov_cols else f"NULL AS {c}" for c in ("outlier", "years_outlier", "category"))
        has_parents = _has_table(conn, "orgnr_parent")
        has_buyers = _has_table(conn, "doffin_buyers")
        for a in agencies:
            if has_coverage:
                c = conn.execute(
                    f"SELECT coverage, peer_group, peer_count, peer_median, z, flagged, {extra} "
                    "FROM doffin_coverage WHERE orgnr = ? AND year = ?", (a.orgnr, year)).fetchone()
                if c is not None and c["coverage"] is not None:
                    a.coverage = Coverage(ratio=c["coverage"], peer_group=c["peer_group"] or "",
                                          peer_count=c["peer_count"] or 0, peer_median=c["peer_median"] or 0.0,
                                          z=c["z"] or 0.0, flagged=bool(c["flagged"]),
                                          outlier=bool(c["outlier"] if c["outlier"] is not None else c["flagged"]),
                                          years_outlier=c["years_outlier"] or 0, category=c["category"] or "")
            for s in conn.execute(
                "SELECT artskonto, SUM(amount) AS amount, MAX(source_file) AS source_file "
                "FROM statsregnskap WHERE orgnr = ? AND year = ? GROUP BY artskonto", (a.orgnr, year)):
                key = s["artskonto"] or "sum"
                if len(key) == 3 and key.isdigit():   # rådata har tresifret artskonto; vis gruppen
                    key = key[:2]
                a.spend_parts[key] = a.spend_parts.get(key, 0.0) + (s["amount"] or 0.0)
                a.report_url = a.report_url or _report_url(s["source_file"])
                if s["source_file"] and not _report_url(s["source_file"]):
                    a.spend_source = a.spend_source or s["source_file"]
            if a.spend_source:
                # Statsregnskapet går foran årsrapporten; Kudos-radene beholdes bare for lenken.
                for key in ("drift", "investering"):
                    a.spend_parts.pop(key, None)

            # Underenheter (orgnr_parent) kunngjør på vegne av virksomheten.
            units = ("buyer_orgnr = ? OR buyer_orgnr IN (SELECT child FROM orgnr_parent WHERE parent = ?)"
                     if has_parents else "buyer_orgnr = ? OR buyer_orgnr = ?")
            awarded = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(value), 0) FROM doffin_contracts WHERE ({units}) AND year = ?",
                (a.orgnr, a.orgnr, year)).fetchone()
            a.awarded_in_year_count, a.awarded_in_year_sum = awarded[0], awarded[1]
            a.contracts = _contracts(conn, a.orgnr, year, annualized)
            if has_buyers:
                a.joint = _joint(conn, a.orgnr, has_parents)

        agencies.sort(key=lambda a: a.name.lower())
        health = _health(conn, year, annualized, has_parents, has_buyers) if _has_table(conn, "foretak") else []
        has_suppliers = False
        if _has_table(conn, "doffin_winners"):
            by_contract: dict[str, list[Supplier]] = {}
            for w in conn.execute("SELECT contract_id, name, orgnr FROM doffin_winners ORDER BY contract_id, position"):
                by_contract.setdefault(w["contract_id"], []).append(Supplier(name=w["name"], orgnr=w["orgnr"]))
            for a in agencies + health:
                for c in a.contracts:
                    c.suppliers = by_contract.get(c.id, [])
                    has_suppliers = has_suppliers or bool(c.suppliers)
        unmatched = _unmatched(conn, year, {a.orgnr for a in agencies}, has_parents, has_buyers)
        fetched = conn.execute("SELECT MAX(fetched_at) FROM doffin_contracts").fetchone()[0]
        data_date = f"{fetched[8:10]}.{fetched[5:7]}.{fetched[:4]}" if fetched and len(fetched) >= 10 else ""
        months = [m for a in agencies if (m := source_month(a.spend_source))]
        return Dataset(year=year, annualized=annualized, threshold=threshold, agencies=agencies,
                       has_coverage=any(a.coverage for a in agencies), has_suppliers=has_suppliers,
                       data_date=data_date, through_month=max(months, default=12), unmatched=unmatched,
                       health=health)
    finally:
        conn.close()


HEALTH_SOURCE = "regnskapsregisteret"


def _health(conn: sqlite3.Connection, year: int, annualized: bool, has_parents: bool,
            has_buyers: bool) -> list[Agency]:
    """Helseforetakene for året: Doffin-kontraktene som for virksomhetene, og driftskostnader utenom lønn
    fra årsregnskapet som «kjøp» (0 når regnskapet for året ikke er levert ennå). Ingen flagg."""
    out = []
    for orgnr, name, owner in conn.execute("SELECT orgnr, name, owner FROM foretak").fetchall():
        acc = conn.execute("SELECT operating_costs, payroll FROM foretak_regnskap WHERE orgnr = ? AND year = ?",
                           (orgnr, year)).fetchone()
        costs = acc[0] - (acc[1] or 0.0) if acc and acc[0] is not None else 0.0
        contracts = _contracts(conn, orgnr, year, annualized)
        a = Agency(orgnr=orgnr, name=name, year=year, contract_sum=sum(c.year_value or 0.0 for c in contracts),
                   contract_count=len(contracts), spend_sum=costs, deviation=0.0, flagged=False, match_method=None,
                   spend_source=HEALTH_SOURCE if acc else None, contracts=contracts, section="helseforetak",
                   owner=owner or "")
        awarded = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(value), 0) FROM doffin_contracts WHERE {_units(has_parents)} AND year = ?",
            (orgnr, orgnr, year)).fetchone()
        a.awarded_in_year_count, a.awarded_in_year_sum = awarded[0], awarded[1]
        if has_buyers:
            a.joint = _joint(conn, orgnr, has_parents)
        out.append(a)
    return sorted(out, key=lambda a: a.name.lower())


def _units(has_parents: bool, column: str = "buyer_orgnr") -> str:
    """WHERE-uttrykk for virksomheten (?) og underenhetene (?), med to parametere."""
    return (f"({column} = ? OR {column} IN (SELECT child FROM orgnr_parent WHERE parent = ?))" if has_parents
            else f"({column} = ? OR {column} = ?)")


def _joint(conn: sqlite3.Connection, orgnr: str, has_parents: bool) -> list[Contract]:
    """Felles anskaffelser der virksomheten (eller en underenhet) er med, men ikke står som første kjøper.
    Nyeste først."""
    rows = conn.execute(
        f"""SELECT c.id, c.title, c.url, c.value, c.value_type, c.publication_date, c.is_framework,
                   (SELECT name FROM doffin_buyers WHERE contract_id = c.id AND position = 0) AS lead,
                   (SELECT COUNT(*) FROM doffin_buyers WHERE contract_id = c.id) AS n
            FROM doffin_contracts c
            WHERE c.id IN (SELECT contract_id FROM doffin_buyers WHERE position > 0 AND {_units(has_parents, "orgnr")})
              AND NOT {_units(has_parents, "c.buyer_orgnr")}
            ORDER BY c.publication_date DESC, c.id""", (orgnr, orgnr, orgnr, orgnr)).fetchall()
    return [Contract(id=r[0], title=r[1] or "(uten tittel)", url=r[2], value=r[3], year_value=None, share=None,
                     published=r[5], start_date=None, end_date=None, duration_months=None, period_source=None,
                     first_year=None, last_year=None, is_framework=bool(r[6]), value_type=r[4],
                     lead_buyer=display_name(r[7] or ""), n_buyers=r[8]) for r in rows]


def _unmatched(conn: sqlite3.Connection, year: int, matched: set[str], has_parents: bool,
               has_buyers: bool = False) -> list[Unmatched]:
    """Virksomheter med kjøp i regnskapet for året som ikke er i sammenligningen fordi Doffin ikke har
    kontrakter for dem som løper i året. Størst kjøp først."""
    out = []
    units = ("buyer_orgnr = ? OR buyer_orgnr IN (SELECT child FROM orgnr_parent WHERE parent = ?)"
             if has_parents else "buyer_orgnr = ? OR buyer_orgnr = ?")
    for orgnr, name, spend in conn.execute(
            "SELECT orgnr, MAX(virksomhet), SUM(amount) FROM statsregnskap "
            "WHERE year = ? AND orgnr IS NOT NULL GROUP BY orgnr", (year,)).fetchall():
        if orgnr in matched or (spend or 0) < UNMATCHED_MIN_SPEND:
            continue
        n, last = conn.execute(f"SELECT COUNT(*), MAX(year) FROM doffin_contracts WHERE {units}",
                               (orgnr, orgnr)).fetchone()
        name = display_name(name or orgnr)
        note = UNMATCHED_NOTES.get(orgnr) or (STATSFORVALTER_NOTE if name.lower().startswith(("statsforvalter", "statsforvaltar")) else "")
        joint = len(_joint(conn, orgnr, has_parents)) if has_buyers else 0
        out.append(Unmatched(orgnr=orgnr, name=name, spend_sum=spend, notices=n, last_notice=last, note=note,
                             joint=joint))
    return sorted(out, key=lambda u: -u.spend_sum)


def _contracts(conn: sqlite3.Connection, orgnr: str, year: int, annualized: bool) -> list[Contract]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(doffin_contracts)")}

    def col(name: str) -> str:
        return f"c.{name}" if name in cols else f"NULL AS {name}"

    base = (f"SELECT c.id, c.title, c.url, c.value, c.value_type, c.publication_date, c.buyer_orgnr, "
            f"{col('buyer_name')}, "
            f"{col('start_date')}, {col('end_date')}, {col('duration_months')}, {col('is_framework')}")
    if annualized:
        rows = conn.execute(
            f"""{base}, y.value_share, y.share, y.period_source,
                       (SELECT MIN(year) FROM doffin_contract_years WHERE contract_id = c.id) AS first_year,
                       (SELECT MAX(year) FROM doffin_contract_years WHERE contract_id = c.id) AS last_year
                FROM doffin_contract_years y JOIN doffin_contracts c ON c.id = y.contract_id
                WHERE y.buyer_orgnr = ? AND y.year = ?
                ORDER BY y.value_share DESC NULLS LAST, c.value DESC NULLS LAST, c.id""", (orgnr, year))
    else:
        rows = conn.execute(
            f"""{base}, c.value AS value_share, 1.0 AS share, NULL AS period_source,
                       NULL AS first_year, NULL AS last_year
                FROM doffin_contracts c WHERE c.buyer_orgnr = ? AND c.year = ?
                ORDER BY c.value DESC NULLS LAST, c.id""", (orgnr, year))
    return [
        Contract(
            id=r["id"], title=r["title"] or "(uten tittel)", url=r["url"], value=r["value"],
            year_value=r["value_share"], share=r["share"], published=r["publication_date"],
            start_date=r["start_date"], end_date=r["end_date"], duration_months=r["duration_months"],
            period_source=r["period_source"], first_year=r["first_year"], last_year=r["last_year"],
            is_framework=bool(r["is_framework"]), value_type=r["value_type"],
            buyer_unit=display_name(r["buyer_name"] or r["buyer_orgnr"] or "") if r["buyer_orgnr"] != orgnr else None,
        )
        for r in rows
    ]
