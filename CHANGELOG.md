# Endringslogg

## Ikke utgitt

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
