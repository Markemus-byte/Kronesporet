# Sjekkliste før betalte varsler (for Morten)

Dette er de fire punktene i #39 som bare du kan gjøre. Resten (Stripe-oppsett, vilkår og koden) ligger i
[betaling-stripe.md](betaling-stripe.md) og `src/web/build.py`. Dette er ikke juridisk rådgivning; sjekk
punktene mot [Altinn: Starte og drive bedrift](https://info.altinn.no/starte-og-drive/) og en advokat.

## 1. Selskapsform: ENK eller AS

| | Enkeltpersonforetak (ENK) | Aksjeselskap (AS) |
|---|---|---|
| Oppstart | Gratis i Enhetsregisteret, gjøres på en kveld i Altinn | Minst 30 000 kr i aksjekapital og gebyr til Foretaksregisteret |
| Ansvar | Du hefter personlig for all gjeld og alle krav | Ansvaret er begrenset til selskapets midler |
| Skatt | Overskuddet skattes som din personinntekt | Selskapsskatt, og utbytte skattes igjen hos deg |
| Regnskap | Næringsoppgave med skattemeldingen | Årsregnskap til Regnskapsregisteret, aksjonærregisteroppgave |
| Adresse på nettsiden | Ofte hjemmeadressen din | Selskapets adresse |

**Valgt (28.10.2026):** Morten selger varslene gjennom enkeltpersonforetaket han alt har, og vurderer AS når
inntektene er der (rundt 30 000 kr).

- [ ] Sjekk at næringskoden og formålet til ENK-et dekker salg av informasjonstjenester, og endre det i
  [Samordnet registermelding](https://info.altinn.no/skjemaoversikt/bronnoysundregistrene/samordnet-registermelding---enhetsregisteret-og-foretaksregisteret/)
  hvis ikke (gratis)
- [ ] Bruk en egen bankkonto for ENK-et til utbetalingene fra Stripe, så salget er lett å skille ut
- [ ] Gi meg navnet på ENK-et, orgnr og forretningsadressen, så kommer de inn i vilkårene og bunnteksten (`SELLER` i
  `src/web/build.py`). Ehandelsloven krever en geografisk adresse; for et ENK er det som regel adressen i
  Enhetsregisteret

### Stripe på ENK-et

Velg *Enkeltpersonforetak* (sole proprietorship) som virksomhetstype og oppgi orgnr. Stripe ber om
legitimasjon for deg som innehaver og om bankkontoen for utbetalinger. Sett offentlig navn til «Kronesporet»,
teksten på kontoutskriften til `KRONESPORET` og support-adressen til post@kronesporet.no, så ser kundene
Kronesporet og ikke navnet ditt.

### Når du bytter til AS

- Et AS er et nytt rettssubjekt med nytt orgnr. Det trenger som regel en ny Stripe-konto. Stripe kan kopiere
  kunder og lagrede kort til den nye kontoen etter søknad til support, men abonnementene må opprettes på nytt der.
  Gjør byttet ved et månedsskifte.
- Vilkårene sier at abonnementet kan overdras til et selskap som Kronesporets innehaver eier, så kundene trenger
  ikke å samtykke på nytt, men de skal ha beskjed på e-post minst 30 dager før.
- Bytt `SELLER` i `src/web/build.py`, webhook-hemmeligheten og Payment Link i Workeren.
- AS-et må mva-registreres for seg når salget i AS-et passerer 50 000 kr. Salg i ENK-et teller ikke med der.
- Brevo, Cloudflare, Stripe og domenet bør stå på post@kronesporet.no fra start, så kontoene kan følge med.

## 2. Mva

- Du **må** registrere deg i Merverdiavgiftsregisteret når salget passerer **50 000 kr i løpet av 12 måneder**.
  Før det tar du ikke mva.
- Grensen gjelder hele ENK-et. Har det annet mva-pliktig salg, teller det med. Er ENK-et alt mva-registrert, skal
  det legges mva på varslene fra første krone.
- 30 000 kr har ingenting med mva å gjøre; det er minste aksjekapital for et AS.
- Etter registreringen legger du på 25 % mva. Prisene til bedrifter vises eks. mva, så prisen på nettsiden kan
  stå som den er.
- Hold øye med salget i Stripe-dashbordet. Med 490 kr i måneden passeres grensen ved rundt 9 betalende kunder i et
  helt år.

- [ ] Registrer mva når grensen nærmer seg, og slå på mva i Stripe (Settings → Tax) samme dag

## 3. Brukervilkår for den betalte delen

Utkastet ligger i `src/web/build.py` (`paid_terms_section()`). Det vises på `vilkar.html` først når pris og
selger er fylt inn, så nettsiden er uendret til da. Hovedpunktene:

- abonnementet selges bare til **næringsdrivende** med orgnr. Da gjelder ikke angrerettloven og digitalytelsesloven,
  og vilkårene blir mye enklere. Redaksjoner, forskere og offentlige virksomheter søker om gratis tilgang
- månedlig forskuddsbetaling via Stripe, oppsigelse når som helst med virkning fra neste periode
- prisendringer varsles minst 30 dager før
- varslene bygger på anslag (løpetid, verdier), og ansvaret er begrenset til det kunden har betalt de siste 12
  månedene
- norsk rett, og tvister løses ved selgers verneting
- personvern: Stripe, Cloudflare og e-posttjenesten er databehandlere eller selvstendige behandlingsansvarlige,
  regnskapsdata lagres i 5 år etter bokføringsloven

- [ ] Les utkastet og si fra om noe skal endres
- [ ] Bestem prisen (forslag 490 kr i måneden eks. mva, 4 900 kr for et år)

## 4. Juridisk gjennomgang

Be en advokat med e-handel og personvern om en fastpris-gjennomgang av:

- [ ] brukervilkårene for abonnementet (punkt 3)
- [ ] personvernerklæringen på `vilkar.html`, særlig behandling av abonnenter og enkeltpersonforetak blant
  leverandørene
- [ ] at opplysningene ehandelsloven § 8 krever (navn, geografisk adresse, orgnr, e-post og eventuelt mva-nummer)
  står på nettsiden
- [ ] at Kronesporet kan ta betalt for varsler bygget på NLOD-data (NLOD tillater kommersiell bruk med
  kildehenvisning, men be advokaten bekrefte at vilkårene ikke begrenser selve kildedataene)
- [ ] databehandleravtalene med Stripe, Cloudflare og e-posttjenesten (de er som regel en del av standardvilkårene
  deres, men det bør stå i personvernerklæringen hvem som behandler hva)

## Rekkefølge

1. ENK-et klart: næringskode, bankkonto og selgeropplysninger (punkt 1)
2. Stripe-konto i testmodus og pris (betaling-stripe.md)
3. Advokat ser på vilkårene mens varslene fra #38 testes
4. Fyll inn `SELLER`, `PAID_ALERTS_PRICE` og lenkene i `src/web/build.py`, slå på Stripe live, og lanser
5. Mva-registrering når salget nærmer seg 50 000 kr
