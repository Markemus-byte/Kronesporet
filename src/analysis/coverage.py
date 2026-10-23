"""Doffin-dekningsgrad: hvor stor del av kjøpene som finnes igjen i Doffin.

dekning = årsandel av Doffin-kontrakter / kjøp av varer og tjenester (regnskap).

Doffin viser bare kunngjorte kontrakter. Kjøp under terskelverdi, avrop på
eksisterende avtaler og husleie kunngjøres ikke, så dekningen ligger typisk
langt under 100 %. Et fast avvik på 5 % flagger derfor alle. Her sammenlignes
hver virksomhet i stedet med lignende virksomheter:

- Lignende = samme størrelsesgruppe etter kjøp (tredeling per år). Er gruppen
  mindre enn MIN_PEERS, brukes alle virksomhetene det året.
- Avstanden måles på log10(dekning) som robust z-verdi:
  z = (log10(dekning) - gruppemedian) / (1,4826 * MAD).
- Avvik: |z| > Z_THRESHOLD et enkelt år.
- Flagg (flag_type 'doffin_dekning', category 'flagg') når virksomheten har avvik
  samme vei i minst PERSIST_MIN av de siste PERSIST_WINDOW årene (2 av 3), med
  året selv blant dem. Ett år alene er en observasjon
  (flag_type 'doffin_dekning_observasjon', severity 'info', category 'observasjon').
  Med bare ett regnskapsår i databasen kan ingenting bli mer enn en observasjon.
- Dekning over 100 % (Doffin-andelen er større enn virksomhetens egne kjøp) får egen
  kategori i stedet for flagg, fordi forklaringen som regel er kjent:
    'tilskudd'          kjøpet betales som tilskudd (artskonto 870-876, 878), og
                        tilskuddene er minst like store som Doffin-andelen
                        (f.eks. flyruter og togtrafikk fra Samferdselsdepartementet)
    'avtaler_for_andre' avtaler virksomheten inngår for andre, eller kjøp som føres
                        utenfor kjøp av varer og tjenester (fellesavtaler, folketrygden)
  Høy dekning under 100 % og lav dekning flagges som før.

Bygger på `contract_spend_comparison` fra join_and_flag.flag_deviations, så
5 %-flaggene ('doffin_avvik') beholdes uendret ved siden av.
"""
from __future__ import annotations

import math
import sqlite3
import statistics

from src.analysis.join_and_flag import SCHEMA as COMPARISON_SCHEMA

FLAG_TYPE = "doffin_dekning"
OBSERVATION_TYPE = "doffin_dekning_observasjon"
Z_THRESHOLD = 2.0
PERSIST_WINDOW = 3
PERSIST_MIN = 2
PEER_BANDS = 3
MIN_PEERS = 8
BAND_NAMES = {3: ["små", "mellomstore", "store"]}

SCHEMA = """
CREATE TABLE IF NOT EXISTS doffin_coverage (
    orgnr        TEXT,
    year         INTEGER,
    virksomhet   TEXT,
    coverage     REAL,     -- contract_sum / spend_sum
    peer_group   TEXT,     -- størrelsesgruppe etter kjøp
    peer_count   INTEGER,
    peer_median  REAL,     -- gruppens mediandekning
    z            REAL,     -- robust z-verdi på log10(dekning)
    flagged      INTEGER,  -- 1 når category = 'flagg'
    outlier      INTEGER,  -- |z| > terskel dette året
    years_outlier INTEGER, -- år med avvik samme vei i vinduet (siste PERSIST_WINDOW år)
    category     TEXT,     -- 'flagg' | 'observasjon' | 'tilskudd' | 'avtaler_for_andre' | NULL
    PRIMARY KEY (orgnr, year)
);
"""
NEW_COLUMNS = {"outlier": "INTEGER", "years_outlier": "INTEGER", "category": "TEXT"}


def _robust_z(values: list[float]) -> tuple[float, float]:
    """(median, 1,4826 * MAD) for log10-verdiene."""
    med = statistics.median(values)
    mad = 1.4826 * statistics.median(abs(v - med) for v in values)
    return med, mad


def _pct(x: float) -> str:
    return f"{x:.2%}" if x < 0.01 else f"{x:.0%}"


def _band_name(i: int, n: int) -> str:
    names = BAND_NAMES.get(n)
    return names[i] if names else f"gruppe {i + 1} av {n}"


