# Kronesporet

Norsk åpenhetsverktøy for offentlig pengebruk (Kronesporet.no).

## Fase 1: Doffin-kontrakter mot statsregnskapet

Kobler kunngjorte kontraktstildelinger i Doffin (kjøperens orgnr) mot
statsregnskapet (virksomhet) per år, og flagger virksomheter der summene
avviker med mer enn 5 %.

```bash
pip install -r requirements.txt
# Last ned CSV manuelt fra https://statsregnskapet.dfo.no/last-ned
python scripts/run_phase1.py --year 2025 --statsregnskap data/statsregnskap_2025.csv
```

### Rådata fra statsregnskapet (anbefalt)

DFØ publiserer «Rådata <måned> <år> (bevilgningsregnskap)» som xlsx på
statsregnskapet.dfo.no/last-ned: én rad per virksomhet (orgnr), kapittel, post og
artskonto, beløp hittil i år. Desember gir hele året. Flere filer kan gis samtidig,
og hver fil blir sitt år.

```bash
python scripts/run_phase1.py --year 2025 --kudos --statsregnskap \
    data/raadata_desember_2023_bevilgningsregnskap.xlsx \
    data/raadata_desember_2024_bevilgningsregnskap.xlsx \
    data/raadata_desember_2025_bevilgningsregnskap.xlsx
```

- Kjøp av varer og tjenester = artskonto 40–45 og 61–69 (varer, tjenester, husleie,
  konsulenter m.m.), 70–77 unntatt 71 (drift ellers) og 47–49 (investeringer).
  Lønn, tilskudd, overføringer og finans er holdt utenfor.
- Filene dekker de bruttobudsjetterte virksomhetene (omtrent 160 per år).
  Nettobudsjetterte virksomheter (universiteter m.fl.) står bare som tilskudd og er ikke med.
  Med `--kudos` hentes kjøpstallene deres fra årsrapportene i stedet (se under);
  virksomheter som finnes i rådata hentes ikke fra Kudos.
- Doffin-tildelinger hentes for alle regnskapsårene pluss `--history` år før det første.
- Mange virksomheter kunngjør på underenheter eller organisasjonsledd: politidistriktene og
  Politiets fellestjenester for Politiet, FLO for Forsvaret. Disse slås opp i Enhetsregisteret
  (tabellen `orgnr_parent`, `src/collectors/brreg.py`) og føres på virksomheten i regnskapet.
  En underenhet som selv står i regnskapet (f.eks. HELFO under Helsedirektoratet) beholder sitt eget orgnr.

### Uten statsregnskap-fil: årsrapportene i Kudos

Når statsregnskapet.dfo.no ikke svarer, kan kjøp av varer og tjenester hentes fra
årsrapportene i Kudos (kudos.dfo.no, ingen nøkkel). Hver statlig virksomhet har en
standard «Oppstilling av artskontorapportering»; vi bruker «Andre utbetalinger til
drift» + «Utbetaling til investeringer» (uten lønn, tilskudd og finans).

```bash
python scripts/run_phase1.py --year 2025 --skip-collect --kudos
```

- Krever `pdftotext` (poppler-utils) for å lese PDF-ene.
- Kommuner, fylker, selskaper og helseforetak har ikke årsrapport i Kudos og hoppes over.
- Nettobudsjetterte virksomheter (universiteter) har ikke artskontorapportering.
  For dem brukes SRS-regnskapets «utbetalinger for kjøp av varer og tjenester»
  (KS.12, ellers «Andre driftskostnader», RE.10) og investeringer (KS.17).
- Underenheter som ikke har egen årsrapport i Kudos (f.eks. Politiets fellestjenester,
  Forsvarets logistikkorganisasjon) blir stående utenfor.

### Med DFØs månedsrapport (xlsx)

Statsregnskapet publiserer også «Utgifter til staten <måned> <år>» som xlsx
(hittil i år, per kapittel og post, uten orgnr). Den kan brukes direkte:

```bash
python scripts/run_phase1.py --year 2026 --to-month 8 \
    --statsregnskap data/utgifter_til_staten_august_2026.xlsx
```

