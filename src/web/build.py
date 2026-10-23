"""Bygger Kronesporet.no som statiske HTML-sider (GitHub Pages, ingen byggesteg).

Sider:
  index.html                 forside med alle virksomhetene
  virksomhet/<orgnr>.html    én side per virksomhet med Doffin-kontraktene
  om.html                    om dataene og metoden
  data/virksomheter.csv|json nedlastbare tall (NLOD)
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import statistics
from datetime import date
from html import escape
from pathlib import Path

from src.web.data import Agency, Contract, Dataset

ASSETS = Path(__file__).parent / "assets"
SITE_NAME = "Kronesporet"
COPYRIGHT = "© 2026 Markemus-byte / Kronesporet. Alle rettigheter forbeholdt."

PERIOD_SOURCES = {
    "datoer": "start- og sluttdato i kunngjøringen",
    "varighet": "varighet oppgitt i kunngjøringen",
    "tekst": "varighet lest fra fritekst",
    "standard": "ingen periode oppgitt, antatt standardvarighet",
}
SPEND_PARTS = {"drift": "Andre utbetalinger til drift", "investering": "Utbetaling til investeringer"}


# --- formatering -----------------------------------------------------------

def _group(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def kr(x: float | None) -> str:
    """Kortform: 28,2 mrd. kr / 4,6 mill. kr / 123 945 kr."""
    if x is None:
        return "–"
    ax = abs(x)
    if ax >= 1e9:
        return f"{x / 1e9:.1f}".replace(".", ",") + " mrd. kr"
    if ax >= 1e6:
        return f"{x / 1e6:.1f}".replace(".", ",") + " mill. kr"
    return _group(x) + " kr"


def kr_full(x: float | None) -> str:
    return "–" if x is None else _group(x) + " kr"


def pct(x: float | None, signed: bool = False, decimals: int = 1) -> str:
    if x is None:
        return "–"
    v = x * 100
    if not signed and 0 < abs(v) < 1:
        decimals = 2
    s = f"{abs(v):,.{decimals}f}".replace(",", " ").replace(".", ",")
    if signed:
        return ("+" if v > 0 else "−" if v < 0 else "") + s + " %"
    return ("−" if v < 0 else "") + s + " %"


def _num(x: float | None) -> str:
    """Rå tallverdi til data-sort-attributter."""
    return "" if x is None else repr(float(x))


# --- sidemal ----------------------------------------------------------------

def page(title: str, body: str, root: str = "", active: str = "", description: str = "") -> str:
    nav = [("index.html", "Virksomheter", "index"), ("om.html", "Om dataene", "om")]
    links = "".join(
        f'<a href="{root}{href}"{" aria-current=\"page\"" if key == active else ""}>{label}</a>'
        for href, label, key in nav
    )
    full_title = f"{title} – {SITE_NAME}" if title != SITE_NAME else f"{SITE_NAME} – hvor går statens penger?"
    return f"""<!doctype html>
<html lang="nb">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(full_title)}</title>
<meta name="description" content="{escape(description or 'Kronesporet sammenligner statlige virksomheters kunngjorte kontrakter i Doffin med kjøpene i regnskapet.')}">
<meta property="og:title" content="{escape(full_title)}">
<meta property="og:description" content="{escape(description or 'Kronesporet sammenligner statlige virksomheters kunngjorte kontrakter i Doffin med kjøpene i regnskapet.')}">
<meta property="og:type" content="website">
<meta property="og:locale" content="nb_NO">
<meta name="theme-color" content="#0f5c4d">
<link rel="icon" href="{root}assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{root}assets/style.css">
</head>
<body>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="{root}index.html"><span class="mark" aria-hidden="true">kr</span>Kronesporet</a>
    <nav>{links}</nav>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="wrap">
    <p>Data: <a href="https://doffin.no">Doffin</a> (Digdir) og årsrapporter i
    <a href="https://kudos.dfo.no">Kudos</a> (DFØ), lisensiert under
    <a href="https://data.norge.no/nlod/no/2.0">Norsk lisens for offentlige data (NLOD 2.0)</a>.
    Kronesporet er ikke tilknyttet DFØ eller Digdir.</p>
    <p>Sist bygget {date.today().strftime('%d.%m.%Y')}. <a href="{root}om.html">Om dataene og metoden</a>.</p>
    <p>{COPYRIGHT}</p>
  </div>
