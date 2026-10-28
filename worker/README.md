# Varsler (Cloudflare Worker + D1)

Workeren tar imot påmeldinger fra skjemaet på [kronesporet.no/varsler.html](https://kronesporet.no/varsler.html),
sender e-post for å bekrefte adressen, sender bekreftede abonnenter videre til betaling i Stripe, tar imot
betalingsstatus fra Stripe og gir abonnentlisten til utsendingen i GitHub Actions.

```
varsler.html ──POST /pamelding──▶ Worker ──bekreft-e-post (Brevo)──▶ abonnent
                                    │  POST /bekreft ──▶ Stripe Payment Link ──webhook──▶ /stripe/webhook
                                    ▼
                              D1: abonnenter, utvalg, sendte_varsler
                                    ▲
GitHub Actions (varsler.yml, daglig) ── GET /api/abonnenter, POST /api/sendt
  scripts/send_alerts.py: nye tildelinger og avtaler som løper ut fra site-data/kronesporet.sql ──e-post (Brevo)──▶ abonnent
```

- `schema.sql`: datamodellen (abonnenter, utvalg, sendte varsler, behandlede Stripe-hendelser).
- `src/index.js`: Workeren. Ingen avhengigheter.
- `test/`: tester mot en D1-erstatning på `node:sqlite`. Kjør `node --test worker/test/*.test.mjs` (Node 22+).
- `eksempel-abonnenter.json`: abonnenter med example.no-adresser for å prøve utsendingen lokalt:
  `python scripts/send_alerts.py --abonnenter worker/eksempel-abonnenter.json --preview /tmp/varsler`

Ingen e-postadresser ligger i repoet. De ligger bare i D1 (og i Stripe for dem som betaler).

## Sjekkliste for oppsett (Morten)

Alt under gjøres én gang. Tallene i parentes er omtrentlig tid.

**1. E-post: Brevo (15 min + venting på DNS)**
1. Lag en gratis konto på [brevo.com](https://www.brevo.com) med post@kronesporet.no. Gratisplanen gir 300 e-poster
   om dagen, som holder lenge.
2. Under *Senders, Domains & Dedicated IPs → Domains*: legg til `kronesporet.no`. Brevo viser 3–4 DNS-poster
   (TXT for `brevo-code`, DKIM og DMARC). Legg dem inn i cPanel hos Domene.no under *Zone Editor*, som A-postene.
   Ikke rør MX-postene. Trykk *Authenticate* i Brevo når postene er lagt inn.
3. Legg til avsenderen `varsler@kronesporet.no` under *Senders*.
4. Lag en API-nøkkel under *SMTP & API → API Keys*. Ta vare på den til steg 2 og 3.

**2. Cloudflare Worker og database (15 min)**
1. Lag en gratis konto på [dash.cloudflare.com](https://dash.cloudflare.com). Domenet trenger ikke flyttes dit.
2. På en maskin med Node.js, i mappen `worker/` i repoet:
   ```
   npx wrangler login
   npx wrangler d1 create kronesporet-varsler
   ```
   Kopier `database_id` fra svaret inn i `wrangler.toml` (eller lim den inn i tråden, så gjør jeg det).
3. ```
   npx wrangler d1 execute kronesporet-varsler --remote --file=schema.sql
   npx wrangler secret put BREVO_API_KEY        # nøkkelen fra Brevo
   npx wrangler secret put API_TOKEN            # finn på en lang tilfeldig streng, f.eks. fra en passordgenerator
   npx wrangler deploy
   ```
   `deploy` skriver ut adressen, f.eks. `https://kronesporet-varsler.<konto>.workers.dev`.
4. Lim adressen inn i tråden. Da legger jeg den inn som `ALERTS_API` i `src/web/build.py`, og skjemaet og
   «Få varsel»-lenkene kommer på nettsiden.

**3. GitHub (5 min)**, under *Settings → Secrets and variables → Actions* i repoet:
- *Variables*: `VARSLER_API` = Worker-adressen fra steg 2.
- *Secrets*: `VARSLER_API_TOKEN` = samme streng som `API_TOKEN`, og `BREVO_API_KEY` = nøkkelen fra Brevo.

Da sender arbeidsflyten *Varsler* e-post hver morgen til dem som har noe nytt. Før dette er gjort, gjør den ingenting.

**4. Stripe (fra betalingsoppsettet i #39)**
1. Lag en Payment Link for abonnementet. Sett `STRIPE_PAYMENT_LINK` i `wrangler.toml` til lenken, og
   `STRIPE_PORTAL_URL` til kundeportalen.
2. Under *Developers → Webhooks*: legg til `https://<worker-adressen>/stripe/webhook` med hendelsene
   `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted` og `invoice.payment_failed`.
3. `npx wrangler secret put STRIPE_WEBHOOK_SECRET` med signeringsnøkkelen (`whsec_...`), og `npx wrangler deploy`.

Uten Stripe virker alt unntatt betalingen: bekreftede abonnenter får beskjed om at betalingen åpner snart, og
gratisabonnenter kan settes opp for hånd.

## Drift

Gratis tilgang for redaksjoner, forskere og offentlige virksomheter (etter at de har meldt seg på og bekreftet):
```
npx wrangler d1 execute kronesporet-varsler --remote \
  --command "UPDATE abonnenter SET status = 'gratis', gratis_grunn = 'redaksjon' WHERE email = 'navn@avis.no'"
```

Oversikt:
```
npx wrangler d1 execute kronesporet-varsler --remote --command "SELECT status, COUNT(*) FROM abonnenter GROUP BY 1"
```

Hva som sendes og når, står i `src/alerts/engine.py`. Kort fortalt: nye tildelinger siden forrige varsel, og avtaler
som har passert 3, 6 eller 12 måneder før sluttdatoen siden forrige varsel. Sluttdatoen er fra Doffin når den er
oppgitt, ellers et anslag fra varigheten i kunngjøringen, og det står i e-posten. Avtaler uten noen opplysning om
varighet er ikke med.
