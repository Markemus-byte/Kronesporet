"""E-posten i et varsel (tekst, HTML og CSV-vedlegg) og utsendingen.

Utsending, valgt ut fra miljøvariablene:
  BREVO_API_KEY              Brevo (transaksjons-API, EU). Samme konto sender bekreftelsene fra Workeren.
  SMTP_HOST, SMTP_PORT,      vanlig SMTP, f.eks. varsler@kronesporet.no i cPanel hos Domene.no
  SMTP_USER, SMTP_PASSWORD
Uten noen av dem kan varslene bare skrives til filer (--preview i scripts/send_alerts.py).
"""
from __future__ import annotations

import base64
import csv
import io
import os
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape
from pathlib import Path
from urllib.parse import quote

from src.alerts.engine import EXPIRY_SOURCES, Alert, Contract

SITE_URL = "https://kronesporet.no"
SENDER = ("Kronesporet", "varsler@kronesporet.no")
MAX_LISTED = 50  # per seksjon i e-posten; resten står i CSV-uttrekket


@dataclass
class Message:
    to: str
    subject: str
    text: str
    html: str
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[tuple[str, bytes]] = field(default_factory=list)  # (filnavn, innhold)


# --- tekst --------------------------------------------------------------------

def _kr(x: float | None, currency: str | None = None) -> str:
    if x is None:
        return "verdi ikke oppgitt"
    unit = "" if currency in (None, "NOK") else f" {currency}"
    if abs(x) >= 1e9:
        return f"{x / 1e9:.1f} mrd. kr{unit}".replace(".", ",", 1)
    if abs(x) >= 1e6:
        return f"{x / 1e6:.1f} mill. kr{unit}".replace(".", ",", 1)
    return f"{x:,.0f} kr{unit}".replace(",", " ")


def _d(d) -> str:
    return d.strftime("%d.%m.%Y") if d else "?"


def _suppliers(c: Contract) -> str:
    names = [n for n, _ in c.suppliers if n]
    if not names:
        return "leverandør ikke oppgitt"
    return ", ".join(names[:3]) + (f" og {len(names) - 3} til" if len(names) > 3 else "")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def subject(alert: Alert) -> str:
    parts = []
    if alert.awards:
        parts.append(_plural(len(alert.awards), "ny tildeling", "nye tildelinger"))
    if alert.n_expiring:
        parts.append(_plural(alert.n_expiring, "avtale som løper ut", "avtaler som løper ut"))
    if alert.first:
        return "Velkommen til varsler fra Kronesporet" + (f": {' og '.join(parts)}" if parts else "")
    return "Kronesporet: " + " og ".join(parts)


def links(alert: Alert, api_url: str, portal_url: str = "") -> dict[str, str]:
    t = quote(alert.sub.token)
    out = {"endre": f"{SITE_URL}/varsler.html#meld-interesse",
           "avmeld": f"{api_url.rstrip('/')}/avmeld?t={t}" if api_url else f"mailto:{SENDER[1]}?subject=Avmeld"}
    if portal_url:
        out["betaling"] = portal_url
    return out


def _selection_text(alert: Alert) -> str:
    s = alert.sub
    parts = []
    if s.buyers:
        parts.append(_plural(len(s.buyers), "kjøper", "kjøpere"))
    if s.suppliers:
        parts.append(_plural(len(s.suppliers), "leverandør", "leverandører"))
    if s.cpv:
        parts.append("CPV " + ", ".join(sorted(s.cpv)))
    if s.keywords:
        parts.append("søkeord " + ", ".join(f"«{k}»" for k in sorted(s.keywords)))
    return ", ".join(parts)


def _end_text(c: Contract) -> str:
    note = "" if c.end_source == "datoer" else f" ({EXPIRY_SOURCES.get(c.end_source or '', 'anslag')})"
    return f"slutter {_d(c.end)}{note}"