</footer>
<script src="{root}assets/app.js"></script>
</body>
</html>
"""


def _flag_badges(a: Agency) -> str:
    out = []
    if a.coverage and a.coverage.flagged:
        word = "høy" if a.coverage.z > 0 else "lav"
        out.append(f'<span class="badge badge-strong" title="Dekningen avviker tydelig fra lignende virksomheter">'
                   f'Uvanlig {word} dekning</span>')
    if a.flagged:
        out.append(f'<span class="badge badge-soft" title="Doffin-summen avviker mer enn 5 % fra kjøpene">'
                   f'Avvik over 5&nbsp;%</span>')
    return " ".join(out) or '<span class="muted">–</span>'


def _caveat(ds: Dataset) -> str:
    return (
        '<aside class="caveat"><strong>Les dette først.</strong> Doffin viser bare kontrakter som er kunngjort. '
        "Kjøp under terskelverdiene, avrop på eksisterende avtaler, husleie og mye annet kommer ikke med, "
        f"så Doffin-summen er nesten alltid langt lavere enn kjøpene i regnskapet. Med en fast terskel på "
        f"{pct(ds.threshold, decimals=0)} blir derfor alle virksomhetene flagget. Et flagg betyr «verdt å se nærmere på», "
        f'ikke at noe er galt. <a href="om.html#forbehold">Mer om forbeholdene</a>.</aside>'
    )


# --- forside ----------------------------------------------------------------

def index_page(ds: Dataset) -> str:
    n = len(ds.agencies)
    n_flag = sum(a.flagged for a in ds.agencies)
    stats = [
        (str(n), "statlige virksomheter med tall"),
        (pct(statistics.median(a.deviation for a in ds.agencies), signed=True) if n else "–",
         "median avvik, Doffin mot kjøp"),
        (f"{n_flag} av {n}", f"flagget for avvik over {pct(ds.threshold, decimals=0)}"),
    ]
    if ds.has_coverage:
        covered = [a for a in ds.agencies if a.coverage]
        n_cov = sum(a.coverage.flagged for a in covered)
        stats.append((f"{n_cov} av {len(covered)}", "har uvanlig høy eller lav dekning"))
    stat_html = "".join(f'<div class="stat"><div class="stat-value">{v}</div><div class="stat-label">{l}</div></div>'
                        for v, l in stats)

    doffin_label = f"Doffin {ds.year}" + (" (årsandel)" if ds.annualized else "")
    head = [
        ("Virksomhet", "text", ""),
        ("Kontrakter", "num", "Antall Doffin-kontrakter som løper i året" if ds.annualized
         else "Antall kontrakter tildelt i året"),
        (doffin_label, "num", "Den delen av kontraktsverdien som faller i regnskapsåret" if ds.annualized
         else "Sum tildelt kontraktsverdi i året"),
        (f"Kjøp {ds.year}", "num", "Kjøp av varer og tjenester (drift + investering) fra årsrapporten"),
        ("Avvik", "num", "(Doffin − kjøp) / kjøp"),
    ]
    if ds.has_coverage:
        head.append(("Dekning", "num", "Doffin-summen som andel av kjøpene"))
    head.append(("Flagg", "text", ""))
    thead = "".join(
        f'<th scope="col" data-type="{t}"{f" title=\"{escape(tip)}\"" if tip else ""}>'
        f'<button type="button">{escape(label)}</button></th>'
        for label, t, tip in head
    )

    rows = []
    for a in sorted(ds.agencies, key=lambda a: a.deviation):
        cov = ""
        if ds.has_coverage:
            cov = (f'<td class="num" data-sort="{_num(a.coverage.ratio)}">{pct(a.coverage.ratio)}</td>'
                   if a.coverage else '<td class="num" data-sort="">–</td>')
        flag_sort = (2 if a.coverage and a.coverage.flagged else 0) + (1 if a.flagged else 0)
        rows.append(
            f'<tr data-name="{escape(a.name.lower())} {a.orgnr}">'
            f'<td><a href="virksomhet/{a.slug}.html">{escape(a.name)}</a></td>'
            f'<td class="num" data-sort="{a.contract_count}">{a.contract_count}</td>'
            f'<td class="num" data-sort="{_num(a.contract_sum)}" title="{kr_full(a.contract_sum)}">{kr(a.contract_sum)}</td>'
            f'<td class="num" data-sort="{_num(a.spend_sum)}" title="{kr_full(a.spend_sum)}">{kr(a.spend_sum)}</td>'
            f'<td class="num dev dev-{a.direction}" data-sort="{_num(a.deviation)}">{pct(a.deviation, signed=True)}</td>'
            f"{cov}"
            f'<td data-sort="{flag_sort}">{_flag_badges(a)}</td>'
            "</tr>"
        )

    intro = (
        f"<p class=\"lede\">Hvor mye av statens innkjøp kan du faktisk se i Doffin? Kronesporet legger "
        f"kunngjorte kontrakter for {n} statlige virksomheter ved siden av hva de selv rapporterer at de "
        f"kjøpte varer og tjenester for i {ds.year}.</p>"
    )
    body = f"""
