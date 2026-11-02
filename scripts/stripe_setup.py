"""Setter opp betalingen for varslene i Stripe via API-et (#39).

Lager eller oppdaterer, så skriptet kan kjøres flere ganger uten å lage duplikater:
  - produktet «Kronesporet varsler» med en månedspris (lookup_key kronesporet_varsler_standard)
  - en Payment Link for prisen, med felt for firmanavn og orgnr og retur til varsler.html#takk
  - en kundeportal der abonnenter sier opp (fra slutten av perioden), bytter kort og ser kvitteringer
  - med --webhook-url: webhook-endepunktet til Workeren. Signeringshemmeligheten skrives bare til
    --webhook-secret-file, aldri til loggen, siden loggene i et offentlig repo er offentlige.

Nøkkelen leses fra STRIPE_SECRET_KEY. Uten --live godtas bare test- og sandkassenøkler (sk_test_/rk_test_).

  STRIPE_SECRET_KEY=sk_test_... python scripts/stripe_setup.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlencode

import requests

API = "https://api.stripe.com/v1"
SITE = "https://kronesporet.no"
TAG = {"kronesporet": "varsler"}  # metadata som kjennetegner det skriptet har laget

PRODUCT_NAME = "Kronesporet varsler"
PRODUCT_DESCRIPTION = ("E-postvarsler om nye tildelinger og avtaler som løper ut hos kjøperne, leverandørene "
                       "eller kategoriene du følger, med uttrekk som CSV.")
PRICE_LOOKUP_KEY = "kronesporet_varsler_standard"
PRICE_NOK = 490  # i måneden eks. mva, se PAID_ALERTS_PRICE i src/web/build.py
TRIAL_DAYS = 14  # gratis prøveperiode på nye betalingslenker (TRIAL_DAYS i src/web/build.py)
WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
]


def flatten(data: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Gjør nøstede dict/list om til Stripes skjemaformat: a[b][0][c]=verdi."""
    out: list[tuple[str, str]] = []
    for key, value in data.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out += flatten(value, name)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    out += flatten(item, f"{name}[{i}]")
                else:
                    out.append((f"{name}[{i}]", _str(item)))
        elif value is not None:
            out.append((name, _str(value)))
    return out


def _str(value: object) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