- `--to-month 8` henter bare Doffin-kunngjøringer januar-august, så periodene matcher.
- `--posts 1-49` (standard) tar med drifts- og investeringsposter; 50-99 er overføringer.
- Kapittelnavn (f.eks. «Statens vegvesen») kobles til orgnr via Doffin-kjøpere og
  Enhetsregisteret. Treffene ligger i tabellen `kapittel_orgnr` og kan rettes for hånd
  med `kilde = 'manuell'`. Kapitler som er formål og ikke virksomheter («Flytransport»)
  får ikke orgnr og blir stående utenfor.

### Valg

- `--no-details` henter bare søketreff (under ett minutt) og bruker anslått verdi.
  Uten flagget hentes tildelt verdi fra hver kunngjøring, som tar omtrent en time
  for et helt år med 1,2 sekunder mellom kall.
- `--max-notices 50` for en rask prøvekjøring.
- `--artskonto-prefix 6 7` tar bare med utvalgte artskontoer fra statsregnskapet.
- `--col-year/--col-orgnr/--col-name/--col-amount` hvis kolonnenavnene i CSV-en
  ikke gjenkjennes automatisk (se `src/analysis/join_and_flag.py`).

Resultatet havner i `data/offentlig_forbruk.db`: tabellene `doffin_contracts`,
`statsregnskap`, `contract_spend_comparison` og `flags` (`flag_type = 'doffin_avvik'`), og for xlsx også `kapittel_orgnr`.

### Årsfordelt kontraktverdi

Doffin-verdier gjelder hele kontraktsperioden (ofte rammeavtaler over flere år),
mens regnskapet er utgifter for ett år. Derfor fordeles hver kontrakt lineært over
perioden den løper, og et regnskapsår sammenlignes med summen av årsandelene til
alle kontrakter som løper det året, også de som ble tildelt tidligere år
(`src/analysis/annualize.py`, tabellen `doffin_contract_years`).

Kontraktsperioden leses fra kunngjøringen, i denne rekkefølgen:

1. start- og sluttdato (`datoer`)
2. «Varighet» / «Varighet, År» (`varighet`), regnet fra startdato eller publisering
3. fritekst som «2+1+1 år» eller «total varighet inntil 4 år» (`tekst`)
4. standard når ingenting er oppgitt: rammeavtaler 48 måneder (lovens maksimum,
   og medianen blant rammeavtaler med oppgitt periode), andre kontrakter 24 måneder
   (medianen blant dem med oppgitt periode)

Fornyelser («Maksimal fornyelse: 2») regnes som 12 måneder hver, men ikke ut over
48 måneder totalt. Verdien er tildelt total for prosedyren, ellers rammeavtalenes
maksimale verdi, ellers summen av vinnernes verdier.

Siden kontrakter tildelt tidligere år også teller, henter `run_phase1.py`
tildelinger 8 år bakover (`--history`) for virksomhetene som har regnskapstall.
Rå kunngjøringer lagres i `data/doffin_cache/`, så neste kjøring går raskt.

```bash
python scripts/run_phase1.py --year 2025 --skip-collect --kudos            # årsfordelt (standard)
python scripts/run_phase1.py --year 2025 --skip-collect --kudos --no-annualize   # gammel sammenligning
```

`--framework-months` og `--contract-months` endrer standardvarighetene.

### Doffin-dekning mot lignende virksomheter

Doffin viser bare kunngjorte kontrakter. Kjøp under terskelverdi, avrop og husleie
kunngjøres ikke, så årsandelen fra Doffin ligger typisk langt under kjøpene, og
5 %-regelen flagger alle. I tillegg til 5 %-flaggene (`doffin_avvik`) regnes derfor
**Doffin-dekning** = årsandel av Doffin-kontrakter / kjøp av varer og tjenester
(`src/analysis/coverage.py`, tabellen `doffin_coverage`).

Hver virksomhet sammenlignes med lignende virksomheter, det vil si samme
størrelsesgruppe etter kjøp (små, mellomstore og store per år). Avstanden måles
som robust z-verdi på log10(dekning) mot gruppens median og MAD.
Ved |z| > 2 får virksomheten et flagg med `flag_type = 'doffin_dekning'`.

- Høy dekning: kontraktverdiene er langt over kjøpene, for eksempel rammeavtaler
  som tak, kjøp for andre, eller kjøp som føres utenfor drift.
- Lav dekning: lite er kunngjort sammenlignet med kjøpene.

`--z-threshold` og `--peer-bands` endrer grensen og antall grupper.

Data: Doffin og statsregnskapet (DFØ), lisensiert under NLOD.
