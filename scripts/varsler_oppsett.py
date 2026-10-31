"""Gjør oppsettet rundt varslene via API-er, så det eneste som må gjøres for hånd er å lage nøklene (worker/README.md).

Kjøres av .github/workflows/worker.yml før Worker-en publiseres. Kan kjøres så ofte man vil; det som allerede er
på plass, blir stående.

  - Cloudflare (CLOUDFLARE_API_TOKEN): finner konto-ID-en og velger et workers.dev-underdomene hvis kontoen
    ikke har et.
  - Brevo (BREVO_API_KEY): legger til domenet kronesporet.no og avsenderen varsler@kronesporet.no, og ber Brevo
    sjekke domenet.
  - DNS (CPANEL_USER og CPANEL_TOKEN, valgfritt): legger postene Brevo trenger inn i sonen hos Domene.no via
    cPanel-API-et. En DMARC- eller SPF-post som allerede finnes, beholdes (SPF får Brevo lagt til). Uten
    cPanel-nøkkel står postene som mangler, i oppsummeringen av kjøringen.

Skriver account_id, brevo_ok (true når Brevo har godkjent domenet) og api_token til GITHUB_OUTPUT. api_token er
VARSLER_API_TOKEN hvis den finnes i GitHub, ellers avledet av CLOUDFLARE_API_TOKEN (scripts/send_alerts.py
regner ut det samme), så den ikke må lages for hånd. Ingen nøkler skrives til loggen.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time

import requests

DOMAIN = "kronesporet.no"
SENDER = ("Kronesporet", "varsler@kronesporet.no")
SUBDOMAINS = ["kronesporet", "kronesporet-no", "kronesporet-varsler"]  # workers.dev, første ledige
CF = "https://api.cloudflare.com/client/v4"
BREVO = "https://api.brevo.com/v3"
CPANEL = f"https://cpanel.{DOMAIN}"  # cPanel-proxyen på port 443 (2083 er ofte stengt)

summary: list[str] = []


def say(text: str) -> None:
    print(text)
    summary.append(text)


def derived_api_token(cloudflare_token: str) -> str:
    """Samme verdi i worker.yml og scripts/send_alerts.py, så VARSLER_API_TOKEN ikke må lages for hånd."""
    return hashlib.sha256(f"kronesporet-varsler|{cloudflare_token}".encode()).hexdigest()


# ---------------------------------------------------------------- Cloudflare


def cloudflare(token: str) -> str:
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    r = s.get(f"{CF}/accounts", timeout=30)
    r.raise_for_status()
    accounts = r.json()["result"]
    if not accounts:
        say("Cloudflare: tokenet ser ingen konto. Lag det på nytt med malen «Edit Cloudflare Workers».")
        raise SystemExit(1)
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or accounts[0]["id"]
    r = s.get(f"{CF}/accounts/{account}/workers/subdomain", timeout=30)
    sub = r.json().get("result", {}).get("subdomain") if r.ok else None
    for name in [] if sub else SUBDOMAINS:
        r = s.put(f"{CF}/accounts/{account}/workers/subdomain", json={"subdomain": name}, timeout=30)
        if r.ok:
            sub = name
            break
    say(f"Cloudflare: konto funnet, workers.dev-underdomene {sub or 'ikke satt (velg et under Workers & Pages)'}")
    return account


# ---------------------------------------------------------------- Brevo


class Brevo:
    def __init__(self, key: str) -> None:
        self.s = requests.Session()
        self.s.headers.update({"api-key": key, "accept": "application/json"})

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        r = self.s.request(method, f"{BREVO}{path}", json=body, timeout=30)
        if r.status_code == 401 and "IP" in r.text:
            say("Brevo avviser GitHub sin IP-adresse. Slå av blokkeringen under Security → Authorised IPs i Brevo.")
            raise SystemExit(1)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json() if r.content else {}


def records(dns_records: dict) -> list[dict]:
    """Postene Brevo ber om, som {type, name, value}; name er fullt navn uten punktum til slutt."""
    out = []
    for rec in (dns_records or {}).values():
        if not isinstance(rec, dict) or not rec.get("value"):
            continue
        host = (rec.get("host_name") or "").strip().rstrip(".")
        name = DOMAIN if host in ("", "@", DOMAIN) else host if host.endswith("." + DOMAIN) else f"{host}.{DOMAIN}"
        out.append({"type": rec.get("type", "TXT").upper(), "name": name.lower(), "value": rec["value"].strip(),
                    "ok": rec.get("status") is True})
    return out


def brevo_domain(brevo: Brevo) -> dict:
    info = brevo.call("GET", f"/senders/domains/{DOMAIN}")
    if not info:
        brevo.call("POST", "/senders/domains", {"name": DOMAIN})
        say(f"Brevo: domenet {DOMAIN} lagt til")
        info = brevo.call("GET", f"/senders/domains/{DOMAIN}")
    return info


def brevo_sender(brevo: Brevo) -> None:
    senders = brevo.call("GET", "/senders").get("senders", [])
    if not any(s.get("email", "").lower() == SENDER[1] for s in senders):
        brevo.call("POST", "/senders", {"name": SENDER[0], "email": SENDER[1]})
        say(f"Brevo: avsenderen {SENDER[1]} lagt til")


# ---------------------------------------------------------------- DNS i cPanel


def _norm(name: str) -> str:
    name = name.strip().rstrip(".").lower()
    if name in ("", "@"):
        return DOMAIN
    return name if name == DOMAIN or name.endswith("." + DOMAIN) else f"{name}.{DOMAIN}"


def _relative(name: str) -> str:
    return "@" if name == DOMAIN else name[: -len(DOMAIN) - 1]


def plan_dns(wanted: list[dict], zone: list[dict]) -> list[dict]:
    """Endringene som trengs. zone er [{type, name, value, line}]. Gir {op: add|edit, ...}."""
    changes = []
    for rec in wanted:
        same = [z for z in zone if z["type"] == rec["type"] and z["name"] == rec["name"]]
        if rec["type"] == "CNAME":
            if any(z["value"].rstrip(".").lower() == rec["value"].rstrip(".").lower() for z in same):
                continue
            if same:  # en annen CNAME på samme navn må byttes ut; to CNAME-er på ett navn er ugyldig
                changes.append({"op": "edit", "line": same[0]["line"], **rec})
            else:
                changes.append({"op": "add", **rec})
            continue
        if any(z["value"] == rec["value"] for z in same):
            continue
        if rec["value"].startswith("v=DMARC1") and any(z["value"].startswith("v=DMARC1") for z in same):
            continue  # én DMARC-post per domene; den som finnes, holder for Brevo
        spf = [z for z in same if z["value"].startswith("v=spf1")]
        if rec["value"].startswith("v=spf1") and spf:
            includes = [p for p in rec["value"].split() if p.startswith("include:")]
            old = spf[0]["value"].split()
            missing = [p for p in includes if p not in old]
            if missing:
                tail = [p for p in old if p.endswith("all")]
                merged = [p for p in old if not p.endswith("all")] + missing + tail
                changes.append({"op": "edit", "line": spf[0]["line"], "type": "TXT", "name": rec["name"],
                                "value": " ".join(merged)})
            continue
        changes.append({"op": "add", **rec})
    return changes


class CPanel:
    def __init__(self, user: str, token: str) -> None:
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"cpanel {user}:{token}"

    def call(self, func: str, params: dict) -> dict:
        r = self.s.get(f"{CPANEL}/execute/DNS/{func}", params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if not data.get("status"):
            raise RuntimeError(f"cPanel {func}: {'; '.join(data.get('errors') or ['ukjent feil'])}")
        return data

    def zone(self) -> tuple[int, list[dict]]:
        serial, out = 0, []
        for z in self.call("parse_zone", {"zone": DOMAIN})["data"]:
            if z.get("type") != "record":
                continue
            values = [base64.b64decode(v).decode() for v in z.get("data_b64", [])]
            if z["record_type"] == "SOA":
                serial = int(values[2])
                continue
            name = _norm(base64.b64decode(z["dname_b64"]).decode())
            out.append({"type": z["record_type"], "name": name, "value": "".join(values), "line": z["line_index"]})
        return serial, out

    def apply(self, change: dict) -> None:
        serial, _ = self.zone()
        rec = {"dname": _relative(change["name"]), "ttl": 3600, "record_type": change["type"],
               "data": [change["value"] if change["type"] == "TXT" else change["value"].rstrip(".") + "."]}
        if change["op"] == "edit":
            rec["line_index"] = change["line"]
        self.call("mass_edit_zone", {"zone": DOMAIN, "serial": serial, change["op"]: json.dumps(rec)})


def fix_dns(wanted: list[dict]) -> bool:
    """True når postene er lagt inn (eller fantes fra før)."""
    user, token = os.environ.get("CPANEL_USER", ""), os.environ.get("CPANEL_TOKEN", "")
    if not (user and token):
        say("DNS: ingen cPanel-nøkkel i GitHub (CPANEL_USER og CPANEL_TOKEN), så postene må legges inn for hånd:")
        for rec in wanted:
            if not rec["ok"]:
                say(f"  {rec['type']}  {rec['name']}  →  {rec['value']}")
        return False
    cp = CPanel(user, token)
    _, zone = cp.zone()
    changes = plan_dns(wanted, zone)
    for c in changes:
        cp.apply(c)
        say(f"DNS: {'la inn' if c['op'] == 'add' else 'endret'} {c['type']} {c['name']} → {c['value']}")
    if not changes:
        say("DNS: postene Brevo trenger, er på plass")
    return True


# ---------------------------------------------------------------- hovedløp


def output(**values: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def write_summary() -> None:
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Oppsett\n\n" + "\n".join(f"  - `{line.strip()}`" if line.startswith("  ") else f"- {line}"
                                                for line in summary) + "\n\n")


def main() -> int:
    try:
        return run()
    finally:
        write_summary()


def run() -> int:
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not cf_token:
        say("CLOUDFLARE_API_TOKEN mangler i GitHub; se worker/README.md")
        output(klar="false")
        return 0
    api_token = os.environ.get("VARSLER_API_TOKEN") or derived_api_token(cf_token)
    print(f"::add-mask::{api_token}")
    account = cloudflare(cf_token)

    brevo_ok = False
    key = os.environ.get("BREVO_API_KEY", "")
    if not key:
        say("BREVO_API_KEY mangler i GitHub, så Worker-en kan ikke sende e-post ennå")
    else:
        brevo = Brevo(key)
        info = brevo_domain(brevo)
        brevo_ok = bool(info.get("authenticated"))
        if not brevo_ok:
            if fix_dns(records(info.get("dns_records", {}))):
                for _ in range(3):  # Brevo slår opp DNS selv; nye poster kan bruke litt tid
                    brevo.call("PUT", f"/senders/domains/{DOMAIN}/authenticate")
                    brevo_ok = bool(brevo.call("GET", f"/senders/domains/{DOMAIN}").get("authenticated"))
                    if brevo_ok:
                        break
                    time.sleep(60)
        brevo_sender(brevo)
        say(f"Brevo: domenet er {'godkjent' if brevo_ok else 'ikke godkjent ennå (sjekkes igjen ved neste kjøring)'}")

    output(klar="true", account_id=account, brevo_ok=str(brevo_ok).lower(), api_token=api_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