class Stripe:
    def __init__(self, key: str, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Bearer {key}"

    def call(self, method: str, path: str, params: dict | None = None) -> dict:
        body = urlencode(flatten(params or {}))
        if method == "GET":
            resp = self.session.get(f"{API}{path}", params=body or None, timeout=30)
        else:
            resp = self.session.request(method, f"{API}{path}", data=body, timeout=30,
                                        headers={"Content-Type": "application/x-www-form-urlencoded"})
        data = resp.json()
        if resp.status_code >= 400:
            err = data.get("error", {})
            raise SystemExit(f"Stripe svarte {resp.status_code} på {method} {path}: {err.get('message', data)}")
        return data

    def list_all(self, path: str, params: dict | None = None) -> list[dict]:
        items: list[dict] = []
        params = {"limit": 100, **(params or {})}
        while True:
            page = self.call("GET", path, params)
            items += page["data"]
            if not page.get("has_more"):
                return items
            params["starting_after"] = page["data"][-1]["id"]


def _tagged(obj: dict) -> bool:
    return all((obj.get("metadata") or {}).get(k) == v for k, v in TAG.items())


def ensure_price(stripe: Stripe) -> dict:
    """Månedsprisen. Endres PRICE_NOK, lages en ny pris som tar over lookup_key; gamle kunder beholder sin."""
    found = stripe.call("GET", "/prices", {"lookup_keys": [PRICE_LOOKUP_KEY], "active": True})["data"]
    if found and found[0]["unit_amount"] == PRICE_NOK * 100 and found[0]["currency"] == "nok":
        print(f"Pris finnes: {found[0]['id']} ({PRICE_NOK} kr i måneden)")
        return found[0]
    if found:
        product_id = found[0]["product"]
    else:
        products = [p for p in stripe.list_all("/products", {"active": True}) if _tagged(p)]
        product_id = products[0]["id"] if products else stripe.call("POST", "/products", {
            "name": PRODUCT_NAME, "description": PRODUCT_DESCRIPTION, "metadata": TAG,
        })["id"]
    price = stripe.call("POST", "/prices", {
        "product": product_id, "currency": "nok", "unit_amount": PRICE_NOK * 100,
        "recurring": {"interval": "month"}, "tax_behavior": "exclusive", "nickname": "standard",
        "lookup_key": PRICE_LOOKUP_KEY, "transfer_lookup_key": True, "metadata": {**TAG, "plan": "standard"},
    })
    print(f"Pris laget: {price['id']} ({PRICE_NOK} kr i måneden) på produktet {product_id}")
    return price


def ensure_payment_link(stripe: Stripe, price_id: str) -> dict:
    links = [link for link in stripe.list_all("/payment_links", {"active": True}) if _tagged(link)]
    current = [link for link in links if link["metadata"].get("price") == price_id
               and link["metadata"].get("trial_days") == str(TRIAL_DAYS)]  # ny prøveperiode gir ny lenke
    for old in links:
        if old not in current[:1]:
            stripe.call("POST", f"/payment_links/{old['id']}", {"active": False})
            print(f"Gammel betalingslenke slått av: {old['id']}")
    if current:
        print(f"Betalingslenke finnes: {current[0]['id']}")
        return current[0]
    link = stripe.call("POST", "/payment_links", {
        "line_items": [{"price": price_id, "quantity": 1}],
        "after_completion": {"type": "redirect", "redirect": {"url": f"{SITE}/varsler.html#takk"}},
        "billing_address_collection": "required",
        "custom_fields": [
            {"key": "firma", "type": "text", "label": {"type": "custom", "custom": "Firmanavn"}},
            {"key": "orgnr", "type": "numeric", "label": {"type": "custom", "custom": "Organisasjonsnummer"},
             "numeric": {"minimum_length": 9, "maximum_length": 9}},
        ],
        "custom_text": {"submit": {"message": (
            f"De første {TRIAL_DAYS} dagene er gratis. Abonnementet fornyes hver måned til du sier det opp, "
            "og selges bare til næringsdrivende. "
            f"Vilkårene står på {SITE}/vilkar.html#abonnement")}},
        "subscription_data": {"trial_period_days": TRIAL_DAYS, "metadata": {**TAG, "plan": "standard"}},
        "metadata": {**TAG, "price": price_id, "trial_days": str(TRIAL_DAYS)},
    })
    print(f"Betalingslenke laget: {link['id']}")
    return link


def ensure_portal(stripe: Stripe) -> dict:
    params = {
        "business_profile": {
            "headline": "Kronesporet varsler",
            "privacy_policy_url": f"{SITE}/vilkar.html#personvern",
            "terms_of_service_url": f"{SITE}/vilkar.html#abonnement",
        },
        "default_return_url": f"{SITE}/varsler.html",
        "features": {
            "customer_update": {"enabled": True, "allowed_updates": ["email", "name", "address", "tax_id"]},
            "invoice_history": {"enabled": True},
            "payment_method_update": {"enabled": True},
            "subscription_cancel": {
                "enabled": True, "mode": "at_period_end",
                "cancellation_reason": {"enabled": True, "options": [
                    "too_expensive", "missing_features", "unused", "switched_service", "other"]},
            },
        },
        "login_page": {"enabled": True},
    }
    configs = [c for c in stripe.list_all("/billing_portal/configurations") if _tagged(c)]
    if configs:
        portal = stripe.call("POST", f"/billing_portal/configurations/{configs[0]['id']}", params)
        print(f"Kundeportal oppdatert: {portal['id']}")
    else:
        portal = stripe.call("POST", "/billing_portal/configurations", {**params, "metadata": TAG})
        print(f"Kundeportal laget: {portal['id']}")
    return portal


def ensure_webhook(stripe: Stripe, url: str, secret_file: str | None) -> dict:
    """Hemmeligheten vises bare når endepunktet lages, så et eksisterende lages på nytt når den trengs."""
    existing = [w for w in stripe.list_all("/webhook_endpoints") if w["url"] == url]
    if existing and not secret_file:
        hook = stripe.call("POST", f"/webhook_endpoints/{existing[0]['id']}", {"enabled_events": WEBHOOK_EVENTS})
        print(f"Webhook oppdatert: {hook['id']} -> {url}")
        return hook
    for old in existing:
        stripe.call("DELETE", f"/webhook_endpoints/{old['id']}")
        print(f"Webhook {old['id']} lages på nytt for å få ny hemmelighet")
    hook = stripe.call("POST", "/webhook_endpoints", {
        "url": url, "enabled_events": WEBHOOK_EVENTS, "description": "Kronesporet varsler (Worker)",
        "metadata": TAG,
    })
    if secret_file:
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write(hook["secret"])
        print(f"Webhook laget: {hook['id']} -> {url}. Hemmeligheten er skrevet til {secret_file}")
    else:
        print(f"Webhook laget: {hook['id']} -> {url}. Kjør med --webhook-secret-file for å få hemmeligheten")
    return hook


def show_status(stripe: Stripe) -> None:
    """Abonnementene på prisen, uten e-post eller navn (loggene er offentlige)."""
    subs = stripe.list_all("/subscriptions", {"status": "all", "price": None})
    ours = [s for s in subs if any(i["price"].get("lookup_key") == PRICE_LOOKUP_KEY or _tagged(i["price"])
                                   for i in s["items"]["data"])]
    print(f"Abonnementer på Kronesporet varsler: {len(ours)}")
    for sub in ours:
        amount = sum(i["price"]["unit_amount"] * i.get("quantity", 1) for i in sub["items"]["data"]) / 100
        print(f"  {sub['id']}: {sub['status']}, {amount:.0f} kr, startet {_date(sub['created'])}"
              + (", sagt opp fra periodens slutt" if sub.get("cancel_at_period_end") else ""))
    paid = stripe.call("GET", "/checkout/sessions", {"limit": 20, "status": "complete"})["data"]
    ref = sum(1 for c in paid if c.get("client_reference_id"))
    print(f"Fullførte betalinger (siste 20): {len(paid)}, {ref} med kobling til påmeldingen (client_reference_id)")


def _date(ts: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", action="store_true", help="tillat live-nøkkel (ekte betalinger)")
    ap.add_argument("--webhook-url", help="Workerens adresse til /stripe/webhook")
    ap.add_argument("--webhook-secret-file", help="fil som får signeringshemmeligheten (whsec_...)")
    ap.add_argument("--status", action="store_true", help="bare vis abonnementene, endre ingenting")
    args = ap.parse_args(argv)

    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        raise SystemExit("STRIPE_SECRET_KEY mangler")
    live = key.startswith(("sk_live_", "rk_live_"))
    if live and not args.live:
        raise SystemExit("Dette er en live-nøkkel. Kjør med --live hvis det er meningen.")
    print(f"Modus: {'LIVE' if live else 'test/sandkasse'}")

    stripe = Stripe(key)
    # I GitHub Actions endres Stripe bare fra main; fra andre grener vises bare status.
    if args.status or os.environ.get("GITHUB_REF", "refs/heads/main") != "refs/heads/main":
        show_status(stripe)
        return 0
    price = ensure_price(stripe)
    link = ensure_payment_link(stripe, price["id"])
    portal = ensure_portal(stripe)
    if args.webhook_url:
        ensure_webhook(stripe, args.webhook_url, args.webhook_secret_file)

    result = {
        "modus": "live" if live else "test",
        "pris": price["id"],
        "STRIPE_PAYMENT_LINK": link["url"],
        "STRIPE_PORTAL_URL": (portal.get("login_page") or {}).get("url"),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## Stripe ({result['modus']})\n\n"
                    f"- Betalingslenke (`STRIPE_PAYMENT_LINK`): {result['STRIPE_PAYMENT_LINK']}\n"
                    f"- Kundeportal (`STRIPE_PORTAL_URL`): {result['STRIPE_PORTAL_URL']}\n"
                    f"- Pris: `{result['pris']}`, {PRICE_NOK} kr i måneden eks. mva\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
