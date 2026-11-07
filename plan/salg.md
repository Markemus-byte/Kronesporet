# Salg av varsler: første betalende kunder

Mål: de første kundene på varsler (490 kr i måneden eks. mva, 14 dager gratis) så fort som mulig, uten nye
funksjoner. Alt under kan sendes som det står.

## Advokat er ikke en blokkering

Start salget med vilkårene som ligger på nettsiden nå. De er vanlige standardvilkår for et abonnement mellom
bedrifter: salg bare til næringsdrivende (ingen angrerett), oppsigelse når som helst, og ansvaret er begrenset til det
kunden har betalt de siste 12 månedene. Det er nok til å ta imot de første kundene. Få en advokat til å lese
gjennom når det kommer inn noen tusen kroner i måneden, eller før den første store kunden ber om egen avtale.

## Hvem som får e-posten

`scripts/prospects.py` lager lista. Den rangerer leverandørene som vinner offentlige kontrakter etter

1. avtaler de har nå som løper ut de neste 12 månedene (da kommer ny konkurranse, og de må følge med),
2. hvor mange offentlige kontrakter de har vunnet de siste tre årene,
3. hvor mange ulike offentlige kjøpere de har vunnet hos.

Kontaktinformasjonen (e-post, telefon, nettside, daglig leder) er hentet fra Enhetsregisteret. Enkeltpersonforetak,
offentlige enheter, slettede foretak og foretak med over 1000 ansatte er tatt ut. For hvert prospekt lages også en
gratisliste over avtalene i bransjen deres (CPV-gruppene de oftest vinner i) som løper ut de neste 12 månedene.
Den er «gulroten» i e-posten.

Lista har navn og kontaktinformasjon og ligger derfor **ikke** i repoet (repoet er offentlig). Den ligger i
prosjektmappen: `kronesporet/salg/prospekter.csv` og `kronesporet/salg/gratislister/`.

```
python scripts/prospects.py --ut <privat mappe>/prospekter.csv --lister <privat mappe>/gratislister
```

Markedsføringsloven § 15 krever ikke samtykke for e-post til foretak, men e-posten må si hvem den er fra og gi en
enkel måte å si nei takk. Det står i malen. Send fra post@kronesporet.no, noen få om gangen, ikke som masseutsendelse.

## E-post (send som den står)

Felt i klammer fylles inn fra lista (`prospekter.csv`).

**Emne:** Avtalen deres med {neste_avtale_kjoper} løper ut {måned år}

> Hei {fornavn},
>
> Jeg heter Morten og driver Kronesporet.no, som følger alle offentlige kontrakter i Doffin fra 2017 til i dag.
>
> {navn} har vunnet {vunnet_siste_3_ar} offentlige kontrakter de siste tre årene. Etter det vi kan se, løper
> avtalen med {neste_avtale_kjoper} om «{neste_avtale_tittel}» ut rundt {neste_avtale_ut}. I bransjen deres løper
> {i_bransjen_ut_12_mnd} offentlige avtaler ut de neste 12 månedene.
>
> Kronesporet sier fra 12, 6 og 3 måneder før en offentlig avtale løper ut, altså før den nye konkurransen blir
> kunngjort. Det får du ikke fra Doffin. Du får også beskjed om nye tildelinger hos kjøperne du følger: hvem som
> vant, og for hvor mye.
>
> Vil du ha lista over de {i_bransjen_ut_12_mnd} avtalene gratis? Svar «ja», så sender jeg den som regneark.
>
> Vil du heller prøve varslene med en gang: de første 14 dagene er gratis, deretter 490 kr i måneden eks. mva, og
> du kan si opp når som helst. {STRIPE_LENKE}
>
> Vennlig hilsen
> Morten
> Kronesporet.no · post@kronesporet.no
>
> Du får denne e-posten fordi {navn} står som leverandør i offentlige kunngjøringer. Svar «nei takk», så hører du
> ikke mer fra meg.

Uten egen avtale som løper ut (`avtaler_ut_12_mnd` = 0) byttes avsnitt to ut med:

