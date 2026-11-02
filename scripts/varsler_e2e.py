"""Prøver påmeldingen fra start til slutt mot den publiserte Worker-en (kjøres av .github/workflows/worker.yml).

1. Melder på utsending@kronesporet.no (postkassen utsendingen bruker, se scripts/varsler_oppsett.py) via /pamelding.
2. Henter bekreftelses-e-posten fra postkassen over IMAP. Det viser at Worker-en sender e-post.
3. Åpner bekreftelseslenken (siden med knappen) og trykker på knappen, som skal sende videre til Stripe.
4. Sjekker at /api/abonnenter svarer med tokenet fra GitHub.
5. Sletter prøveabonnenten og e-posten igjen.

Miljø: WORKER_URL, API_TOKEN og CPANEL_TOKEN (passordet til postkassen avledes av det). Skriver resultatet til
oppsummeringen av kjøringen og avslutter med feil hvis et steg feiler.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import sys
import time
from email.header import decode_header, make_header

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts.varsler_oppsett import SMTP_HOSTS, SMTP_USER, derived_smtp_password  # noqa: E402

lines: list[str] = []


def say(text: str) -> None:
    print(text)
    lines.append(f"- {text}")


def body_text(msg: email.message.Message) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    return "\n".join(p.get_payload(decode=True).decode("utf-8", "replace") for p in parts
                     if p.get_content_type() == "text/plain")


def find_link(imap: imaplib.IMAP4_SSL, since: float, base: str) -> tuple[str | None, list[bytes]]:
    imap.select("INBOX")
    _, data = imap.search(None, "ALL")
    ids = data[0].split()
    found, mine = None, []
    for num in ids[-20:]:
        _, raw = imap.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(raw[0][1])
        if "Bekreft varsler" not in str(make_header(decode_header(msg.get("Subject", "")))):
            continue
        mine.append(num)
        sent = email.utils.parsedate_to_datetime(msg["Date"]).timestamp() if msg.get("Date") else 0
        m = re.search(re.escape(base) + r"/bekreft\?t=([0-9a-f]{48})", body_text(msg))
        if m and sent >= since - 120:
            found = m.group(0)
    return found, mine


def run() -> int:
    base = os.environ["WORKER_URL"].rstrip("/")
    password = derived_smtp_password(os.environ["CPANEL_TOKEN"])
    s = requests.Session()

    for attempt in range(10):  # et nytt workers.dev-underdomene kan bruke noen minutter
        try:
            r = s.get(f"{base}/api/abonnenter", headers={"Authorization": f"Bearer {os.environ['API_TOKEN']}"},
                      timeout=30)
            if r.status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(30)
    else:
        say("Prøve: Worker-en svarer ikke på /api/abonnenter")
        return 1
    say(f"Prøve: /api/abonnenter svarer ({len(r.json())} betalende eller gratis abonnenter)")

    start = time.time()
    r = s.post(f"{base}/pamelding", data={"epost": SMTP_USER, "kjoper": "986252932", "horisont": "3"},
               allow_redirects=False, timeout=30)
    status = re.search(r"status=([a-z-]+)", r.headers.get("location", ""))
    if r.status_code != 303 or not status or status.group(1) not in ("sjekk-epost", "vent"):
        say(f"Prøve: påmeldingen svarte {r.status_code} {r.headers.get('location', r.text[:200])}")
        return 1
    say(f"Prøve: påmelding sendt ({status.group(1)})")

    imap = None
    for host in SMTP_HOSTS:
        try:
            imap = imaplib.IMAP4_SSL(host, 993, timeout=30)
            imap.login(SMTP_USER, password)
            break
        except (OSError, imaplib.IMAP4.error):
            imap = None
    if imap is None:
        say("Prøve: kom ikke inn i postkassen over IMAP")
        return 1
    link, mine = None, []
    for _ in range(18):
        link, mine = find_link(imap, start if status.group(1) == "sjekk-epost" else start - 900, base)
        if link:
            break
        time.sleep(10)
    if not link:
        say("Prøve: bekreftelses-e-posten kom ikke frem innen 3 minutter")
        return 1
    say(f"Prøve: bekreftelses-e-posten kom frem etter {time.time() - start:.0f} sekunder")

    r = s.get(link, timeout=30)
    if r.status_code != 200 or "<form" not in r.text:
        say(f"Prøve: bekreftelsessiden svarte {r.status_code}")
        return 1
    r = s.post(link, allow_redirects=False, timeout=30)
    loc = r.headers.get("location", "")
    token = link.rsplit("=", 1)[1]
    if r.status_code != 303 or ("stripe.com" not in loc and "status=bekreftet" not in loc):
        say(f"Prøve: bekreftelsen svarte {r.status_code} {loc[:120]}")
        return 1
    say("Prøve: bekreftet, sendt videre til " + ("betaling i Stripe" if "stripe.com" in loc else loc.split("?")[0])
        + (" med riktig kobling (client_reference_id)" if f"client_reference_id={token}" in loc else ""))

    r = s.post(f"{base}/slett?t={token}", timeout=30)
    say("Prøve: prøveabonnenten er slettet igjen" if r.status_code == 200 else f"Prøve: slett svarte {r.status_code}")
    for num in mine:
        imap.store(num, "+FLAGS", "\\Deleted")
    imap.expunge()
    imap.logout()
    return 0 if r.status_code == 200 else 1


def main() -> int:
    try:
        return run()
    finally:
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write("\n## Prøve av påmeldingen\n\n" + "\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
