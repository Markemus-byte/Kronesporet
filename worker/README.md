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

**2. Cloudflare-konto og API-token (10 min)**
1. Lag en gratis konto på [dash.cloudflare.com](https://dash.cloudflare.com). Domenet trenger ikke flyttes dit.
2. Gå til *Workers & Pages* og velg et workers.dev-underdomene når du blir spurt (f.eks. `kronesporet`).
3. Kopier *Account ID* fra høyre kolonne på oversikten (eller fra *Workers & Pages*).
4. Gå til *My Profile → API Tokens → Create Token*, velg malen *Edit Cloudflare Workers*, legg til tillatelsen
   *Account → D1 → Edit*, og lag tokenet. Det vises bare én gang.

**3. GitHub (5 min)**, under [*Settings → Secrets and variables → Actions*](https://github.com/Markemus-byte/Kronesporet/settings/secrets/actions)
→ *New repository secret*:
- `CLOUDFLARE_API_TOKEN` = tokenet fra steg 2
- `CLOUDFLARE_ACCOUNT_ID` = Account ID fra steg 2
- `BREVO_API_KEY` = nøkkelen fra Brevo
- `VARSLER_API_TOKEN` = en lang tilfeldig streng du finner på (f.eks. fra en passordgenerator). Den brukes bare
  mellom GitHub og Worker-en, og du trenger aldri å skrive den inn andre steder.

Kjør så arbeidsflyten [*Worker*](https://github.com/Markemus-byte/Kronesporet/actions/workflows/worker.yml)
(*Run workflow*). Den lager databasen, legger inn tabellene, publiserer Worker-en og legger inn hemmelighetene.
Adressen til Worker-en står i oppsummeringen av kjøringen. Når den legges inn som `ALERTS_API` i
`src/web/build.py`, kommer skjemaet og «Få varsel»-lenkene på nettsiden, og arbeidsflyten *Varsler* begynner å
sende. Før det gjør den ingenting.

**4. Stripe (fra betalingsoppsettet i #39)**
1. Arbeidsflyten *Stripe-oppsett* (`scripts/stripe_setup.py`, nøkkel i GitHub-secret `STRIPE_SANDBOX_KEY`) lager
   produktet, prisen, betalingslenken og kundeportalen. Lenkene står i oppsummeringen av kjøringen; de settes som
   `STRIPE_PAYMENT_LINK` og `STRIPE_PORTAL_URL` i `wrangler.toml`. Betalingslenken sender kunden tilbake til
   `varsler.html#takk`.
2. Kjør *Worker* manuelt med *Stripe-webhook* huket av. Den lager webhook-endepunktet
   `https://<worker-adressen>/stripe/webhook` med de fem hendelsene Worker-en bruker
   (`checkout.session.completed`, `customer.subscription.created/updated/deleted`, `invoice.payment_failed`) og
   legger signeringshemmeligheten rett inn i Worker-en. Den vises ingen steder. Kjør det bare når webhooken
   mangler, for endepunktet lages på nytt med ny hemmelighet hver gang.

Uten Stripe virker alt unntatt betalingen: bekreftede abonnenter får beskjed om at betalingen åpner snart, og
gratisabonnenter kan settes opp for hånd.

## Drift

Kommandoene under kjøres i D1-konsollen i Cloudflare (*Storage & Databases → D1 → kronesporet-varsler → Console*)
uten `npx wrangler ...`-delen, eller med wrangler på en maskin med Node.js.

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