> {navn} har vunnet {vunnet_siste_3_ar} offentlige kontrakter hos {ulike_kjopere} ulike kjøpere. I bransjen deres
> løper {i_bransjen_ut_12_mnd} offentlige avtaler ut de neste 12 månedene, og noen av dem kommer ut på ny
> konkurranse snart.

Før betalingen er live: ta ut setningen med Stripe-lenken. «Svar ja» virker uansett.

**Når noen svarer «ja»:** send gratislista fra `kronesporet/salg/gratislister/` (filnavnet starter med rangen i
lista) og skriv: «Her er lista. Vil du ha slike varsler automatisk hver måned, er de første 14 dagene gratis:
{STRIPE_LENKE}».

## LinkedIn

**Melding til daglig leder eller salgsansvarlig** (kortere, samme innhold):

> Hei {fornavn}! Jeg har laget Kronesporet.no, som følger alle offentlige kontrakter i Doffin. Avtalen dere har med
> {neste_avtale_kjoper} løper etter det jeg ser ut i {måned år}, og {i_bransjen_ut_12_mnd} avtaler i
> bransjen deres løper ut de neste 12 månedene. Vil du ha lista gratis? Si fra, så sender jeg den.

**Innlegg** (ett, på profilen din):

> 616 offentlige avtaler til rundt 30 milliarder kroner løper ut de neste 12 månedene, hos 254 offentlige kjøpere.
> Hver av dem blir en ny konkurranse.
>
> De fleste leverandører får vite om konkurransen først når den kunngjøres i Doffin. Da er det ofte for sent å
> forberede et godt tilbud.
>
> Jeg har laget Kronesporet.no, som følger alle offentlige kontrakter fra 2017 til i dag, og nå også sier fra 12,
> 6 og 3 måneder før en avtale løper ut. Søk opp ditt eget firma på kronesporet.no og se hvilke avtaler dere har.
>
> Vil du ha lista over avtalene i din bransje som løper ut? Kommenter eller send meg en melding.

## Pressesaker (november 2026)