<section class="hero">
  <h1>Hvor går statens penger?</h1>
  {intro}
</section>
<div class="stats">{stat_html}</div>
{_caveat(ds)}
<section>
  <div class="table-tools">
    <label for="filter">Søk etter virksomhet</label>
    <input id="filter" data-filter="agencies" type="search" placeholder="F.eks. vegvesen eller 971032081" autocomplete="off">
    <span class="muted small" data-count-for="agencies" aria-live="polite"></span>
    <a class="download" href="data/virksomheter.csv" download>Last ned CSV</a>
  </div>
  <div class="table-scroll">
  <table class="sortable" id="agencies">
    <thead><tr>{thead}</tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  </div>
  <p class="muted small">Klikk på en kolonne for å sortere. Beløp i kortform; hold pekeren over for nøyaktig tall.</p>
</section>
"""
    return page(SITE_NAME, body, active="index")


# --- virksomhetsside ----------------------------------------------------------

def _period(c: Contract) -> str:
    if c.start_date and c.end_date:
        return f"{c.start_date[:7]} – {c.end_date[:7]}"
    if c.first_year and c.last_year:
        return str(c.first_year) if c.first_year == c.last_year else f"{c.first_year}–{c.last_year}"
    return "–"


def _suppliers_cell(c: Contract) -> str:
    if not c.suppliers:
        return '<td class="muted" data-sort="">–</td>'
    names = [escape(w.name) for w in c.suppliers]
    full = escape("; ".join(f"{w.name} ({w.orgnr})" if w.orgnr else w.name for w in c.suppliers))
    shown = names if len(names) <= 3 else names[:2] + [f"og {len(names) - 2} til"]
    return f'<td class="suppliers" data-sort="{names[0]}" title="{full}">{"<br>".join(shown)}</td>'


def top_suppliers(ds: Dataset, a: Agency, limit: int = 10) -> list[tuple[str, str | None, int, float]]:
    """(navn, orgnr, antall kontrakter, verdi) for de største leverandørene.

    Verdien er årsandelen (eller hele verdien uten årsfordeling), delt likt når
    flere leverandører vant samme kontrakt, siden Doffin sjelden fordeler den.
    """
    acc: dict[str, list] = {}
    for c in a.contracts:
        if not c.suppliers:
            continue
        part = (c.year_value or 0.0) / len(c.suppliers)
        for w in c.suppliers:
            row = acc.setdefault((w.orgnr or w.name).lower(), [w.name, w.orgnr, 0, 0.0])
            row[2] += 1
            row[3] += part
    rows = sorted((tuple(r) for r in acc.values()), key=lambda r: (-r[3], -r[2], r[0]))
    return rows[:limit]  # type: ignore[return-value]


def agency_page(ds: Dataset, a: Agency) -> str:
    facts = [
        ("Organisasjonsnummer", f'<a href="https://data.brreg.no/enhetsregisteret/oppslag/enheter/{escape(a.orgnr)}">'
                                f'{escape(a.orgnr)}</a>'),
        (f"Kjøp av varer og tjenester {ds.year}", kr_full(a.spend_sum)),
    ]
    for key, amount in a.spend_parts.items():
        facts.append((f" {SPEND_PARTS.get(key, key)}", kr_full(amount)))
    if ds.annualized:
        facts.append((f"Doffin-kontrakter som løper i {ds.year}", f"{a.contract_count}"))
        facts.append((f"Årsandel av kontraktsverdien {ds.year}", kr_full(a.contract_sum)))
        facts.append((f"Tildelt i {ds.year} (hele verdien, {a.awarded_in_year_count} stk.)",
                      kr_full(a.awarded_in_year_sum)))
    else:
        facts.append((f"Doffin-kontrakter tildelt i {ds.year}", f"{a.contract_count}"))
        facts.append(("Sum tildelt kontraktsverdi", kr_full(a.contract_sum)))
    facts.append(("Avvik, Doffin mot kjøp", pct(a.deviation, signed=True)))
    if a.coverage:
        c = a.coverage
        facts.append(("Doffin-dekning", f"{pct(c.ratio)} (lignende virksomheter, {escape(c.peer_group)}: "
                                         f"median {pct(c.peer_median)})"))
    facts_html = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)

    notes = []
    if a.coverage and a.coverage.flagged:
        c = a.coverage
        side = "høyere" if c.z > 0 else "lavere"
        notes.append(
            f"<li><strong>Uvanlig {'høy' if c.z > 0 else 'lav'} Doffin-dekning.</strong> Doffin dekker {pct(c.ratio)} "
            f"av kjøpene, tydelig {side} enn de {c.peer_count} {escape(c.peer_group)} virksomhetene i samme "
            f"størrelsesgruppe (median {pct(c.peer_median)}, z = {c.z:+.1f}).</li>")
    if a.flagged:
        word = "høyere" if a.deviation > 0 else "lavere"
        notes.append(
            f"<li><strong>Avvik over {pct(ds.threshold, decimals=0)}.</strong> Doffin-summen er "
            f"{pct(abs(a.deviation))} {word} enn kjøpene i årsrapporten. Det gjelder nesten alle virksomhetene, "
            f"se <a href=\"../om.html#forbehold\">forbeholdene</a>.</li>")
    notes_html = f'<ul class="notes">{"".join(notes)}</ul>' if notes else ""

    report = (f'<a class="button" href="{escape(a.report_url)}">Årsrapport {ds.year} i Kudos (PDF)</a>'
              if a.report_url else "")

    cols = [("Kontrakt", "text")]
    if ds.has_suppliers:
        cols.append(("Leverandør", "text"))
    cols.append(("Tildelt", "num"))
    if ds.annualized:
        cols += [("Løper", "num"), ("Hele verdien", "num"), (f"Andel {ds.year}", "num")]
    else:
        cols.append(("Verdi", "num"))
    thead = "".join(f'<th scope="col" data-type="{t}"><button type="button">{c}</button></th>' for c, t in cols)
    rows = []
    for c in a.contracts:
        title = escape(c.title)
        link = f'<a href="{escape(c.url)}">{title}</a>' if c.url else title
        tags = []
        if c.is_framework:
            tags.append("rammeavtale")
        if c.value_type == "estimated":
            tags.append("anslått verdi")
        if c.period_source == "standard":
            tags.append("antatt varighet")
        tag_html = "".join(f'<span class="tag">{t}</span>' for t in tags)
        cells = [f"<td>{link}{tag_html}</td>"]
        if ds.has_suppliers:
            cells.append(_suppliers_cell(c))
        cells.append(f'<td class="num" data-sort="{escape(c.published or "")}">{escape((c.published or "–")[:10])}</td>')
        if ds.annualized:
            src = PERIOD_SOURCES.get(c.period_source or "", "")
            cells.append(f'<td class="num" data-sort="{c.first_year or ""}" title="{escape(src)}">{_period(c)}</td>')
            cells.append(f'<td class="num" data-sort="{_num(c.value)}" title="{kr_full(c.value)}">{kr(c.value)}</td>')
            cells.append(f'<td class="num" data-sort="{_num(c.year_value)}" title="{kr_full(c.year_value)}">'
                         f'{kr(c.year_value)}</td>')
        else:
            cells.append(f'<td class="num" data-sort="{_num(c.value)}" title="{kr_full(c.value)}">{kr(c.value)}</td>')
        haystack = " ".join([c.title] + [f"{w.name} {w.orgnr or ''}" for w in c.suppliers]).lower()
        rows.append(f'<tr data-name="{escape(haystack)}">{"".join(cells)}</tr>')

    tools = ('<div class="table-tools"><label for="contract-filter">Søk i kontraktene</label>'
             '<input id="contract-filter" data-filter="contracts" type="search" autocomplete="off" '
             f'placeholder="{"Tittel eller leverandør" if ds.has_suppliers else "Tittel"}">'
             '<span class="muted small" data-count-for="contracts" aria-live="polite"></span></div>')
    table = (f'{tools}<div class="table-scroll"><table class="sortable" id="contracts"><thead><tr>{thead}</tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>') if rows else \
        '<p class="muted">Ingen Doffin-kontrakter funnet for året.</p>'
    explain = (f"Kontrakter i Doffin der {escape(a.name)} er kjøper og som løper i {ds.year}. Verdien er fordelt "
               f"jevnt over perioden kontrakten løper; «Andel {ds.year}» er det som faller i regnskapsåret."
               if ds.annualized else
               f"Kontrakter i Doffin der {escape(a.name)} er kjøper, tildelt i {ds.year}.")

    suppliers_html = ""
    top = top_suppliers(ds, a) if ds.has_suppliers else []
    if top:
        value_label = f"Andel {ds.year}" if ds.annualized else "Verdi"
        srows = "".join(
            f"<tr><td>{escape(name)}"
            + (f' <a class="orgnr" href="https://data.brreg.no/enhetsregisteret/oppslag/enheter/{escape(orgnr)}">'
               f"{escape(orgnr)}</a>" if orgnr else "")
            + f'</td><td class="num">{n}</td><td class="num" title="{kr_full(v)}">{kr(v)}</td></tr>'
            for name, orgnr, n, v in top)
        suppliers_html = f"""