def render_text(alert: Alert, link: dict[str, str]) -> str:
    out = [f"Kronesporet, data til og med {_d(alert.ref)}. Du følger: {_selection_text(alert)}.", ""]
    if alert.first:
        out += ["Takk for at du abonnerer. Dette første varselet viser tildelinger fra den siste måneden og alle",
                "avtaler som kan løpe ut innen horisontene du har valgt. Deretter får du bare det som er nytt.", ""]
    if alert.awards:
        out.append(f"NYE TILDELINGER ({len(alert.awards)})")
        for c in alert.awards[:MAX_LISTED]:
            out += [f"- {c.title}", f"  {c.buyer} -> {_suppliers(c)}. {_kr(c.value, c.currency)}"
                    f"{' (rammeavtale)' if c.is_framework else ''}, publisert {_d(c.published)}.", f"  {c.url or ''}"]
        out.append("")
    for h, cs in alert.expiring.items():
        out.append(f"LØPER UT INNEN {h} MÅNEDER ({len(cs)})")
        for c in cs[:MAX_LISTED]:
            out += [f"- {c.title}", f"  {c.buyer}, {_suppliers(c)}. {_kr(c.value, c.currency)}, {_end_text(c)}.",
                    f"  {c.url or ''}"]
        out.append("")
    if alert.empty:
        out += ["Ingen treff ennå. Du får beskjed når det kommer nye tildelinger eller avtaler som løper ut.", ""]
    if any(len(v) > MAX_LISTED for v in [alert.awards, *alert.expiring.values()]):
        out += [f"Lister med mer enn {MAX_LISTED} treff er forkortet. Alle står i CSV-uttrekket.", ""]
    out += ["Om sluttdatoene: «sluttdato i Doffin» er grunnperioden slik kjøperen har oppgitt den. Står det",
            "«anslag», mangler Doffin sluttdato, og datoen er regnet ut fra varigheten i kunngjøringen. Mange avtaler",
            "har opsjoner på forlengelse og kan løpe lenger.", "",
            f"Endre utvalget: {link['endre']}", f"Avslutt varslene: {link['avmeld']}"]
    if "betaling" in link:
        out.append(f"Betaling og kvitteringer: {link['betaling']}")
    out.append(f"Kilder: Doffin (DFØ), Enhetsregisteret. Se {SITE_URL}/om.html")
    return "\n".join(out) + "\n"


_TD = 'style="padding:6px 8px;border-bottom:1px solid #e5e2da;vertical-align:top"'


def _html_rows(cs: list[Contract], expiring: bool) -> str:
    rows = []
    for c in cs[:MAX_LISTED]:
        title = f'<a href="{escape(c.url)}" style="color:#1d4ed8">{escape(c.title)}</a>' if c.url else escape(c.title)
        fw = ' <span style="color:#6b6b6b">(rammeavtale)</span>' if c.is_framework else ""
        when = escape(_end_text(c)) if expiring else f"publisert {_d(c.published)}"
        rows.append(f"<tr><td {_TD}>{title}{fw}<br><span style=\"color:#6b6b6b\">{escape(c.buyer)} · "
                    f"{escape(_suppliers(c))}</span></td><td {_TD} align=\"right\" nowrap>"
                    f"{escape(_kr(c.value, c.currency))}<br><span style=\"color:#6b6b6b\">{when}</span></td></tr>")
    return "".join(rows)


def render_html(alert: Alert, link: dict[str, str]) -> str:
    sections = []
    if alert.awards:
        sections.append(f'<h2 style="font-size:18px;margin:24px 0 8px">Nye tildelinger ({len(alert.awards)})</h2>'
                        f'<table width="100%" cellspacing="0" style="border-collapse:collapse;font-size:14px">'
                        f"{_html_rows(alert.awards, False)}</table>")
    for h, cs in alert.expiring.items():
        sections.append(f'<h2 style="font-size:18px;margin:24px 0 8px">Løper ut innen {h} måneder ({len(cs)})</h2>'
                        f'<table width="100%" cellspacing="0" style="border-collapse:collapse;font-size:14px">'
                        f"{_html_rows(cs, True)}</table>")
    if alert.empty:
        sections.append("<p>Ingen treff ennå. Du får beskjed når det kommer nye tildelinger eller avtaler som løper ut.</p>")
    intro = ("<p>Takk for at du abonnerer. Dette første varselet viser tildelinger fra den siste måneden og alle avtaler "
             "som kan løpe ut innen horisontene du har valgt. Deretter får du bare det som er nytt.</p>"
             if alert.first else "")
    cut = (f"<p>Lister med mer enn {MAX_LISTED} treff er forkortet. Alle står i CSV-uttrekket.</p>"
           if any(len(v) > MAX_LISTED for v in [alert.awards, *alert.expiring.values()]) else "")
    pay = f' · <a href="{escape(link["betaling"])}">Betaling og kvitteringer</a>' if "betaling" in link else ""
    return f"""<!doctype html><html lang="nb"><head><meta charset="utf-8"><title>{escape(subject(alert))}</title></head>
<body style="margin:0;background:#f7f5f0;font-family:Arial,Helvetica,sans-serif;color:#1c1c1c">
<div style="max-width:680px;margin:0 auto;padding:24px 16px;background:#ffffff">
<p style="font-size:20px;font-weight:bold;margin:0">Kronesporet</p>
<p style="color:#6b6b6b;margin:4px 0 16px">Data til og med {_d(alert.ref)}. Du følger: {escape(_selection_text(alert))}.</p>
{intro}{"".join(sections)}{cut}
<p style="font-size:13px;color:#6b6b6b;margin-top:24px">Om sluttdatoene: «sluttdato i Doffin» er grunnperioden slik
kjøperen har oppgitt den. Står det «anslag», mangler Doffin sluttdato, og datoen er regnet ut fra varigheten i
kunngjøringen. Mange avtaler har opsjoner på forlengelse og kan løpe lenger.</p>
<p style="font-size:13px;color:#6b6b6b"><a href="{escape(link['endre'])}">Endre utvalget</a> ·
<a href="{escape(link['avmeld'])}">Avslutt varslene</a>{pay}<br>
Kilder: Doffin (DFØ) og Enhetsregisteret, under NLOD 2.0. <a href="{SITE_URL}/om.html">Om dataene</a></p>
</div></body></html>
"""


