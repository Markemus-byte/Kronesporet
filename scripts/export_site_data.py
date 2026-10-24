"""Eksporter tallene nettsiden bygges fra til en SQL-tekstfil i repoet.

Nettsiden bygges automatisk av GitHub Actions (.github/workflows/nettside.yml)
fra site-data/kronesporet.sql. Tekstformatet gir små, lesbare differ i git,
i motsetning til en binær .db-fil. Bare tabellene og radene siden bruker tas med:
virksomhetene i regnskapet (statsregnskap og contract_spend_comparison) og kontraktene der de
(eller underenhetene) er kjøper. Virksomheter uten kontrakter i et år vises også på siden.

Eksempel (fra repo-roten), etter run_phase1.py og fetch_winners.py:
  python scripts/export_site_data.py --db data/offentlig_forbruk.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (tabell, WHERE-filter). Filteret begrenser til virksomhetene som er med på nettsiden.
# Kontraktene hentes via doffin_contract_years når den finnes, fordi kjøperen der er
# regnskapsvirksomheten også når en underenhet (orgnr_parent) står som kjøper i Doffin.
# Helseforetakene (tabellen foretak) er med når den finnes; {buyers} byttes ut i export().
BUYERS = ("SELECT orgnr FROM contract_spend_comparison UNION "
          "SELECT orgnr FROM statsregnskap WHERE orgnr IS NOT NULL")
HEALTH = " UNION SELECT orgnr FROM foretak"
TABLES: list[tuple[str, str]] = [
    ("contract_spend_comparison", ""),
    ("foretak", ""),
    ("foretak_regnskap", ""),
    ("statsregnskap", "orgnr IN ({buyers})"),
    ("statsregnskap_tilskudd", "orgnr IN ({buyers})"),  # skiller «tilskudd» fra «avtaler for andre» i dekningen
    ("doffin_coverage", ""),
    ("orgnr_parent", "parent IN ({buyers})"),
    ("doffin_contracts", "{contracts}"),
    ("doffin_contract_years", "buyer_orgnr IN ({buyers})"),
    ("doffin_winners", "contract_id IN (SELECT id FROM doffin_contracts WHERE {contracts})"),
    ("doffin_buyers", "contract_id IN (SELECT id FROM doffin_contracts WHERE {contracts})"),
]


HIDDEN = ROOT / "site-data" / "skjulte_leverandorer.txt"
HIDDEN_NAME = "Enkeltpersonforetak (skjult)"


def hidden_suppliers(path: Path = HIDDEN) -> set[str]:
    """Orgnr til enkeltpersonforetak som har bedt om å ikke vises med navn (en per linje, # er kommentar)."""
    if not path.exists():
        return set()
    return {line.split("#")[0].strip() for line in path.read_text(encoding="utf-8").splitlines()} - {""}


def _buyers(present: set[str]) -> str:
    return BUYERS + (HEALTH if "foretak" in present else "")


def _contracts_filter(present: set[str]) -> str:
    roots = _buyers(present)
    buyers = f"{roots} UNION SELECT child FROM orgnr_parent WHERE parent IN ({roots})" if "orgnr_parent" in present else roots
    where = f"buyer_orgnr IN ({buyers})"
    if "doffin_contract_years" in present:
        where += f" OR id IN (SELECT contract_id FROM doffin_contract_years WHERE buyer_orgnr IN ({roots}))"
    if "doffin_buyers" in present:  # felles anskaffelser der virksomheten ikke står som første kjøper
        where += f" OR id IN (SELECT contract_id FROM doffin_buyers WHERE orgnr IN ({buyers}))"
    return where


def export(src: Path, dest: Path) -> dict[str, int]:
    conn = sqlite3.connect(src)
    out = sqlite3.connect(":memory:")
    counts: dict[str, int] = {}
    try:
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        contracts = _contracts_filter(present)
        for table, where in TABLES:
            if table not in present:
                continue
            where = where.replace("{contracts}", contracts).replace("{buyers}", _buyers(present))
            schema = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                                  (table,)).fetchone()[0]
            out.execute(schema)
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            rows = conn.execute(f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "")
                                + f" ORDER BY {', '.join(cols[:2])}").fetchall()
            out.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' for _ in cols)})", rows)
            counts[table] = len(rows)
        hidden = hidden_suppliers()
        if hidden and "doffin_winners" in counts:
            out.execute(f"UPDATE doffin_winners SET name = ?, orgnr = NULL "
                        f"WHERE orgnr IN ({', '.join('?' for _ in hidden)})", [HIDDEN_NAME, *sorted(hidden)])
        out.commit()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8", newline="\n") as f:
            for line in out.iterdump():
                f.write(line + "\n")
    finally:
        conn.close()
        out.close()
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "offentlig_forbruk.db")
    ap.add_argument("--out", type=Path, default=ROOT / "site-data" / "kronesporet.sql")
    args = ap.parse_args()
    counts = export(args.db, args.out)
    size = args.out.stat().st_size / 1e6
    print(f"Skrev {args.out} ({size:.1f} MB): " + ", ".join(f"{t} {n}" for t, n in counts.items()))


if __name__ == "__main__":
    main()
