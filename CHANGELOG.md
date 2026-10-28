# Endringslogg

## Ikke utgitt

- **Betaling for varsler (#39), forberedt:** `plan/betaling-stripe.md` vurderer Stripe på den statiske siden
  (Payment Link, kundeportal og webhook til Workeren fra #38), og `plan/sjekkliste-lansering.md` er sjekklisten for
  selskapsform, mva, vilkår og juridisk gjennomgang. Vilkårene for abonnementet og personvern for abonnenter ligger
  i `src/web/build.py` og vises på `vilkar.html` først når `SELLER` og `PAID_ALERTS_PRICE` er fylt inn. Nettsiden
  er uendret til da.
- **E-post:** `varsler.html` har knappen «Meld interesse på e-post» til varsler@kronesporet.no (#37). Bunnteksten
  viser post@kronesporet.no. `vilkar.html` har personvern@kronesporet.no for personvern og ønsker om å skjule
  enkeltpersonforetak, post@ for feil i tallene og presse@kronesporet.no for presse.
- **Kjøpere uten orgnr:** noen kunngjøringer i Doffin, mest fra 2017–2022, har kjøpere uten orgnr. 1 192 slike
  kjøpere kjennes nå igjen på navnet, eller navnet foran «v/» eller komma («Oslo kommune v/ Bymiljøetaten»), ut fra
  navnene i tidligere kunngjøringer og registrene. Navn som har vært brukt av flere orgnr (som Sande kommune), er
  ikke med. Det gir 98 nye kunngjøringer (blant annet Helse Sør-Øst, departementer og kommuner i Finnmark), og
  kommuner og virksomheter som var medkjøpere i felles anskaffelser, får dem på sidene sine.
- **Nedlagte IKS og kommunale foretak:** 16 nedlagte interkommunale selskaper og kommunale foretak (som ÅRIM, Viva
  IKS og Nedre Romerike vannverk) er med blant de kommunale selskapene. De har mistet sektorkoden i
  Enhetsregisteret og finnes ut fra organisasjonsformen. Til sammen 151 nye kontrakter (30 867 i alt).
- **Stedsnavn i selskapsnavn:** navn som kommer med store bokstaver fra Enhetsregisteret, får stor forbokstav på
  steder og regioner («Asker og Bærum vannverk IKS», «Nord-Gudbrandsdal renovasjonsselskap IKS»). 76 navn er rettet.
- **Viken, Vestfold og Telemark og Troms og Finnmark:** fylkeskommunene som ble delt i 2024, er med for
  2020–2023 med KOSTRA-tallene og kontraktene sine (342 nye kontrakter, 30 716 i alt). Kunngjøringer fra
  fylkeskommunene før 2020 som ble en del av dem (som Akershus, Buskerud og Østfold), regnes nå til dem i stedet
  for til de nye fylkeskommunene fra 2024. Alle fylkeskommunene har dermed tall for 2020–2023. Hver av dem har en
  egen side med en merknad om delingen, og `kommuner.html` har en ny tabell over dem. Dekningen for alle kommunene
  og fylkeskommunene i 2020 er 12,2 % (var 12,8 %), fordi de delte fylkeskommunene har få kontrakter fra før 2020 i
  Doffin-tallene. `kommuner` har en ny kolonne `until_year`.
- **Kommunale og interkommunale selskaper:** 352 selskaper som kommunene eier, alene eller sammen (som Ruter,
  Kolumbus, BIR, ROAF, IVAR og Sirkula), er med. De finnes blant kjøperne i Doffin ut fra sektorene for kommunale
  foretak, kommunalt eide aksjeselskaper og kommuneforvaltningen i Enhetsregisteret, på samme måte som de statlige
  selskapene, og 2 286 nye kontrakter for 2017–2026 er hentet (30 374 i alt). Ny side `kommunale-selskaper.html`,
  linket fra `selskaper.html`, `kommuner.html` og forsiden. De regnes ikke til én kommune, fordi mange har flere
  eiere. `selskaper.csv` har en ny kolonne for eier (staten eller kommunene).
- **Statlige selskaper:** 65 statsforetak, statlig eide selskaper og andre statlige enheter utenfor
  statsregnskapet (som Bane NOR, Avinor, Nye Veier, NRK, Norsk Helsenett, Vinmonopolet og Norges Bank) er med.
  De finnes blant kjøperne i Doffin ut fra institusjonell sektor i Enhetsregisteret (`src/collectors/selskaper.py`),
  så nye kjøpere kommer med av seg selv. 2 430 nye kontrakter for 2017–2026 (28 088 i alt). Ny side
  `selskaper.html` i menyen og én side per selskap, med driftskostnader utenom lønn fra Regnskapsregisteret som
  sammenligningstall og uten flagg. Kontraktene er med på leverandørsidene, i `kontrakter.csv` og i den nye
  `selskaper.csv`, og selskapene teller med blant de statlige kjøperne på forsiden.
- **Tidligere kommuner og fylkeskommuner:** kunngjøringer fra kommuner og fylkeskommuner som er slått sammen
  (som Skedsmo, nå Lillestrøm, og Hordaland, nå Vestland), og fra nedlagte kommunale foretak og etater (som
  Omsorgsbygg Oslo KF og Renovasjonsetaten i Oslo), regnes nå til kommunen de ble en del av. 90 slike enheter er
  koblet ut fra navnet i Enhetsregisteret og SSBs kommuneendringer (KLASS 131 og 104), og 1 423 nye kontrakter er
  hentet (25 658 i alt, 6 549 leverandører). Dekningen for kommunene øker mest i de første årene (2020: 9,2 % til
  12,8 %; 2025: 18,3 % til 18,6 %). Ny valgfri `--recheck-buyers` i `update_site_data.py` slår opp igjen kjøpere som er sjekket før.
- **KOSTRA 2020–2023:** kommunene har nå kjøpstall for seks år (2020–2025) i stedet for to. Kommuner som fikk nytt
  nummer i 2024 (som Halden, 3001 til 3101), kobles til de gamle numrene med SSBs klassifikasjon (KLASS 131). 355
  kommuner og 7 fylkeskommuner har tall for 2020–2023; Ålesund og Haram (delt i 2024) og de sju nye
  fylkeskommunene har tall fra 2024. Utviklingstabellen på kommunesidene og på `kommuner.html` går tilbake til
  2020, og 2023 er sammenligningsår for 2024, så 7 kommuner har uvanlig dekning to år på rad i 2024 (8 i 2025).
- **Kommuner og fylkeskommuner:** alle 357 kommuner og 14 fylkeskommuner er med. Kjøpene hentes fra KOSTRA (SSB)
  for 2024 og 2025 (345 og 352 mrd. kr), og 11 755 nye Doffin-kontrakter for 2017–2026 er hentet (24 235 i alt,
  6 253 leverandører). 128 etater, bydeler og kommunale foretak som kunngjør med eget orgnr, er funnet ved å slå opp
  kjøperne oppover i Enhetsregisteret, og de regnes til kommunen sin. Ny side `kommuner.html` i menyen med søk og
  filter, og én side per kommune. Dekningen sammenlignes med kommuner av lignende størrelse (tredeling etter kjøp;
  fylkeskommunene er én gruppe). Avvik over z = 2 merkes, og avvik to år på rad merkes som uvanlig (8 kommuner i
  2025). Dekning over 100 % merkes som «avtaler for andre» (vertskommuner i innkjøpssamarbeid). Kontraktene er også
  med på leverandørsidene, i `kontrakter.csv` og i den nye `kommuner.csv`. Forsiden har KOSTRA som sjuende kilde.
- **Helseforetakene:** de fire regionale helseforetakene og 32 helseforetak (`site-data/helseforetak.csv`) er
  med. De er statlige, men ikke i statsregnskapet. 1 221 sykehus og avdelinger fra Enhetsregisteret regnes til
  foretaket sitt, og 2 158 nye Doffin-kontrakter for 2017–2026 er hentet (12 480 i alt). Årsregnskapene for
  2023–2025 hentes fra Regnskapsregisteret, og driftskostnader utenom lønn brukes som sammenligningstall, uten
  flagg. Ny side `helseforetak.html` i menyen og én side per foretak. Kontraktene er også med på
  leverandørsidene, i `kontrakter.csv` og i den nye `helseforetak.csv`. Helse Nord-Trøndelag HF har 2023-tallene
  i feil enhet i Regnskapsregisteret, og slike år deles nå på 1000.
- **Varsler (planlagt):** ny side `varsler.html` i menyen beskriver de planlagte e-postvarslene (nye tildelinger,
  avtaler som løper ut, fullt uttrekk) med interessepåmelding, og viser de 20 største av avtalene som kan løpe ut
  de neste 12 månedene (156 per 23.10.2026, bare der Doffin har sluttdato eller varighet). Påmeldingen bruker
  `CONTACT_EMAIL` i `src/web/build.py` og står som «åpner snart» til adressen er satt. Menyen brytes nå pent på
  smale skjermer.
- **Rettinger av kjente feil i Doffin:** `site-data/rettinger.csv` (kontrakt, felt, verdi, begrunnelse) brukes av
  den månedlige oppdateringen. Første retting gjelder en kunngjøring fra Sykehusinnkjøp HF (315 mill. kr) som står med
  orgnr til Indre og Østre Finnmark tingrett og derfor ble regnet til Domstolene. Domstolenes Doffin-sum for 2025 går
  fra 375 til 217 mill. kr. Metodesiden lenker til listen.
- **Vilkår og personvern:** ny side `vilkar.html`, lenket fra bunnteksten. Den beskriver hvem som står bak, bruk
  av tallene under NLOD 2.0, forbehold, hvordan man melder feil, og personvern. Enkeltpersonforetak kan be om å bli
  skjult: orgnr i `site-data/skjulte_leverandorer.txt` vises som «Enkeltpersonforetak (skjult)» uten orgnr og
  leverandørside.
- **Felles anskaffelser:** alle kjøperne i en kunngjøring lagres nå (`doffin_buyers`), ikke bare den første.
  145 kunngjøringer har flere statlige kjøpere. Virksomhetssidene viser dem under «Felles anskaffelser» der
  virksomheten ikke står først (81 virksomheter, f.eks. 16 for Tolletaten og 45 for PST), og listen over
  virksomheter uten egne kontrakter viser hvor mange felles anskaffelser de er med i. Verdien regnes fortsatt
  bare hos den første kjøperen. Den månedlige oppdateringen tar med kunngjøringer der en av virksomhetene er
  blant kjøperne, og en gjennomgang av 2017–2026 fant 85 kunngjøringer som manglet, hvorav 37 med en statlig
  virksomhet som første kjøper.
- **Hele regnskapet er med:** årssidene har en ny liste over virksomhetene som har kjøp i statsregnskapet, men
  ingen Doffin-kontrakter som løper i året (49 virksomheter og 59,4 mrd. kr i 2025, hvorav SDØE 57 mrd. kr).
  Listen viser kjøpene, hvor mange kunngjøringer virksomheten har hatt totalt, og når den siste var, med
  merknad der forklaringen er kjent. Nøkkeltallet øverst viser nå «129 av 178 virksomheter i regnskapet har
  Doffin-kontrakter i året». `site-data/` tar med regnskapet og kontraktene for alle virksomhetene i regnskapet.
- **Nasjonalbiblioteket** har nå regnskapstall fra statsregnskapet for alle år, også 2026. Rådata fører
  biblioteket på en avdeling (979530730), mens Doffin og årsrapportene bruker hovedenheten (976029100).
  Siden brukte derfor årsrapporttall, og for 2025 var det feil: 34,8 mill. kr mot 443,5 mill. kr i
  regnskapet. Orgnr i rådata som er underenheter, kobles nå til hovedenheten via Enhetsregisteret.
- **Tolletaten** er med for 2025 og 2026, og kontraktene for 2023 og 2024 er riktige. Tolletaten kunngjør
  på 880455702 «TOLLETATEN», som er det overordnede leddet over regnskapsvirksomheten (974761343) og
  ligger under Finansdepartementet. De 36 kunngjøringene ble derfor ført på departementet. Et overordnet
  ledd med samme navn som virksomheten føres nå på virksomheten. Finansdepartementet får riktige tall og
  har ikke lenger dekning over 100 % i 2023 og 2026.
- **Månedlig oppdatering:** GitHub Actions henter nye Doffin-kontrakter den 20. hver måned, tar inn
  rådatafiler fra statsregnskapet som er lastet opp i `site-data/raadata/`, regner ut tallene på nytt og
  publiserer siden (`scripts/update_site_data.py`). En nyere rådatamåned erstatter den forrige
  for samme år, og tilskuddstallene følger med i `site-data/` så dekningskategoriene beholdes.
- **GitHub Actions:** `actions/checkout@v6`, `actions/setup-python@v6` og `actions/cache@v5` (Node 24), og
  byggene kjører på `ubuntu-24.04` i stedet for `ubuntu-latest`.
- **Ytelse:** leverandørlisten laster de 100 største i HTML-en (49 kB mot 1,3 MB) og henter resten som
  `data/leverandorer.json` først når noen søker, sorterer eller trykker «Vis alle». På en treg mobil går
  innlastingen fra 5,5 til 0,35 sekunder. Sorteringen leser hver verdi én gang, og sorteringsverdiene er
  kortere, så store virksomhetssider blir også raskere.
- **Metodesiden:** ny del «Hvorfor Kronesporet» med en tabell over hva hver kilde viser alene og hva
  koblingen gir. Teksten er oppdatert til statsregnskapet og Enhetsregisteret, med egne avsnitt om
  leverandører og delår.
- **Datakvalitet:** startdatoer mer enn ti år før kunngjøringen (som 1900-01-01) og perioder over 20 år
  regnes som feil i Doffin og erstattes med standard varighet.
- **Ny forside:** toppfelt med nøkkeltall for hele datagrunnlaget, kort for de fire kildene (Doffin,
  statsregnskapet, Enhetsregisteret, Kudos) og en tidslinje over kontrakter 2017–2026 med årene
  statsregnskapet dekker. Tallene regnes ut fra databasen ved hver bygging.
- **Leverandører på tvers av år:** årsandel for hvert år i leverandørlisten, og hver leverandørside viser
  år, kjøpere og alle kontrakter.
- **Nettside:** egen «Fant ikke siden»-side (404) med lenker videre, og strukturerte data
  (schema.org/Dataset) på siden om dataene, så søkemotorene finner de nedlastbare tallene.
- **Regnskapstall fra statsregnskapet:** 128 statlige virksomheter (mot 40) for 2023, 2024, 2025 og
  2026 januar–august, med kjøp per artskontogruppe fra rådata i bevilgningsregnskapet. Kunngjøringer fra
  underenheter (politidistriktene, FLO o.l.) regnes til virksomheten og vises ved kontrakten.
- **Delår:** 2026 vises som «2026 (jan–aug)» med eget forbehold; forsiden viser siste hele år.
- **Doffin-dekning:** flagget krever avvik flere år på rad. Avvik bare ett år, og dekning over 100 % som
  skyldes tilskudd eller avtaler for andre, vises som egne merker og kan filtreres.

## v0.1.0 – 2026-10-23

Første versjon av Kronesporet.

- **Innsamling:** Doffin-kunngjøringer om tildelte kontrakter med tildelt verdi, periode og
  leverandører (med organisasjonsnummer der det er oppgitt), med rate limiting og cache.
- **Regnskap:** kjøp av varer og tjenester fra årsrapportene i Kudos, og alternativt
  statsregnskapet som CSV eller DFØs månedsrapport (xlsx).
- **Analyse:** kobling på kjøperens orgnr, kontraktverdi fordelt over årene kontrakten løper,
  flagg for avvik over 5 % og for uvanlig høy eller lav Doffin-dekning sammenlignet med
  lignende virksomheter.
- **Nettside:** statisk side i `docs/` med forside for 40 statlige virksomheter (2025), én side
  per virksomhet med kontrakter, leverandører og lenke til årsrapporten, en side om dataene og
  metoden, og nedlastbare data (CSV/JSON).