<section>
  <h2>Største leverandører</h2>
  <p class="muted">Etter verdi av kontraktene som løper i {ds.year}. Når flere leverandører vant samme kontrakt
  (typisk rammeavtaler), er verdien delt likt mellom dem, fordi Doffin sjelden viser fordelingen.</p>
  <div class="table-scroll"><table><thead><tr><th scope="col">Leverandør</th>
  <th scope="col" data-type="num">Kontrakter</th><th scope="col" data-type="num">{value_label}</th></tr></thead>
  <tbody>{srows}</tbody></table></div>
</section>"""

    body = f"""
<p class="crumbs"><a href="../index.html">Alle virksomheter</a></p>
<section class="hero">
  <h1>{escape(a.name)}</h1>
  <div class="badges">{_flag_badges(a)}</div>
</section>
<div class="two-col">
  <dl class="facts">{facts_html}</dl>
  <div>
    {notes_html}
    <p class="actions">{report}</p>
  </div>
</div>
{suppliers_html}
<section>
  <h2>Doffin-kontrakter ({len(a.contracts)})</h2>
  <p class="muted">{explain}</p>
  {table}
</section>
"""
    return page(a.name, body, root="../", description=f"Doffin-kontrakter og kjøp for {a.name} i {ds.year}.")


# --- om dataene ---------------------------------------------------------------

def about_page(ds: Dataset) -> str:
    coverage = ""
    if ds.has_coverage:
        coverage = """