Pressen betaler ikke, men én sak i DN, E24 eller Kommunal Rapport sender leverandører til kronesporet.no. Redaksjoner
og forskere får varslene gratis (står i vilkårene). Tre saker er gravd fram fra dataene og etterprøvd mot Doffin og
statsregnskapet 7.11.2026. Ferdige e-poster med kopier-knapper ligger i artefakten «Kronesporets pressesaker»
(https://claude.ai/artifact/GqUusEN9hghUXjgdJ24Bbk). Hver sak går til én redaksjon først, med tilbud om å få den
alene til torsdag kl. 12. Svarer de ikke, går den til neste.

Datafilene journalistene får lenke til ligger i `docs/data/presse/` (kronesporet.no/data/presse/), og alle tallene
regnes ut på nytt med `scripts/presse_tall.py`. Tipsadressene er hentet fra redaksjonenes egne sider samme dag.

| Sak | Første redaksjon | Neste | Hovedtall | Kilde |
|---|---|---|---|---|
| 1. Private barnevernstjenester | tips@dn.no | tips@aftenposten.no, 03030@nrk.no | 12,49 mrd. kr 2023–2025 (+40 %); 541 Doffin-kunngjøringer fra Bufdir/Bufetat siden 2017, ingen konkurranse om institusjons- eller fosterhjemsplasser; fire tildelinger av private omsorgstjenester, 335 mill. kr | Statsregnskapet kap. 0855 post 22; Doffin; kronesporet.no/virksomhet/986128433.html |
| 2. Nordland fylkeskommune | redaksjon@kommunal-rapport.no | 03030@nrk.no | 311 konkurranser 2023–2025 (150 over EØS-terskelen), 9 tildelinger; 3 tildelinger per 100 konkurranser mot 32 i hele Doffin; kontrakter på 285–700 mill. kr uten kunngjort vinner | Doffin; kronesporet.no/kommune/964982953.html |
| 3. UDI og asylmottak | tips@e24.no | tips@dn.no | 8,73 mrd. kr 2023–2025; vinneren kunngjort i 2 av 18 mottakskonkurranser 2023–2024; 31 229 konkurranser og 10 064 tildelinger i hele Doffin 2023–2025 | Statsregnskapet kap. 0490 post 21; Doffin; kronesporet.no/virksomhet/974760746.html |

Forbehold som står i e-postene: barnevernstjenester har egne regler og enkeltkjøp kan være lovlig uten kunngjøring;
vinnerne kan være kjent andre steder enn Doffin (Torghatten Nord vant Festvåg–Misten ifølge fagpressen); UDI viser
selv hvem som driver mottakene. Sakene påstår ikke lovbrudd, de spør.

Lagt bort: Hovedredningssentralen, fordi redningshelikopteravtalene ble kunngjort av Justisdepartementet (Doffin
2020-383463, 2023-603960). Flagget er rettet i PR #67 («Overtatt budsjettkapittel»), og etter det har 2025 ingen
dekningsflagg. Pressesakene viser ikke til flagg. Kommuner med null kontrakter
(Eidsvoll, Nannestad) kjøper gjennom Øvre Romerike Innkjøpssamarbeid. Forsvaret og UD har unntak og kjøp i utlandet.

Etterprøving:

```
python scripts/presse_tall.py barnevern --raadata /mnt/project-files/kronesporet/data/statsregnskap_raadata
python scripts/presse_tall.py kjoper --navn "Nordland fylkeskommune" --orgnr 964982953
python scripts/presse_tall.py landet --ar 2023,2024,2025 --ut landet.csv   # rundt en time
```

### Generell pitch (reserve)

Brukes hvis en redaksjon vil ha selve verktøyet heller enn en enkeltsak.

> **Emne: Nytt verktøy: hvor mye av statens innkjøp som faktisk kunngjøres**
>
> Kronesporet.no kobler for første gang alle kontraktene i Doffin fra 2017 til i dag (30 867 kontrakter og 7 378
> leverandører) mot statsregnskapet for 128 statlige virksomheter. For en typisk statlig virksomhet tilsvarer
> kontraktene som er kunngjort i Doffin bare rundt 13 prosent av det den kjøper varer og tjenester for (median 2025).
> Noe av forskjellen har gode grunner, som kjøp under terskelverdien og felles rammeavtaler, og nettsiden viser hvem
> som ligger langt under lignende virksomheter. Neste 12 måneder løper 616 offentlige avtaler til rundt 30
> milliarder kroner ut. Alt er åpent og gratis å søke i på kronesporet.no, og dataene bygger på åpne kilder (Doffin,
> DFØ og Brønnøysundregistrene). Jeg stiller gjerne opp, viser tallene og lager uttrekk for deres saker.
>
> Morten, Kronesporet.no · post@kronesporet.no

Tallene er fra dataene per 31.10.2026. Avtaler som løper ut: sluttdato fra Doffin eller anslag fra oppgitt varighet.
Dekningen: `dekning_2025_statsregnskap.csv` i prosjektmappen.

## Den ene sjekklista for å ta imot betaling

Varslene er i drift: påmeldingen på kronesporet.no/varsler.html virker, og utsendingen går fra
utsending@kronesporet.no på Domene.no-serveren (Cloudflare- og cPanel-nøklene er lagt inn, 02.11.2026).
Med 14 dager gratis trengs betalingen først når den første kunden har prøvd.

Det eneste som står igjen:

1. **Stripe live** (5 min): i Stripe, slå av *Test mode*/sandkassen, fullfør det Stripe ber om for å aktivere
   betalinger (bankkonto og legitimasjon). Under *Developers → API keys*, kopier *Secret key* (`sk_live_...`) og
   lagre den som **STRIPE_LIVE_KEY** på
   [Settings → Secrets → New repository secret](https://github.com/Markemus-byte/Kronesporet/settings/secrets/actions/new).

Resten gjøres fra GitHub Actions: *Stripe-oppsett* i modus `live` lager den ekte betalingslenken (med 14 dager
gratis) og kundeportalen, og live-lenkene og live-webhooken settes inn i Worker-en.
