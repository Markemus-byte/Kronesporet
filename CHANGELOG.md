# Endringslogg

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
