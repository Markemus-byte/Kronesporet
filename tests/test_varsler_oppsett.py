"""Tester for scripts/varsler_oppsett.py (uten nett). Kjør: python -m unittest tests.test_varsler_oppsett"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os  # noqa: E402
from unittest import mock  # noqa: E402

from scripts.varsler_oppsett import _relative, derived_api_token, derived_smtp_password, plan_dns, records  # noqa: E402
from src.alerts.mail import BrevoSender, SmtpSender, sender_from_env  # noqa: E402

ZONE = [
    {"type": "TXT", "name": "kronesporet.no", "line": 10,
     "value": "v=spf1 +a +mx +ip4:185.126.36.19 include:_spf.spamprotection.no ~all"},
    {"type": "TXT", "name": "_dmarc.kronesporet.no", "line": 11, "value": "v=DMARC1; p=none;"},
    {"type": "MX", "name": "kronesporet.no", "line": 12, "value": "0mail.kronesporet.no"},
]
BREVO = {
    "brevo_code": {"type": "TXT", "value": "brevo-code:abc123", "host_name": "@", "status": False},
    "dkim1Record": {"type": "CNAME", "value": "b1.kronesporet-no.dkim.brevo.com", "host_name": "brevo1._domainkey",
                    "status": False},
    "dkim2Record": {"type": "CNAME", "value": "b2.kronesporet-no.dkim.brevo.com", "host_name": "brevo2._domainkey",
                    "status": False},
    "dmarc_record": {"type": "TXT", "value": "v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com",
                     "host_name": "_dmarc", "status": False},
}


class DnsPlanTest(unittest.TestCase):
    def test_records(self) -> None:
        recs = {r["name"]: r for r in records(BREVO)}
        self.assertEqual(recs["kronesporet.no"]["value"], "brevo-code:abc123")
        self.assertIn("brevo1._domainkey.kronesporet.no", recs)
        self.assertEqual(_relative("brevo1._domainkey.kronesporet.no"), "brevo1._domainkey")
        self.assertEqual(_relative("kronesporet.no"), "@")

    def test_plan_keeps_dmarc_and_adds_the_rest(self) -> None:
        changes = plan_dns(records(BREVO), ZONE)
        self.assertEqual(sorted((c["op"], c["name"]) for c in changes), [
            ("add", "brevo1._domainkey.kronesporet.no"), ("add", "brevo2._domainkey.kronesporet.no"),
            ("add", "kronesporet.no")])
        # alt på plass: ingen endringer
        done = ZONE + [{"type": c["type"], "name": c["name"], "value": c["value"], "line": 20 + i}
                       for i, c in enumerate(changes)]
        self.assertEqual(plan_dns(records(BREVO), done), [])

    def test_spf_is_merged(self) -> None:
        spf = [{"type": "TXT", "name": "kronesporet.no", "value": "v=spf1 include:spf.brevo.com ~all", "ok": False}]
        (c,) = plan_dns(spf, ZONE)
        self.assertEqual((c["op"], c["line"]), ("edit", 10))
        self.assertEqual(c["value"], "v=spf1 +a +mx +ip4:185.126.36.19 include:_spf.spamprotection.no "
                                     "include:spf.brevo.com ~all")

    def test_derived_token_is_stable(self) -> None:
        self.assertEqual(derived_api_token("x"), derived_api_token("x"))
        self.assertNotEqual(derived_api_token("x"), derived_api_token("y"))
        self.assertEqual(len(derived_api_token("x")), 64)


class MailChoiceTest(unittest.TestCase):
    def test_smtp_before_brevo(self) -> None:
        env = {"SMTP_HOST": "mail.kronesporet.no", "SMTP_PASSWORD": "pw", "BREVO_API_KEY": "k"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsInstance(sender_from_env(), SmtpSender)
        with mock.patch.dict(os.environ, {"BREVO_API_KEY": "k"}, clear=True):
            self.assertIsInstance(sender_from_env(), BrevoSender)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(sender_from_env())

    def test_smtp_password(self) -> None:
        pw = derived_smtp_password("tok")
        self.assertEqual(pw, derived_smtp_password("tok"))
        self.assertTrue(len(pw) >= 30 and any(c.isupper() for c in pw) and "-" in pw)


if __name__ == "__main__":
    unittest.main()
