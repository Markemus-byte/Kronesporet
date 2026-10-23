"""Eksporter tallene nettsiden bygges fra til en SQL-tekstfil i repoet.

Nettsiden bygges automatisk av GitHub Actions (.github/workflows/nettside.yml)
fra site-data/kronesporet.sql. Tekstformatet gir små, lesbare differ i git,
i motsetning til en binær .db-fil. Bare tabellene og radene siden bruker tas med:
virksomhetene i contract_spend_comparison og kontraktene der de er kjøper.

Eksempel (fra repo-roten), etter run_phase1.py og fetch_winners.py:
  python scripts/export_site_data.py --db data/offentlig_forbruk.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (tabell, WHERE-filter). Filteret begrenser til virksomhetene som er med på nettsiden.
BUYERS = "SELECT orgnr FROM contract_spend_comparison"
TABLES: list[tuple[str, str]] = [
    ("contract_spend_comparison", ""),
    ("statsregnskap", f"orgnr IN ({BUYERS})"),
    ("doffin_coverage", ""),
    ("doffin_contracts", f"buyer_orgnr IN ({BUYERS})"),
    ("doffin_contract_years", f"buyer_orgnr IN ({BUYERS})"),
    ("doffin_winners", f"contract_id IN (SELECT id FROM doffin_contracts WHERE buyer_orgnr IN ({BUYERS}))"),
]


def export(src: Path, dest: Path) -> dict[str, int]:
    conn = sqlite3.connect(src)
    out = sqlite3.connect(":memory:")
    counts: dict[str, int] = {}
    try:
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table, where in TABLES:
            if table not in present:
                continue
            schema = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                                  (table,)).fetchone()[0]
            out.execute(schema)
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            rows = conn.execute(f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "")
                                + f" ORDER BY {', '.join(cols[:2])}").fetchall()
            out.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' for _ in cols)})", rows)
            counts[table] = len(rows)
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
