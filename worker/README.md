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

Alt annet gjør arbeidsflyten [*Worker*](https://github.com/Markemus-byte/Kronesporet/actions/workflows/worker.yml)
selv (`scripts/varsler_oppsett.py`): Cloudflare-konto-ID og workers.dev-underdomene, databasen, Worker-en, Brevo-domenet
og avsenderen varsler@kronesporet.no, DNS-postene for Brevo i cPanel, Stripe-webhooken og til slutt skjemaet på
nettsiden. Den kjører hver morgen til alt er på plass. Du trenger bare å lage tre nøkler og lime dem inn som
GitHub-secrets under [*Settings → Secrets and variables → Actions → New repository secret*](https://github.com/Markemus-byte/Kronesporet/settings/secrets/actions/new).
Lim aldri nøklene inn noe annet sted.

**1. Cloudflare (5 min)**
1. Lag en gratis konto på [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up) med post@kronesporet.no
   og bekreft e-posten. Domenet skal ikke flyttes dit; hopp over spørsmål om å legge til et nettsted.
2. Klikk personikonet øverst til høyre → *My Profile* → *API Tokens* → *Create Token*.
3. Ved *Edit Cloudflare Workers* klikker du *Use template*.
4. Under *Permissions* klikker du *+ Add more* og velger `Account` · `D1` · `Edit`.
5. *Account Resources*: `Include` · kontoen din. *Zone Resources*: `Include` · `All zones`.
6. *Continue to summary* → *Create Token* → *Copy*. Lagre som secret `CLOUDFLARE_API_TOKEN`.

**2. Brevo (3 min)**
1. I [app.brevo.com](https://app.brevo.com): klikk navnet ditt øverst til høyre → *SMTP & API* → fanen *API Keys*
   → *Generate a new API key*, navn `github` → *Generate* → kopier. Lagre som secret `BREVO_API_KEY`.
2. Klikk navnet ditt øverst til høyre → *Security* → *Authorised IPs*. Står blokkeringen på, slår du den av
   (*Deactivate blocking*). GitHub sender fra skiftende adresser, så ellers avviser Brevo nøkkelen.

**3. cPanel hos Domene.no (3 min)**, så DNS-postene for Brevo legges inn automatisk
1. Logg inn i cPanel → *Security* → *Manage API Tokens* → *Create*. Navn `github`, la utløpsdato stå tom → *Create*
   → kopier tokenet (vises bare én gang). Lagre som secret `CPANEL_TOKEN`.
2. Brukernavnet står i cPanel i høyre kolonne under *General Information → Current User*. Lagre det som
   secret `CPANEL_USER`.

Uten cPanel-nøkkel virker resten likevel, men da står DNS-postene for Brevo i oppsummeringen av kjøringen og må
legges inn for hånd i *Zone Editor*.

Når nøklene er lagt inn, kjører Worker-arbeidsflyten neste morgen, eller med en gang via *Run workflow*. Brevo kan
bruke opptil et døgn på å godkjenne domenet. Når domenet er godkjent, setter arbeidsflyten `ALERTS_API` i
`src/web/build.py` og bygger nettsiden på nytt.

`VARSLER_API_TOKEN` og `CLOUDFLARE_ACCOUNT_ID` trengs ikke lenger: tokenet mellom GitHub og Worker-en avledes av
`CLOUDFLARE_API_TOKEN`, og konto-ID-en hentes fra Cloudflare. Finnes de, brukes de.

**Senere, før ekte betalinger (fra betalingsoppsettet i #39):** live-nøkkelen fra Stripe som secret
`STRIPE_LIVE_KEY` når testen i sandkassen virker, og en advokat som ser over vilkårene og personvernerklæringen.

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