def _category(cov: float, contract_sum: float, tilskudd: float, outlier: bool, persistent: bool) -> str | None:
    if cov > 1:
        return "tilskudd" if tilskudd >= contract_sum else "avtaler_for_andre"
    if not outlier:
        return None
    return "flagg" if persistent else "observasjon"


def flag_coverage_outliers(
    conn: sqlite3.Connection,
    z_threshold: float = Z_THRESHOLD,
    n_bands: int = PEER_BANDS,
    min_peers: int = MIN_PEERS,
    persist_window: int = PERSIST_WINDOW,
    persist_min: int = PERSIST_MIN,
) -> int:
    """Regn ut dekning per virksomhet og år og flagg dem som skiller seg ut over flere år.

    Returnerer antall flagg (observasjoner og kategorier ikke medregnet).
    """
    conn.executescript(COMPARISON_SCHEMA + SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(doffin_coverage)")}
    for col, typ in NEW_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE doffin_coverage ADD COLUMN {col} {typ}")
    conn.execute("DELETE FROM doffin_coverage")
    conn.execute("DELETE FROM flags WHERE flag_type IN (?, ?)", (FLAG_TYPE, OBSERVATION_TYPE))
    rows = conn.execute(
        "SELECT orgnr, virksomhet, year, contract_sum, spend_sum FROM contract_spend_comparison "
        "WHERE contract_sum > 0 AND spend_sum > 0"
    ).fetchall()
    try:
        tilskudd = {(o, y): a for o, y, a in conn.execute(
            "SELECT orgnr, year, SUM(amount) FROM statsregnskap_tilskudd GROUP BY orgnr, year")}
    except sqlite3.OperationalError:
        tilskudd = {}

    # 1. Robust z per år innen størrelsesgruppen
    scored: dict[tuple[str, int], tuple] = {}
    for year in sorted({r[2] for r in rows}):
        agencies = sorted((r for r in rows if r[2] == year), key=lambda r: r[4])
        size = len(agencies)
        bands = [agencies[i * size // n_bands:(i + 1) * size // n_bands] for i in range(n_bands)]
        for i, band in enumerate(bands):
            peers, group = (band, _band_name(i, n_bands)) if len(band) >= min_peers else (agencies, "alle")
            if len(peers) < 3:
                continue
            med, mad = _robust_z([math.log10(r[3] / r[4]) for r in peers])
            for org, name, yr, contract_sum, spend in band:
                cov = contract_sum / spend
                z = (math.log10(cov) - med) / mad if mad else 0.0
                scored[(org, yr)] = (name, cov, contract_sum, group, len(peers), 10 ** med, z)

    # 2. Vedvarende avvik og kategori
    n_flags = 0
    for (org, yr), (name, cov, contract_sum, group, n_peers, peer_med, z) in sorted(scored.items()):
        outlier = abs(z) > z_threshold
        sign = 1 if z > 0 else -1
        window = [scored.get((org, y)) for y in range(yr - persist_window + 1, yr + 1)]
        years_outlier = sum(1 for w in window if w and abs(w[6]) > z_threshold and (w[6] > 0) == (sign > 0))
        category = _category(cov, contract_sum, tilskudd.get((org, yr), 0.0), outlier,
                             outlier and years_outlier >= persist_min)
        conn.execute(
            "INSERT OR REPLACE INTO doffin_coverage "
            "(orgnr, year, virksomhet, coverage, peer_group, peer_count, peer_median, z, flagged, "
            "outlier, years_outlier, category) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (org, yr, name, cov, group, n_peers, peer_med, z, int(category == "flagg"),
             int(outlier), years_outlier if outlier else 0, category),
        )
        if category not in ("flagg", "observasjon"):
            continue
        direction = "høyere" if z > 0 else "lavere"
        persist = (f", {years_outlier} av de siste {persist_window} årene" if category == "flagg"
                   else ", bare dette året")
        conn.execute(
            "INSERT INTO flags (flag_type, severity, description, source_id) VALUES (?, ?, ?, ?)",
            (
                FLAG_TYPE if category == "flagg" else OBSERVATION_TYPE,
                "warning" if category == "flagg" else "info",
                f"{name or org} {yr}: Doffin dekker {_pct(cov)} av kjøpene, {direction} enn "
                f"lignende virksomheter ({group}, median {_pct(peer_med)}, z = {z:+.1f}{persist})",
                f"{org}:{yr}",
            ),
        )
        n_flags += category == "flagg"
    conn.commit()
    return n_flags
