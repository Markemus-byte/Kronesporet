"""Tester for Doffin-dekningen (src/analysis/coverage.py). Kjør: python -m unittest tests.test_coverage"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.coverage import TAKEOVER_CATEGORY, chapter_takeovers, flag_coverage_outliers  # noqa: E402
from src.analysis.join_and_flag import SCHEMA as COMPARISON_SCHEMA  # noqa: E402
from src.collectors.statsregnskap import RAADATA_KAPITTEL_SCHEMA  # noqa: E402

DEP, HRS = "972417831", "971390425"


def make_db() -> sqlite3.Connection:
    """Ti virksomheter med dekning rundt 10 %, og «HRS» som overtar et stort kapittel fra «JD» i 2024."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(COMPARISON_SCHEMA + RAADATA_KAPITTEL_SCHEMA)
    rows = []
    for year in (2023, 2024, 2025):
        for i in range(10):
            spend = 100e6 * (i + 1)
            rows.append((f"9000000{i:02d}", f"Virksomhet {i}", year, spend * (0.08 + 0.004 * i), spend))
        hrs_spend = 80e6 if year == 2023 else 1300e6
        rows.append((HRS, "Hovedredningssentralen", year, 9e6 if year == 2023 else 2.5e6, hrs_spend))
    conn.executemany("INSERT INTO contract_spend_comparison (orgnr, virksomhet, year, contract_sum, spend_sum) "
                     "VALUES (?, ?, ?, ?, ?)", rows)
    chapters = [
        (2023, DEP, "0454", "Redningshelikoptertjenesten", "Justis- og beredskapsdepartementet", 1700e6),
        (2023, HRS, "0455", "Redningstjenesten", "Hovedredningssentralen", 80e6),
    ]
    # delt kapittel: mange betaler litt, og når én slutter og en ny begynner, er det ingen overtakelse
    chapters += [(2023, f"90000000{i}", "1540", "Digitaliseringsdirektoratet", f"Virksomhet {i}", 3e6)
                 for i in (1, 2, 3, 4)]
    for year in (2024, 2025):
        chapters += [
            (year, HRS, "0454", "Redningshelikoptertjenesten", "Hovedredningssentralen", 1210e6),
            (year, HRS, "0455", "Redningstjenesten", "Hovedredningssentralen", 90e6),
        ]
        chapters += [(year, f"90000000{i}", "1540", "Digitaliseringsdirektoratet", f"Virksomhet {i}", 3e6)
                     for i in (1, 2, 3, 5)]
    conn.executemany("INSERT INTO statsregnskap_kapittel VALUES (?, ?, ?, ?, ?, ?, 'test')",
                     chapters)
    return conn


class ChapterTakeoverTest(unittest.TestCase):
    def test_finds_chapter_moved_from_department(self) -> None:
        found = chapter_takeovers(make_db())
        self.assertEqual(set(found), {(HRS, 2024), (HRS, 2025)})
        amount, note = found[(HRS, 2025)]
        self.assertEqual(amount, 1210e6)
        self.assertIn("kap. 0454 Redningshelikoptertjenesten", note)
        self.assertIn("Justis- og beredskapsdepartementet til og med 2023", note)

    def test_shared_chapter_is_not_a_takeover(self) -> None:
        found = chapter_takeovers(make_db())
        self.assertNotIn(("900000005", 2024), found)

    def test_takeover_is_marked_not_flagged(self) -> None:
        conn = make_db()
        flag_coverage_outliers(conn)
        rows = {y: (c, f, s) for y, c, f, s in conn.execute(
            "SELECT year, category, flagged, takeover_share FROM doffin_coverage WHERE orgnr = ?", (HRS,))}
        self.assertIsNone(rows[2023][0])
        for year in (2024, 2025):
            category, flagged, share = rows[year]
            self.assertEqual(category, TAKEOVER_CATEGORY)
            self.assertEqual(flagged, 0)
            self.assertAlmostEqual(share, 1210 / 1300)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM flags").fetchone()[0], 0)

    def test_without_chapter_data_it_is_flagged(self) -> None:
        conn = make_db()
        conn.execute("DROP TABLE statsregnskap_kapittel")
        flag_coverage_outliers(conn)
        self.assertEqual(conn.execute("SELECT category FROM doffin_coverage WHERE orgnr = ? AND year = 2025",
                                      (HRS,)).fetchone()[0], "flagg")


if __name__ == "__main__":
    unittest.main()