<h3>Doffin-dekning mot lignende virksomheter</h3>
<p>Fordi Doffin bare viser en del av innkjøpene, er det mer nyttig å spørre om en virksomhet kunngjør
<em>uvanlig mye eller lite</em> sammenlignet med lignende virksomheter. Dekningen er Doffin-summen delt på kjøpene.
Virksomhetene deles i tre størrelsesgrupper etter kjøp (små, mellomstore, store), og hver virksomhet
sammenlignes med medianen i sin gruppe på logaritmisk skala (robust z-verdi). Avviker den med mer enn
to standardavvik, får den flagget «uvanlig høy» eller «uvanlig lav dekning».</p>
<ul>
<li><strong>Høy dekning</strong> kan bety at kontraktverdiene er tak i rammeavtaler som ikke blir brukt fullt ut,
at virksomheten kjøper inn for andre (som Statens innkjøpssenter), eller at kjøpene føres et annet sted
enn drift og investering.</li>
<li><strong>Lav dekning</strong> kan bety at virksomheten kjøper mye under terskelverdiene, via andres avtaler,
eller at kunngjøringer mangler.</li>
</ul>
"""
    annual = ""
    if ds.annualized:
        annual = """
<h3>Årsfordelt kontraktverdi</h3>
<p>Verdien i Doffin gjelder hele kontraktsperioden, ofte en rammeavtale over flere år, mens regnskapet viser
ett år. Hver kontrakt fordeles derfor jevnt over perioden den løper, og regnskapsåret sammenlignes med
summen av årsandelene til alle kontrakter som løper det året, også de som ble tildelt tidligere.
Perioden leses fra kunngjøringen (start- og sluttdato, oppgitt varighet eller fritekst som «2+1+1 år»).
Står det ingenting, antas 48 måneder for rammeavtaler og 24 måneder for andre kontrakter; slike
kontrakter er merket «antatt varighet».</p>
"""
    body = f"""
