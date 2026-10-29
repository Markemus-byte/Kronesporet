# Betaling for varsler: Stripe på en statisk side

Vurdering for #39. Varslene selv bygges i #38 (Cloudflare Worker med D1 og en e-posttjeneste).
Nettsiden er fortsatt statiske filer på GitHub Pages, og den forblir gratis.

## Kort anbefaling

1. **Betaling:** en Stripe **Payment Link** for månedsabonnementet. Det er en ferdig betalingsside hos Stripe,
   og knappen på `varsler.html` er bare en lenke. Ingen kode og ingen server trengs for å ta betalt.
2. **Oppsigelse og kort:** Stripes **kundeportal** (Customer Portal) med innlogging via e-post. Også bare en lenke.
3. **Hvem som har betalt:** en **webhook** fra Stripe til en egen rute på den samme Cloudflare Workeren som #38
   bruker, `POST /stripe/webhook`. Den oppdaterer en tabell over abonnenter i D1, og varselmotoren sender bare
   til dem som er aktive eller har gratis tilgang.
4. **Gratis tilgang** for redaksjoner, forskere og offentlige virksomheter settes manuelt i D1 etter søknad på
   post@kronesporet.no. De går ikke via Stripe.

Dette koster ingenting i drift utover Stripe-gebyrene: Cloudflare Workers og D1 har gratisnivå som holder lenge
(i dag 100 000 kall i døgnet og 5 GB i D1).

## Alternativene

| Valg | Hva det er | Fordeler | Ulemper |
|---|---|---|---|
| **Payment Link** (anbefalt først) | Ferdig betalingsside hos Stripe, lages i dashbordet | Ingen kode. Kan ta imot orgnr og firmanavn i egne felt. E-post kan fylles inn via `?prefilled_email=` | Lite styring av flyten. Én lenke per pris |
| **Checkout Session** via Worker | Workeren lager en betalingsøkt per kunde | Pris, valuta og innhold kan settes per kunde | Trenger en rute til (`POST /stripe/checkout`) og hemmelig nøkkel i Workeren |
| **Ingen webhook**, varseljobben spør Stripe | Jobben henter aktive abonnementer fra Stripe-API-et før hver utsending | Ingen ny rute i Workeren | Ny kunde får ikke bekreftelse før neste kjøring. Varseljobben blir avhengig av Stripe |
| Netlify Functions | `netlify.toml` finnes alt | Kjent oppsett | Nettsiden ligger på GitHub Pages, så det blir en ekstra tjeneste ved siden av Cloudflare |

Payment Link holder: følge-listen velges i skjemaet på `varsler.html` før betalingen (se flyten under), og
lenken får kundens token med seg. Prøveperiode og rabattkoder kan også slås på i en Payment Link. Checkout Session trengs først hvis prisen skal settes per kunde.

## Flyten (avtalt med #38)

Worker-koden ligger i `worker/` og skrives i #38, også webhook-ruten, så filen har én eier.

1. Kunden fyller ut skjemaet på `varsler.html`. Workeren lagrer raden med status `ny` og sender en
   bekreftelse på e-post.
2. Bekreft-lenken setter tidsstempelet `bekreftet` og sender kunden videre til Payment Link med
   `?prefilled_email=<e-post>&client_reference_id=<token>`. Stripe godtar bare bokstaver, tall, `-` og `_` i
   `client_reference_id`, så den er raden sitt token, ikke e-posten.
3. Stripe sender hendelser til `POST /stripe/webhook`. Workeren verifiserer signaturen i `Stripe-Signature` med
   `STRIPE_WEBHOOK_SECRET` (Worker-hemmelighet, aldri i repoet) og avviser alt annet.

| Hendelse | Ny status |
|---|---|
| `checkout.session.completed` | `active` |
| `customer.subscription.updated` (active eller trialing) | `active` |
| `invoice.payment_failed` | `past_due` |
| `customer.subscription.deleted` | `canceled` |

