# Personvern: databehandlere og behandlingsprotokoll

Sist oppdatert 08.11.2026. Den offentlige erklæringen står på
[kronesporet.no/vilkar.html#personvern](https://kronesporet.no/vilkar.html#personvern) og bygges fra
`privacy_section()` og `PROCESSORS` i `src/web/build.py`. Endres en leverandør eller en lagringstid, endres
erklæringen, denne fila og (for lagringstid) `RETENTION_DAYS` i `worker/src/index.js` samtidig.

## Behandlingsansvarlig

Brafaring Thevik (enkeltpersonforetak), org.nr. 938 405 565, Multeveien 1, 1712 Grålum.
Kontakt: personvern@kronesporet.no (videresendes til post@). Ikke personvernombud (ikke påkrevd).

## Databehandlere og underleverandører

| Leverandør | Hva den gjør | Persondata | Hvor | Avtale | Overføring ut av EØS |
|---|---|---|---|---|---|
| GitHub, Inc. (USA) | Nettsiden (GitHub Pages), daglig utsending av varsler (GitHub Actions, `varsler.yml`) | IP i serverlogg; e-post og utvalg til abonnenter i minnet under utsendingen | USA | [GitHub Data Protection Agreement](https://github.com/customer-terms/github-data-protection-agreement) og [GitHubs personvernerklæring](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement) | DPF + SCC |
| Cloudflare, Inc. (USA) | Worker `kronesporet-varsler` og D1-databasen | e-post, token, utvalg, status, Stripe-kunde-ID, logg over sendte varsler; IP til den som sender skjemaet | USA (D1 ble laget fra GitHub Actions uten stedsvalg) og Cloudflares nett | [Cloudflare Customer DPA](https://www.cloudflare.com/cloudflare-customer-dpa/), del av selvbetjeningsavtalen | DPF + SCC |
| Domene AS (Norge, org.nr. 880 478 982) | Webhotell (cPanel, bruker qkggefaf) og e-postserveren mail.kronesporet.no / loke.domene.no | alle e-poster inn og ut: bekreftelser, varsler, salgs- og pressee-post, henvendelser | Norge (IP-blokken er registrert på Domene.no i Norge) | Domene AS' kundevilkår | ingen |
| Stripe (Stripe Payments Europe, Limited, Irland) | Betaling, kundeportal, kvitteringer | navn, e-post, fakturaadresse, firmanavn, orgnr, kort | EØS og USA | [Stripe DPA](https://stripe.com/legal/dpa), del av Stripe Services Agreement. Stripe er også selvstendig behandlingsansvarlig for svindel og hvitvasking | DPF + SCC |

**Ikke i bruk:** Brevo. Koden støtter Brevo, men SMTP via Domene brukes når `SMTP_PASSWORD` finnes, og Worker-en
faller ikke tilbake til Brevo. Hemmeligheten `BREVO_API_KEY` kan slettes i GitHub. Tas Brevo i bruk, må den inn i
`PROCESSORS` og her.

## Behandlingsprotokoll (artikkel 30)

| Behandling | Registrerte | Opplysninger | Formål | Grunnlag | Lagringstid | Hvor |
|---|---|---|---|---|---|---|
| Nettsidebesøk | besøkende | IP, nettleser, side | drift og sikkerhet | 6.1 f | GitHubs regler | GitHub |
| Leverandørsider | innehavere av ENK | foretaksnavn, orgnr, kontrakter | åpenhet om offentlige kontrakter | 6.1 f | så lenge kontrakten er i dataene; skjules på forespørsel | GitHub (offentlig) |
| Påmelding | interessenter | e-post, utvalg, tidspunkter | bekrefte og sende til betaling | 6.1 b | 30 dager uten betaling (automatisk) | Cloudflare D1 |
| Abonnement | kunder | e-post, utvalg, status, logg | sende varsler | 6.1 b | 90 dager etter oppsigelse/avmelding (automatisk); logg 13 mnd | Cloudflare D1, GitHub Actions, Domene |
| Betaling og regnskap | kunder | navn, firma, adresse, orgnr, beløp | bokføring | 6.1 c, bokføringsloven § 13 | 5 år etter regnskapsåret | Stripe |
| Henvendelser | alle som skriver | e-post og innhold | svare | 6.1 f / b | 12 mnd etter avsluttet sak | Domene (post@) |
| Salg til bedrifter | kontaktpunkter i firmaer | firma, orgnr, firmaets e-post/telefon, kontrakter | tilby varsler | 6.1 f, markedsføringsloven § 15 (næringsdrivende, med reservasjon) | 12 mnd etter siste kontakt; «nei takk» = sperreliste | prosjektfiler (private CSV-er), Domene |

Ingen sensitive opplysninger (artikkel 9), ingen profilering eller automatiserte avgjørelser.

## Rutiner

- **Innsyn, retting, sletting:** svar innen én måned. Abonnenter: slett raden i D1-konsollen
  (`DELETE FROM utvalg/sendte_varsler/abonnenter WHERE email = '...'`) eller be dem bruke «Slett meg». Kunder i
  Stripe: kunden kan slettes i Stripe-dashbordet, men kvitteringer må beholdes i 5 år.
- **Skjul ENK:** legg orgnr i `site-data/skjulte_leverandorer.txt` (`scripts/export_site_data.py` viser det som «Enkeltpersonforetak (skjult)»).
- **Sperreliste for salg:** den som svarer «nei takk» eller «ikke send mer», skal markeres i salgslistene og ikke
  kontaktes igjen.
- **Én gang i året (januar):** slett e-poster i post@ om avsluttede saker eldre enn 12 måneder, og salgsrader uten
  kontakt på 12 måneder.
- **Avvik** (f.eks. lekkasje av abonnentlisten): meld til Datatilsynet innen 72 timer hvis det er risiko for de
  registrerte ([meld avvik](https://www.datatilsynet.no/om-datatilsynet/kontakt-oss/meld-avvik-til-datatilsynet/)).
