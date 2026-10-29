"""Tester for varselmotoren (src/alerts/). Kjør: python -m unittest tests.test_alerts"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.alerts.engine import build_alert, children, cpv_prefix, load_contracts, parse_subscriber  # noqa: E402
from src.alerts.mail import build_message, history_csv  # noqa: E402
from src.collectors.doffin import init_db  # noqa: E402

REF = date(2026, 10, 24)


def make_db() -> sqlite3.Connection:
    conn = init_db(":memory:")
    conn.execute("CREATE TABLE orgnr_parent (child TEXT, parent TEXT, child_name TEXT, kind TEXT)")
    conn.execute("INSERT INTO orgnr_parent VALUES ('982531950', '915429785', 'Politidirektoratet', 'underenhet')")
    rows = [
        # id, kjøper, tittel, verdi, publisert, rammeavtale, start, slutt, varighet, kilde, cpv, lagt inn
        ("2026-1", "982531950", "Batonger", 75e6, "2026-10-15", 1, None, None, None, None, "35800000", "2026-10-20 05:10:00"),
        ("2026-2", "971032081", "Servere og lagring", 580e6, "2026-10-14", 1, None, None, None, None, "48820000 30230000",
         "2026-10-20 05:11:00"),
        ("2024-3", "971032081", "Asfalt Nord", 40e6, "2024-03-01", 0, "2024-03-01", "2026-12-11", None, "datoer", "45233222",
         "2026-09-01 05:00:00"),
        ("2023-4", "915429785", "Skytjenester for politiet", 20e6, "2023-05-01", 0, None, None, 42.0, "varighet",
         "72000000", "2026-09-01 05:00:00"),
        ("2022-5", "915429785", "Renhold", 5e6, "2022-01-10", 0, None, None, None, None, "90910000", "2026-09-01 05:00:00"),
        ("2026-6", "915429785", "Gammel kunngjøring hentet sent", 1e6, "2025-01-05", 0, None, None, None, None, "",
         "2026-10-20 05:12:00"),
    ]
    conn.executemany(
        """INSERT INTO doffin_contracts (id, buyer_orgnr, title, value, publication_date, is_framework, start_date,
           end_date, duration_months, duration_source, cpv, fetched_at, buyer_name, url, year, currency)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Kjøper', 'https://doffin.no/notices/x', 2026, 'NOK')""", rows)
    conn.execute("INSERT INTO doffin_winners VALUES ('2026-2', 0, 'Atea AS', '920237126')")
    conn.execute("INSERT INTO doffin_winners VALUES ('2024-3', 0, 'NCC Industry AS', '911545055')")
    conn.commit()
    return conn


def sub(utvalg: list[tuple[str, str]], sist: str | None = "2026-09-30", horisonter: str = "3,6,12"):
    return parse_subscriber({"email": "A@Example.no", "token": "t" * 48, "status": "active", "horisonter": horisonter,
                             "sist_sendt": sist, "sist_sendt_tid": f"{sist} 07:00:00" if sist else None,
                             "utvalg": [{"type": t, "verdi": v} for t, v in utvalg]})


class AlertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        conn = make_db()
        cls.contracts, cls.units = load_contracts(conn), children(conn)

    def alert(self, s):
        return build_alert(s, self.contracts, REF, self.units)

    def test_cpv_prefix(self) -> None:
        self.assertEqual(cpv_prefix("72000000-1"), "72")
        self.assertEqual(cpv_prefix("30000000"), "30")
        self.assertEqual(cpv_prefix("45210000"), "4521")
        self.assertEqual(cpv_prefix("722"), "722")
        self.assertIsNone(cpv_prefix("7"))

    def test_buyer_includes_subunits(self) -> None:
        a = self.alert(sub([("kjoper", "915429785")]))
        self.assertEqual([c.id for c in a.awards], ["2026-1"])  # 2026-6 er etterslep, ikke nytt
        self.assertEqual({c.id for c in a.history}, {"2026-1", "2023-4", "2022-5", "2026-6"})

    def test_supplier_and_cpv(self) -> None:
        self.assertEqual([c.id for c in self.alert(sub([("leverandor", "atea")])).awards], ["2026-2"])
        self.assertEqual([c.id for c in self.alert(sub([("leverandor", "920237126")])).awards], ["2026-2"])
        # kjøper OG kategori: Statens vegvesen og CPV 48 gir bare serverne
        a = self.alert(sub([("kjoper", "971032081"), ("cpv", "48000000")]))
        self.assertEqual([c.id for c in a.history], ["2026-2"])
        self.assertEqual([c.id for c in self.alert(sub([("sokeord", "skytjenester")])).history], ["2023-4"])

    def test_expiring_windows(self) -> None:
        # Asfalt Nord slutter 11.12.2026: innenfor 3 måneder fra 24.10, ikke fra 31.08 -> krysset siden sist
        a = self.alert(sub([("kjoper", "971032081")], sist="2026-08-31"))
        self.assertEqual({h: [c.id for c in v] for h, v in a.expiring.items()}, {3: ["2024-3"]})
        # samme data på nytt: ingenting nytt
        again = self.alert(sub([("kjoper", "971032081")], sist="2026-10-24"))
        self.assertTrue(again.empty)
        # bare 12 måneder valgt: krysset 12-månedersgrensen tidligere, så ikke med nå
        self.assertFalse(self.alert(sub([("kjoper", "971032081")], sist="2026-08-31", horisonter="12")).expiring)

    def test_estimate_and_standard(self) -> None:
        # Skytjenester: 42 måneder fra 01.05.2023 = ca. 31.10.2026 (anslag); Renhold har standardvarighet og er ikke med
        a = self.alert(sub([("kjoper", "915429785")], sist=None))
        self.assertTrue(a.first)
        exp = [c for v in a.expiring.values() for c in v]
        self.assertEqual([(c.id, c.end_source) for c in exp], [("2023-4", "varighet")])
        msg = build_message(a, "https://varsler.example")
        self.assertIn("anslag fra oppgitt varighet", msg.text)
        self.assertIn("https://varsler.example/avmeld?t=", msg.headers["List-Unsubscribe"])
        self.assertTrue(msg.subject.startswith("Velkommen"))
        self.assertEqual(len(msg.attachments), 1)

    def test_not_sending(self) -> None:
        s = sub([("kjoper", "915429785")])
        s.status = "past_due"
        self.assertFalse(s.receives)
        self.assertFalse(sub([]).receives)

    def test_csv(self) -> None:
        text = history_csv(self.alert(sub([("kjoper", "971032081")])).history).decode("utf-8-sig")
        lines = text.strip().splitlines()
        self.assertTrue(lines[0].startswith("kontrakt_id;publisert;kjoper"))
        self.assertEqual(len(lines), 3)
        self.assertIn("2026-12-11;sluttdato i Doffin", text)


if __name__ == "__main__":
    unittest.main()