<section class="hero"><h1>Om dataene og metoden</h1>
<p class="lede">Kronesporet er et åpenhetsverktøy for offentlig pengebruk. Første versjon sammenligner
kunngjorte kontrakter i Doffin med det statlige virksomheter selv rapporterer at de kjøpte for.</p></section>
<div class="prose">
<h2>Kilder</h2>
<dl class="sources">
<dt><a href="https://doffin.no">Doffin</a></dt>
<dd>Den nasjonale kunngjøringsdatabasen for offentlige anskaffelser, drevet av Digdir. Vi henter
kunngjøringer om tildelte kontrakter, med kjøperens organisasjonsnummer, tittel og tildelt verdi.
Mangler tildelt verdi, brukes anslått verdi (merket «anslått verdi»).{" Leverandørene er vinnerne slik de står i kunngjøringen, med organisasjonsnummer når det er oppgitt." if ds.has_suppliers else ""}</dd>
<dt><a href="https://kudos.dfo.no">Kudos</a> (DFØ)</dt>
<dd>Kunnskapsdatabasen for offentlige utredninger, der statlige virksomheter publiserer årsrapportene sine.
Fra den standardiserte «Oppstilling av artskontorapportering» bruker vi
<em>Andre utbetalinger til drift</em> + <em>Utbetaling til investeringer</em> som «kjøp av varer og tjenester».
Lønn, tilskudd og finansposter er holdt utenfor.</dd>
</dl>
<p>Begge kildene er lisensiert under <a href="https://data.norge.no/nlod/no/2.0">Norsk lisens for offentlige data
(NLOD 2.0)</a>, som tillater gjenbruk, også kommersielt, med kildehenvisning. Inneholder data under NLOD
tilgjengeliggjort av Digitaliseringsdirektoratet (Doffin) og Direktoratet for forvaltning og økonomistyring (Kudos).</p>

<h2>Slik kobles tallene</h2>
<p>Regnskapet har ikke leverandører, så koblingen går på <em>kjøper</em>: organisasjonsnummeret til kjøperen i
Doffin mot virksomheten som har levert årsrapporten. Avviket er (Doffin − kjøp) / kjøp, og virksomheter
der avviket er større enn {pct(ds.threshold, decimals=0)} i den ene eller andre retningen, blir flagget.</p>
{annual}
{coverage}
<h2 id="forbehold">Forbehold</h2>
<ul>
<li><strong>Doffin dekker bare en del av kjøpene.</strong> Kjøp under terskelverdiene, avrop på eksisterende
rammeavtaler (også Statens innkjøpssenters fellesavtaler), husleie og kjøp via andre virksomheter kunngjøres
ikke. Doffin-summen blir derfor som regel mye lavere enn kjøpene, og {pct(ds.threshold, decimals=0)}-terskelen
flagger nesten alle. Den er beholdt fordi den er den opprinnelige målestokken, ikke fordi et slikt avvik i seg
selv tyder på feil.</li>
<li><strong>Kontraktverdier er anslag.</strong> For rammeavtaler er verdien ofte et tak, ikke det som faktisk
blir kjøpt.</li>
<li><strong>Ikke alle virksomheter er med.</strong> Kommuner, fylker, helseforetak og selskaper har ikke
årsrapport i Kudos. Forvaltningsbedrifter som Statsbygg og universitetene bruker et annet oppsett som foreløpig
ikke leses.</li>
<li><strong>Feil i kildene følger med.</strong> Navn og tall vises slik de står i Doffin og årsrapportene.</li>
</ul>

