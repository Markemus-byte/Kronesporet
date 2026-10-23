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
- Flagg (flag_type 'doffin_dekning') når |z| > Z_THRESHOLD.
  Høy dekning: kontraktverdier langt over kjøpene (rammeavtaler som tak,
  kjøp for andre, eller kjøp som føres utenfor drift). Lav dekning: lite
  kunngjort sammenlignet med kjøpene.

Bygger på `contract_spend_comparison` fra join_and_flag.flag_deviations, så
5 %-flaggene ('doffin_avvik') beholdes uendret ved siden av.
"""
from __future__ import annotations

import math
import sqlite3
import statistics

from src.analysis.join_and_flag import SCHEMA as COMPARISON_SCHEMA

FLAG_TYPE = "doffin_dekning"
Z_THRESHOLD = 2.0
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
    flagged      INTEGER,
    PRIMARY KEY (orgnr, year)
);
"""


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


def flag_coverage_outliers(
    conn: sqlite3.Connection,
    z_threshold: float = Z_THRESHOLD,
    n_bands: int = PEER_BANDS,
    min_peers: int = MIN_PEERS,
) -> int:
    """Regn ut dekning per virksomhet og år og flagg dem som skiller seg ut. Returnerer antall flagg."""
    conn.executescript(COMPARISON_SCHEMA + SCHEMA)
    conn.execute("DELETE FROM doffin_coverage")
    conn.execute("DELETE FROM flags WHERE flag_type = ?", (FLAG_TYPE,))
    rows = conn.execute(
        "SELECT orgnr, virksomhet, year, contract_sum, spend_sum FROM contract_spend_comparison "
        "WHERE contract_sum > 0 AND spend_sum > 0"
    ).fetchall()

    n_flags = 0
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
                flagged = abs(z) > z_threshold
                conn.execute(
                    "INSERT OR REPLACE INTO doffin_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (org, yr, name, cov, group, len(peers), 10 ** med, z, int(flagged)),
                )
                if not flagged:
                    continue
                direction = "høyere" if z > 0 else "lavere"
                conn.execute(
                    "INSERT INTO flags (flag_type, severity, description, source_id) VALUES (?, ?, ?, ?)",
                    (
                        FLAG_TYPE,
                        "warning",
                        f"{name or org} {yr}: Doffin dekker {_pct(cov)} av kjøpene, {direction} enn "
                        f"lignende virksomheter ({group}, median {_pct(10 ** med)}, z = {z:+.1f})",
                        f"{org}:{yr}",
                    ),
                )
                n_flags += 1
    conn.commit()
    return n_flags
