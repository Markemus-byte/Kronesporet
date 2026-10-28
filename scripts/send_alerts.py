"""Send e-postvarsler fra site-data/kronesporet.sql (kjøres daglig av .github/workflows/varsler.yml).

Finner nye tildelinger og avtaler som løper ut for hver abonnent (src/alerts/engine.py) og sender
e-posten (src/alerts/mail.py). Abonnentene hentes fra Workeren (worker/, GET /api/abonnenter), og hvert
sendte varsel meldes tilbake (POST /api/sendt), så neste kjøring vet hvor forrige slapp. Abonnenter uten
noe nytt får ingen e-post, så jobben kan kjøres hver dag og på nytt: nye abonnenter får velkomstvarselet
dagen etter at de har bekreftet, og de andre får varsel når den månedlige oppdateringen har lagt inn nye data.

Miljøvariabler:
  VARSLER_API         adressen til Workeren (standard: ALERTS_API i src/web/build.py)
  VARSLER_API_TOKEN   samme verdi som API_TOKEN i Workeren
  BREVO_API_KEY eller SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD   utsending (src/alerts/mail.py)
  STRIPE_PORTAL_URL   valgfri lenke til Stripe-kundeportalen i bunnen av e-posten

Uten Worker-adresse (og uten --abonnenter) avslutter skriptet uten å gjøre noe, så arbeidsflyten
virker før Workeren er satt opp.

Eksempel (fra repo-roten):
  python scripts/send_alerts.py --abonnenter worker/eksempel-abonnenter.json --preview /tmp/varsler
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_site import sql_to_db  # noqa: E402
from src.alerts.engine import build_alert, children, load_contracts, load_subscribers, ref_date  # noqa: E402
from src.alerts.mail import PreviewSender, build_message, sender_from_env  # noqa: E402
from src.web.build import ALERTS_API  # noqa: E402

log = logging.getLogger("varsler")


def _open(path: Path, tmp: str) -> sqlite3.Connection:
    return sqlite3.connect(sql_to_db(path, tmp) if path.suffix == ".sql" else path)


def report_sent(api: str, token: str, rows: list[dict]) -> None:
    import requests
    r = requests.post(f"{api.rstrip('/')}/api/sendt", json=rows, timeout=60,
                      headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=ROOT / "site-data" / "kronesporet.sql", help=".sql eller .db")
    ap.add_argument("--abonnenter", help="JSON-fil med abonnenter i stedet for Workeren (testing)")
    ap.add_argument("--preview", type=Path, help="Skriv e-postene til denne mappen i stedet for å sende dem")
    ap.add_argument("--dry-run", action="store_true", help="Bare skriv ut hvor mange som ville fått varsel")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    api, token = os.environ.get("VARSLER_API") or ALERTS_API, os.environ.get("VARSLER_API_TOKEN", "")
    if not a.abonnenter and not api:
        print("- Varsler: Workeren er ikke satt opp (ALERTS_API), ingen varsler sendt.")
        return 0
    source = a.abonnenter or f"{api.rstrip('/')}/api/abonnenter"
    subs = [s for s in load_subscribers(source, token) if s.receives]

    sender = PreviewSender(a.preview) if a.preview else None if a.dry_run else sender_from_env()
    if sender is None and not a.dry_run:
        raise SystemExit("Varsler: verken BREVO_API_KEY eller SMTP_HOST er satt (bruk --preview for å teste)")

    with tempfile.TemporaryDirectory() as tmp:
        conn = _open(a.data, tmp)
        contracts, ref, units = load_contracts(conn), ref_date(conn), children(conn)
        conn.close()
    if ref is None:
        raise SystemExit("Varsler: ingen Doffin-kontrakter i dataene")
    log.info("%d abonnenter som får varsler, %d kontrakter, data til og med %s", len(subs), len(contracts), ref)

    sent, skipped, failed = 0, 0, 0
    for sub in subs:
        alert = build_alert(sub, contracts, ref, units)
        if alert.empty and not alert.first:
            skipped += 1  # ingenting nytt; vinduene for utløp regnes fra forrige varsel neste gang
            continue
        if a.dry_run:
            log.info("%s: %d tildelinger, %d utløp", sub.email, len(alert.awards), alert.n_expiring)
            sent += 1
            continue
        try:
            sender.send(build_message(alert, api, os.environ.get("STRIPE_PORTAL_URL", "")))
        except Exception as e:  # én feil adresse skal ikke stoppe resten
            log.warning("Kunne ikke sende til abonnent %s: %s", sub.token[:6] or sub.email, e)
            failed += 1
            continue
        sent += 1
        if api and not a.preview and not a.abonnenter:
            report_sent(api, token, [{"email": sub.email, "kjoring": ref.isoformat(), "tildelinger": len(alert.awards),
                                      "utlop": alert.n_expiring, "status": "sendt"}])

    text = f"- Varsler: {sent} sendt, {skipped} uten noe nytt, {failed} feilet (data til og med {ref})."
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n## Varsler\n\n" + text + "\n")
    return 1 if failed and not sent else 0


if __name__ == "__main__":
    sys.exit(main())