D1-tabellen `abonnenter` har `email` som nøkkel, og ellers blant annet `stripe_customer_id`, `status`
som bare er betalingsstatus (`ny`, `active`, `past_due`, `canceled`, `gratis`), tidsstemplene `bekreftet` og
`avmeldt`, `plan` (`standard`, eventuelt `aar` for
årspris), `gratis_grunn` (redaksjon, forskning, offentlig), `token` og `oppdatert`. Følge-listene ligger i
`utvalg`.

Varselmotoren sender bare når `bekreftet` er satt, `avmeldt` er tom og `status` er `active` eller `gratis`.
Avmelding stopper e-postene uten å røre Stripe; avmeldingssiden lenker til kundeportalen for å si opp betalingen. Webhooken må svare 200 raskt og tåle samme hendelse
to ganger, for Stripe prøver på nytt i opptil tre dager hvis svaret uteblir.

E-postadresser og følge-lister ligger bare i D1 og Stripe. Repoet er offentlig, så ingen abonnentdata skal dit.

## Gebyrer

Stripe tar 2,4 % + 2 kr per betaling med kort fra EØS, og Billing (abonnement) 0,7 % i tillegg (tallene fra #39;
sjekk [stripe.com/no/pricing](https://stripe.com/no/pricing) før lansering). Kort fra utenfor EØS koster mer.

| Pris per måned | Gebyr | Du sitter igjen med | Andel |
|---|---|---|---|
| 300 kr | 11,30 kr | 288,70 kr | 3,8 % |
| 490 kr | 17,19 kr | 472,81 kr | 3,5 % |
| 800 kr | 26,80 kr | 773,20 kr | 3,4 % |

Etter mva-registrering kommer 25 % mva på toppen, og gebyret regnes av beløpet med mva.

## Pris

#39 foreslår 300–800 kr i måneden per bruker, og prisen skal testes. Valgt 29.10.2026: **490 kr i måneden
eks. mva**. Et årsabonnement til 4 900 kr (to måneder gratis) kan legges til senere. Det er lett å senke prisen for de neste
kundene, men vanskelig å heve den for dem som alt betaler. Stripe lar eksisterende kunder beholde gammel pris
når en ny pris legges til.

## Hva som må gjøres i Stripe (Morten)

1. Opprett konto på stripe.com med selskapet (ENK eller AS, se [sjekklisten](sjekkliste-lansering.md)) og
   bankkontoen til selskapet.
2. Lag produktet «Kronesporet varsler» med en månedspris (og eventuelt en årspris).
3. Lag en Payment Link for prisen. Slå på «Collect tax ID» eller egne felt for firmanavn og orgnr, og sett
   bekreftelsessiden til `https://kronesporet.no/varsler.html#takk`.
4. Slå på kundeportalen (Settings → Billing → Customer portal) med oppsigelse ved periodens slutt.
5. Legg inn webhooken (`https://<worker>/stripe/webhook`, de fire hendelsene over) når Workeren fra #38 er ute.
   Signeringshemmeligheten legges inn med `wrangler secret put STRIPE_WEBHOOK_SECRET`, og Payment Link-adressen
   som `STRIPE_PAYMENT_LINK`.
6. Test hele flyten i testmodus med kort 4242 4242 4242 4242 før lenken legges på nettsiden.

Lenken til kundeportalen og selgeropplysningene legges som konstanter øverst i `src/web/build.py`
(`STRIPE_PORTAL_URL`, `SELLER`, `PAID_ALERTS_PRICE`), slik e-postene er. Selger og pris er fylt inn; vilkårene
vises når `STRIPE_PORTAL_URL` også er satt. Payment Link brukes bare av Workeren.

## Senere

- **Faktura:** noen bedrifter vil heller ha faktura enn kort. Stripe Invoicing kan sende faktura med
  betalingslenke. EHF (elektronisk faktura til offentlige) trengs ikke så lenge offentlige virksomheter får
  varslene gratis.
- **Vipps:** mange i Norge forventer Vipps. Det er mindre viktig når kundene er bedrifter som betaler med
  firmakort.
