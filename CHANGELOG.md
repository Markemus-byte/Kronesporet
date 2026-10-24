# Endringslogg

## Ikke utgitt

- **Nasjonalbiblioteket** har nå regnskapstall fra statsregnskapet for alle år, også 2026. Rådata fører
  biblioteket på en avdeling (979530730), mens Doffin og årsrapportene bruker hovedenheten (976029100).
  Siden brukte derfor årsrapporttall, og for 2025 var det feil: 34,8 mill. kr mot 443,5 mill. kr i
  regnskapet. Orgnr i rådata som er underenheter, kobles nå til hovedenheten via Enhetsregisteret.
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
