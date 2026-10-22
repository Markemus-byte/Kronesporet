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

Merk: Doffin-verdier gjelder ofte hele kontraktsperioden (rammeavtaler over flere
år), mens statsregnskapet er årlige utgifter. Terskelen på 5 % bør justeres når
vi ser de reelle fordelingene.

Data: Doffin og statsregnskapet (DFØ), lisensiert under NLOD.