def history_csv(cs: list[Contract]) -> bytes:
    """Alle kontraktene som treffer utvalget, med leverandører og sluttdato (semikolon, UTF-8 med BOM som
    CSV-filene på nettsiden)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["kontrakt_id", "publisert", "kjoper", "kjoper_orgnr", "tittel", "leverandorer", "leverandor_orgnr",
                "verdi", "valuta", "verditype", "rammeavtale", "start", "slutt", "slutt_grunnlag", "cpv", "lenke"])
    for c in cs:
        w.writerow([c.id, c.published.isoformat() if c.published else "", c.buyer, c.buyers[0] if c.buyers else "",
                    c.title, "; ".join(n for n, _ in c.suppliers), "; ".join(o or "" for _, o in c.suppliers),
                    round(c.value) if c.value is not None else "", c.currency or "", c.value_type or "",
                    int(c.is_framework), c.start.isoformat() if c.start else "",
                    c.end.isoformat() if c.end and c.end_source in EXPIRY_SOURCES else "",
                    EXPIRY_SOURCES.get(c.end_source or "", ""), " ".join(c.cpv), c.url or ""])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def build_message(alert: Alert, api_url: str = "", portal_url: str = "") -> Message:
    link = links(alert, api_url, portal_url)
    headers = {"List-Unsubscribe": f"<mailto:{SENDER[1]}?subject=Avmeld>"}
    if link["avmeld"].startswith("https://"):  # ettklikk-avmelding (RFC 8058) via Workeren
        headers = {"List-Unsubscribe": f"<{link['avmeld']}>, {headers['List-Unsubscribe']}",
                   "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    msg = Message(to=alert.sub.email, subject=subject(alert), text=render_text(alert, link),
                  html=render_html(alert, link), headers=headers)
    if alert.sub.csv and alert.history:
        msg.attachments.append((f"kronesporet-kontrakter-{alert.ref.isoformat()}.csv", history_csv(alert.history)))
    return msg


# --- utsending ----------------------------------------------------------------

def to_email(msg: Message) -> EmailMessage:
    m = EmailMessage()
    m["From"] = formataddr(SENDER)
    m["To"] = msg.to
    m["Subject"] = msg.subject
    m["Message-ID"] = make_msgid(domain=SENDER[1].split("@")[1])
    for k, v in msg.headers.items():
        m[k] = v
    m.set_content(msg.text)
    m.add_alternative(msg.html, subtype="html")
    for name, data in msg.attachments:
        m.add_attachment(data, maintype="text", subtype="csv", filename=name)
    return m


class PreviewSender:
    """Skriver hver e-post til en .eml-fil (kan åpnes i e-postprogrammet) og en .html-fil."""

    def __init__(self, out: Path) -> None:
        self.out = out
        out.mkdir(parents=True, exist_ok=True)

    def send(self, msg: Message) -> None:
        stem = msg.to.replace("@", "_at_")
        (self.out / f"{stem}.eml").write_bytes(bytes(to_email(msg)))
        (self.out / f"{stem}.html").write_text(msg.html, encoding="utf-8")


class SmtpSender:
    def __init__(self, host: str, port: int, user: str, password: str) -> None:
        self.host, self.port, self.user, self.password = host, port, user, password

    def send(self, msg: Message) -> None:
        cls = smtplib.SMTP_SSL if self.port == 465 else smtplib.SMTP
        with cls(self.host, self.port, timeout=60) as s:
            if self.port != 465:
                s.starttls()
            s.login(self.user, self.password)
            s.send_message(to_email(msg))


class BrevoSender:
    URL = "https://api.brevo.com/v3/smtp/email"

    def __init__(self, api_key: str) -> None:
        import requests
        self.session = requests.Session()
        self.session.headers.update({"api-key": api_key, "accept": "application/json"})

    def send(self, msg: Message) -> None:
        body = {"sender": {"name": SENDER[0], "email": SENDER[1]}, "to": [{"email": msg.to}],
                "replyTo": {"email": SENDER[1]}, "subject": msg.subject, "htmlContent": msg.html,
                "textContent": msg.text, "headers": msg.headers}
        if msg.attachments:
            body["attachment"] = [{"name": n, "content": base64.b64encode(d).decode()} for n, d in msg.attachments]
        r = self.session.post(self.URL, json=body, timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f"Brevo svarte {r.status_code}: {r.text[:300]}")


def sender_from_env():
    """BrevoSender eller SmtpSender ut fra miljøvariablene, ellers None."""
    if os.environ.get("BREVO_API_KEY"):
        return BrevoSender(os.environ["BREVO_API_KEY"])
    if os.environ.get("SMTP_HOST"):
        return SmtpSender(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 465),
                          os.environ.get("SMTP_USER") or SENDER[1], os.environ.get("SMTP_PASSWORD") or "")
    return None