<h2>Last ned dataene</h2>
<p>Tallene bak forsiden kan lastes ned som
<a href="data/virksomheter.csv">CSV</a> eller <a href="data/virksomheter.json">JSON</a>, og alle kontraktene
med leverandører som <a href="data/kontrakter.csv">kontrakter.csv</a>. Dataene fra Doffin og Kudos er fortsatt
underlagt NLOD 2.0; oppgi kildene ved gjenbruk.</p>
</div>
"""
    return page("Om dataene", body, active="om")


# --- nedlastbare data ---------------------------------------------------------

def export_rows(ds: Dataset) -> list[dict[str, object]]:
    rows = []
    for a in ds.agencies:
        row: dict[str, object] = {
            "virksomhet": a.name, "orgnr": a.orgnr, "år": a.year,
            "antall_kontrakter": a.contract_count,
            "doffin_sum_kr": round(a.contract_sum),
            "kjop_varer_tjenester_kr": round(a.spend_sum),
            "avvik_prosent": round(a.deviation * 100, 1),
            "flagget_5_prosent": int(a.flagged),
        }
        if ds.has_coverage:
            c = a.coverage
            row.update({
                "dekning_prosent": round(c.ratio * 100, 2) if c else None,
                "storrelsesgruppe": c.peer_group if c else None,
                "gruppens_mediandekning_prosent": round(c.peer_median * 100, 1) if c else None,
                "flagget_dekning": int(c.flagged) if c else None,
            })
        row["kilde_arsrapport"] = a.report_url or ""
        rows.append(row)
    return rows


def contract_rows(ds: Dataset) -> list[dict[str, object]]:
    rows = []
    for a in ds.agencies:
        for c in a.contracts:
            rows.append({
                "virksomhet": a.name, "virksomhet_orgnr": a.orgnr, "kontrakt_id": c.id, "tittel": c.title,
                "leverandorer": "; ".join(w.name for w in c.suppliers),
                "leverandor_orgnr": "; ".join(w.orgnr or "" for w in c.suppliers),
                "publisert": c.published or "", "rammeavtale": int(c.is_framework),
                "verdi_kr": round(c.value) if c.value is not None else "",
                "verditype": c.value_type or "",
                f"andel_{ds.year}_kr": round(c.year_value) if c.year_value is not None else "",
                "lenke": c.url or "",
            })
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    path.write_text("\ufeff" + buf.getvalue(), encoding="utf-8")


def build_site(ds: Dataset, out: Path) -> int:
    """Skriver hele nettstedet til `out`. Returnerer antall HTML-sider."""
    if (out / "virksomhet").exists():
        shutil.rmtree(out / "virksomhet")
    (out / "virksomhet").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)
    shutil.copytree(ASSETS, out / "assets", dirs_exist_ok=True)
    (out / ".nojekyll").write_text("")

    (out / "index.html").write_text(index_page(ds), encoding="utf-8")
    (out / "om.html").write_text(about_page(ds), encoding="utf-8")
    for a in ds.agencies:
        (out / "virksomhet" / f"{a.slug}.html").write_text(agency_page(ds, a), encoding="utf-8")

    rows = export_rows(ds)
    _write_csv(out / "data" / "virksomheter.csv", rows)
    _write_csv(out / "data" / "kontrakter.csv", contract_rows(ds))
    meta = {"år": ds.year, "årsfordelt": ds.annualized, "terskel": ds.threshold,
            "lisens": "NLOD 2.0", "kilder": ["Doffin (Digdir)", "Kudos (DFØ)"], "virksomheter": rows}
    (out / "data" / "virksomheter.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return 2 + len(ds.agencies)
