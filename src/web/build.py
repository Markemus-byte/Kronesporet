"""Bygger Kronesporet.no som statiske HTML-sider (GitHub Pages, ingen byggesteg).

Sider:
  index.html                 forside med alle virksomhetene
  virksomhet/<orgnr>.html    én side per virksomhet med Doffin-kontraktene
  helseforetak.html          helseforetakene (utenfor statsregnskapet), med helseforetak/<orgnr>.html
  kommuner.html              kommunene og fylkeskommunene (KOSTRA), med kommune/<orgnr>.html
  om.html                    om dataene og metoden
  varsler.html               planlagte varsler, avtaler som løper ut og interessepåmelding
  vilkar.html                vilkår, forbehold og personvern
  404.html                   «fant ikke siden» (GitHub Pages viser den for ukjente adresser)
  data/virksomheter.csv|json nedlastbare tall (NLOD)
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import statistics
from collections import Counter
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from urllib.parse import quote

from src.web.data import MONTHS, Agency, Breadth, Contract, Dataset, Expiring, source_month

ASSETS = Path(__file__).parent / "assets"
SITE_NAME = "Kronesporet"
DEFAULT_DESCRIPTION = ("Kronesporet sammenligner statlige virksomheters kunngjorte kontrakter i Doffin "
                       "med kjøpene i regnskapet.")

CURRENT = ' aria-current="page"'
NUM_TYPE = ' data-type="num"'
ROW_CURRENT = ' aria-current="true"'

# Settes av build_site() for hele byggingen: offentlig adresse (for canonical, Open Graph
# og sidekart) og datoen dataene sist ble hentet (vises i bunnteksten).
_SITE: dict[str, str] = {"base_url": "", "data_date": "", "years": "", "statsregnskap": "", "units": "",
                         "health": "", "companies": "", "municipal": ""}
COPYRIGHT = "© 2026 Markemus-byte / Kronesporet. Alle rettigheter forbeholdt."
REPO_URL = "https://github.com/Markemus-byte/Kronesporet"
CONTACT_EMAIL = "post@kronesporet.no"  # kontakt i bunnteksten; tom = henvendelser via GitHub
PRIVACY_EMAIL = "personvern@kronesporet.no"  # personvernhenvendelser (vilkar.html)
ALERTS_EMAIL = "varsler@kronesporet.no"  # interessepåmelding for varsler (varsler.html)
PRESS_EMAIL = "presse@kronesporet.no"
# Workeren for varsler (worker/, f.eks. https://kronesporet-varsler.<konto>.workers.dev). Tom = varsler.html har
# interessepåmelding på e-post, og sidene har ingen «Få varsel»-lenker.
ALERTS_API = ""

PERIOD_SOURCES = {
    "datoer": "start- og sluttdato i kunngjøringen",
    "varighet": "varighet oppgitt i kunngjøringen",
    "tekst": "varighet lest fra fritekst",
    "standard": "ingen periode oppgitt, antatt standardvarighet",
}
SPEND_PARTS = {"drift": "Andre utbetalinger til drift", "investering": "Utbetaling til investeringer"}
# Artskontogruppene (to første sifre i statens standard kontoplan) som telles som kjøp i statsregnskapet.
ARTSKONTO_GROUPS = {
    "40": "Innkjøp av råvarer og halvfabrikater",
    "41": "Frakt, toll og spedisjon",
    "43": "Forbruk av innkjøpte varer og tjenester",
    "45": "Fremmedytelse og underentreprise",
    "47": "Immaterielle eiendeler og programvarelisenser",
    "48": "Bygninger, anlegg og infrastruktur",
    "49": "Maskiner, transportmidler, inventar og IKT-utstyr",
    "61": "Frakt og transport",
    "62": "Energi og brensel",
    "63": "Lokaler",
    "64": "Leie av maskiner, inventar og datasystemer",
    "65": "Verktøy, inventar og driftsmateriale",
    "66": "Reparasjon og vedlikehold",
    "67": "Konsulenter og andre fremmede tjenester",
    "68": "Kontorkostnader, trykksaker, møter og kurs",
    "69": "Telefoni, datakommunikasjon og porto",
    "70": "Kostnader til transportmidler",
    "72": "Provisjonskostnader",
    "73": "Salg, reklame og representasjon",
    "74": "Kontingenter og gaver",
    "75": "Forsikring, garanti og service",
    "76": "Lisenser og patenter",
    "77": "Annen kostnad",
}
# Delår (år -> siste måned med regnskapstall) og året forsiden viser; settes av build_site().
_PARTIAL: dict[int, int] = {}
_FRONT: dict[str, int] = {"year": 0}


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
    """Tallverdi til data-sort-attributter: hele kroner for beløp, seks gjeldende siffer ellers."""
    if x is None:
        return ""
    return str(round(x)) if abs(x) >= 1000 else f"{x:.6g}"


# --- sidemal ----------------------------------------------------------------

def page(title: str, body: str, root: str = "", active: str = "", description: str = "", path: str = "",
         head: str = "") -> str:
    nav = [("index.html", "Virksomheter", "index")]
    if _SITE["health"]:
        nav.append(("helseforetak.html", "Helseforetak", "helseforetak"))
    if _SITE["companies"]:
        nav.append(("selskaper.html", "Selskaper", "selskaper"))
    if _SITE["municipal"]:
        nav.append(("kommuner.html", "Kommuner", "kommuner"))
    nav += [("leverandorer.html", "Leverandører", "leverandorer"), ("varsler.html", "Varsler", "varsler"),
            ("om.html", "Om dataene", "om")]
    links = "".join(
        f'<a href="{root}{href}"{CURRENT if key == active else ""}>{label}</a>'
        for href, label, key in nav
    )
    full_title = f"{title} – {SITE_NAME}" if title != SITE_NAME else f"{SITE_NAME} – hvor går statens penger?"
    description = description or DEFAULT_DESCRIPTION
    base = _SITE["base_url"]
    url_meta = ""
    if base and path:
        url = base + ("" if path == "index.html" else path)
        url_meta = (f'<link rel="canonical" href="{escape(url)}">\n'
                    f'<meta property="og:url" content="{escape(url)}">\n'
                    f'<meta property="og:image" content="{escape(base)}assets/og-bilde.png">\n'
                    '<meta name="twitter:card" content="summary_large_image">\n')
    data_date = f"Data hentet {_SITE['data_date']}. " if _SITE["data_date"] else ""
    sources = ('<a href="https://doffin.no">Doffin</a> (Digdir), '
               '<a href="https://statsregnskapet.dfo.no">statsregnskapet</a> og årsrapporter i\n    '
               '<a href="https://kudos.dfo.no">Kudos</a> (DFØ)'
               + (', <a href="https://www.brreg.no">Enhetsregisteret</a>'
                  + (" og Regnskapsregisteret" if _SITE["health"] else "") + " (Brønnøysundregistrene)"
                  if _SITE["units"] else "")
               + (', <a href="https://www.ssb.no/offentlig-sektor/kostra">KOSTRA</a> (SSB)' if _SITE["municipal"] else "")
               if _SITE["statsregnskap"] else
               '<a href="https://doffin.no">Doffin</a> (Digdir) og årsrapporter i\n    '
               '<a href="https://kudos.dfo.no">Kudos</a> (DFØ)')
    return f"""<!doctype html>
<html lang="nb">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(full_title)}</title>
<meta name="description" content="{escape(description)}">
<meta property="og:title" content="{escape(full_title)}">
<meta property="og:description" content="{escape(description)}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:type" content="website">
{url_meta}
<meta property="og:locale" content="nb_NO">
<meta name="theme-color" content="#0f5c4d">
<link rel="icon" href="{root}assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{root}assets/style.css">
{head}</head>
<body>
<a class="skip-link" href="#innhold">Hopp til innholdet</a>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="{root}index.html"><span class="mark" aria-hidden="true">kr</span>Kronesporet</a>
    <nav aria-label="Hovedmeny">{links}</nav>
  </div>
</header>
<main class="wrap" id="innhold" tabindex="-1">
{body}
</main>
<footer class="site-footer">
  <div class="wrap">
    <p>Data: {sources}, lisensiert under
    <a href="https://data.norge.no/nlod/no/2.0">Norsk lisens for offentlige data (NLOD 2.0)</a>.
    Kronesporet er ikke tilknyttet DFØ, Digdir eller Brønnøysundregistrene.</p>
    <p>{data_date}<a href="{root}om.html">Om dataene og metoden</a> ·
    <a href="{root}vilkar.html">Vilkår og personvern</a>{
    f' · Kontakt: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>' if CONTACT_EMAIL else ""}.</p>
    <p>{COPYRIGHT}</p>
  </div>
</footer>
<script src="{root}assets/app.js"></script>
</body>
</html>
"""


# Merker for Doffin-dekningen som ikke er flagg (doffin_coverage.category): (tekst, forklaring)
COVERAGE_NOTES = {
    "observasjon": ("Avvik i dekning i år", "Dekningen avviker fra lignende virksomheter dette året, men ikke "
                                             "flere år på rad"),
    "tilskudd": ("Over 100 %: tilskudd", "Doffin-andelen er større enn kjøpene, men dekkes av tilskudd ut av "
                                         "staten (f.eks. kjøp av fly- og togruter)"),
    "avtaler_for_andre": ("Over 100 %: avtaler for andre", "Doffin-andelen er større enn kjøpene, typisk fordi "
                                                           "virksomheten inngår avtaler som andre bruker"),
}


def _z(z: float) -> str:
    """Robust z-verdi med norsk desimalkomma og minustegn: −2,3."""
    return f"{z:+.1f}".replace(".", ",").replace("-", "\u2212")


def _flag_badges(a: Agency) -> str:
    out = []
    if a.coverage and a.coverage.flagged:
        word = "høy" if a.coverage.z > 0 else "lav"
        tip = ("Dekningen avviker fra lignende virksomheter flere år på rad" if a.coverage.category
               else "Dekningen avviker tydelig fra lignende virksomheter")
        out.append(f'<span class="badge badge-strong" title="{tip}">Uvanlig {word} dekning</span>')
    elif a.coverage and a.coverage.category in COVERAGE_NOTES:
        label, tip = COVERAGE_NOTES[a.coverage.category]
        out.append(f'<span class="badge badge-note" title="{escape(tip)}">{escape(label)}</span>')
    if a.flagged:
        out.append(f'<span class="badge badge-soft" title="Doffin-summen avviker mer enn 5 % fra kjøpene">'
                   f'Avvik over 5&nbsp;%</span>')
    return " ".join(out) or '<span class="muted">–</span>'


def _caveat(ds: Dataset, root: str = "") -> str:
    return (
        '<aside class="caveat"><strong>Les dette først.</strong> Doffin viser bare kontrakter som er kunngjort. '
        "Kjøp under terskelverdiene, avrop på eksisterende avtaler, husleie og mye annet kommer ikke med, "
        f"så Doffin-summen er nesten alltid langt lavere enn kjøpene i regnskapet. Med en fast terskel på "
        f"{pct(ds.threshold, decimals=0)} blir derfor "
        f"{'alle' if all(a.flagged for a in ds.agencies) else 'nesten alle'} virksomhetene flagget. Et flagg betyr «verdt å se nærmere på», "
        f'ikke at noe er galt. <a href="{root}om.html#forbehold">Mer om forbeholdene</a>.</aside>'
    )


# --- forside ----------------------------------------------------------------

def _row_flags(a: Agency) -> str:
    """Nøkkelord som filteret på forsiden matcher mot (data-flags)."""
    tags = [a.direction]
    if a.flagged:
        tags.append("avvik")
    if a.coverage:
        tags.append(f"gruppe-{a.coverage.peer_group}")
        if a.coverage.flagged:
            tags += ["dekning", "dekning-hoy" if a.coverage.z > 0 else "dekning-lav"]
        elif a.coverage.category in COVERAGE_NOTES:
            tags.append(a.coverage.category.replace("_", "-"))
    return " ".join(tags)


def _year_nav(years: list[int], current: int, root: str) -> str:
    if len(years) < 2:
        return ""
    front = _FRONT["year"] or max(years)
    links = []
    for y in sorted(years, reverse=True):
        href = f"{root}index.html" if y == front else f"{root}ar/{y}.html"
        attr = ' aria-current="page"' if y == current else ""
        links.append(f'<a href="{href}"{attr}>{year_text(y, short=True)}</a>')
    return f'<nav class="years" aria-label="Regnskapsår"><span class="muted">År:</span> {"".join(links)}</nav>'


def _span(years: list[int]) -> str:
    return "" if not years else str(years[0]) if len(years) == 1 else f"{years[0]}–{years[-1]}"


def _front_hero(b: Breadth, ds: Dataset, root: str) -> str:
    """Forsidens toppfelt: hva Kronesporet kobler sammen, og hvor mye som er hentet inn."""
    figures = [
        (_group(b.contracts), f"kontrakter fra Doffin, {_span(b.contract_years)}"),
        (_group(b.suppliers), "leverandører med organisasjonsnummer"),
        (_group(b.buyers), "statlige kjøpere i Doffin"),
        (_group(b.ledger_rows), f"regnskapslinjer fra statsregnskapet, {_span(b.ledger_years)}"),
        (_group(b.units), "enheter og underenheter koblet via Enhetsregisteret"),
    ]
    if b.health and b.municipal:
        figures.append((_group(b.health + b.companies + b.municipal + b.municipal_companies),
                        f'<a href="{root}helseforetak.html">helseforetak</a>, '
                        + (f'<a href="{root}selskaper.html">statlige selskaper</a>, ' if b.companies else "")
                        + f'<a href="{root}kommuner.html">kommuner og fylkeskommuner</a>'
                        + (f' og <a href="{root}kommunale-selskaper.html">kommunale selskaper</a>'
                           if b.municipal_companies else "") + " utenfor statsregnskapet"))
    elif b.health:
        figures.append((_group(b.health), f'<a href="{root}helseforetak.html">helseforetak</a> med kontrakter og årsregnskap'))
    figs = "".join(f'<div class="figure"><div class="figure-value">{v}</div><div class="figure-label">{l}</div></div>'
                   for v, l in figures if v != "0")
    return f"""
<section class="hero-band" aria-labelledby="tittel">
  <p class="eyebrow">{"Offentlige" if b.municipal else "Statlige"} innkjøp · {_span(sorted(set(b.contract_years) | set(b.ledger_years)))}</p>
  <h1 id="tittel">Hvor går statens penger?</h1>
  <p class="lede">Kronesporet kobler hver kunngjorte kontrakt i Doffin med statsregnskapet og Enhetsregisteret,
  virksomhet for virksomhet og år for år. Da ser du hva staten kjøper, fra hvem, og hvor mye som aldri blir
  kunngjort. Ingen av kildene viser dette alene.{" Helseforetakene" + (", de statlige selskapene" if b.companies else "") + (", alle kommuner og fylkeskommuner og selskapene deres er også med." if b.municipal_companies else " og alle kommuner og fylkeskommuner er også med.") if b.health and b.municipal else ""}</p>
  <div class="figures">{figs}</div>
  <div class="hero-actions">
    <a class="button button-light" href="#virksomheter">Se virksomhetene {year_text(ds.year, short=True)}</a>
    <a class="button button-outline" href="{root}leverandorer.html">Søk blant leverandørene</a>
  </div>
</section>
"""


def _source_cards(b: Breadth, root: str) -> str:
    cards = [
        ("D", "Doffin", "Digdir", "https://doffin.no",
         f"{_group(b.contracts)} kunngjorte kontrakter med verdi, løpetid og vinnere, {_span(b.contract_years)}"
         + (f", hvorav {_group(b.joint)} felles anskaffelser med flere statlige kjøpere. " if b.joint else ". ")
         + 
         f"{_group(b.winners)} vinnerplasser gir {_group(b.suppliers)} leverandører med organisasjonsnummer."),
        ("S", "Statsregnskapet", "DFØ", "https://statsregnskapet.dfo.no",
         f"Månedlige rådata fra bevilgningsregnskapet: {_group(b.ledger_rows)} linjer fordelt på "
         f"{b.ledger_accounts} artskontoer" + (f" for {b.ledger_agencies} virksomheter" if b.ledger_agencies else "")
         + f", {_span(b.ledger_years)}. Viser hva virksomhetene faktisk betalte."),
        ("E", "Enhetsregisteret", "Brønnøysundregistrene", "https://www.brreg.no",
         f"{_group(b.units)} enheter, hvorav {_group(b.subunits)} underenheter som politidistrikter og "
         "Forsvarets avdelinger. Kontraktene deres regnes til riktig virksomhet."),
        ("R", "Regnskapsregisteret", "Brønnøysundregistrene", "https://www.brreg.no",
         f"Årsregnskapene til {b.health} helseforetak"
         + (f" og {b.companies_accounts} statlige selskaper" if b.companies_accounts else "")
         + f", {_span(b.health_years)}. De står ikke i statsregnskapet, men er med på egne sider for "
         f'<a href="{root}helseforetak.html">helseforetakene</a>'
         + (f' og <a href="{root}selskaper.html">selskapene</a>.' if b.companies else ".")),
        ("Ko", "KOSTRA", "SSB", "https://www.ssb.no/offentlig-sektor/kostra",
         f"Kjøpene til {b.municipal} kommuner og fylkeskommuner, {_span(b.municipal_years)}, sammenlignet med "
         f'{_group(b.municipal_contracts)} kontrakter i Doffin. <a href="{root}kommuner.html">Se kommunene</a>.'),
        ("K", "Kudos", "DFØ", "https://kudos.dfo.no",
         f"Årsrapporter fra statlige virksomheter. {_group(b.report_rows)} tall fyller hull der regnskapet "
         "mangler, og hver virksomhetsside lenker til rapporten."),
    ]
    items = "".join(
        f'<li class="source-card"><span class="source-mark" aria-hidden="true">{m}</span>'
        f'<div><h3><a href="{url}">{name}</a> <span class="source-owner">{owner}</span></h3>'
        f"<p>{text}</p></div></li>"
        for m, name, owner, url, text in cards if not (m == "K" and not b.report_rows)
        and not (m == "E" and not b.units) and not (m == "R" and not b.health)
        and not (name == "KOSTRA" and not b.municipal)
    )
    n = items.count("<li")
    count = {2: "To", 3: "Tre", 4: "Fire", 5: "Fem", 6: "Seks", 7: "Sju"}.get(n, str(n))
    return f"""
<section aria-labelledby="kilder">
  <p class="eyebrow eyebrow-dark">Datagrunnlaget</p>
  <h2 id="kilder" class="section-title">{count} offentlige kilder, koblet sammen</h2>
  <ul class="source-cards">{items}</ul>
  <p class="muted small">Alle kildene er åpne data under NLOD 2.0. <a href="{root}om.html#kobling">Slik kobles de</a>.</p>
</section>
"""


def _timeline(b: Breadth, years_with_pages: list[int], root: str) -> str:
    """Tidslinje: Doffin-kontrakter per tildelingsår, med årene statsregnskapet dekker markert under."""
    years = b.contract_years
    if len(years) < 2:
        return ""
    top = max(b.contracts_by_year.values())
    front = _FRONT["year"]
    cols = []
    for y in years:
        n = b.contracts_by_year[y]
        label = year_text(y, short=True) if y in _PARTIAL else str(y)
        href = (f"{root}index.html#virksomheter" if y == front else f"{root}ar/{y}.html"
                if y in years_with_pages else "")
        year = f'<a href="{href}">{y}</a>' if href else str(y)
        cols.append(f'<div class="tl-col" title="{label}: {_group(n)} kontrakter">'
                    f'<span class="tl-value">{_group(n)}</span>'
                    f'<span class="tl-bar" style="height:{max(n / top * 100, 2):.1f}%"></span>'
                    f'<span class="tl-year">{year}</span></div>')
    tracks = [("Doffin", years[0], years[-1], "tl-track-doffin")]
    if b.ledger_years:
        last = b.ledger_years[-1]
        partial = f", {last}: jan–{MONTHS[_PARTIAL[last] - 1][:3]}" if last in _PARTIAL else ""
        tracks.append(("Statsregnskapet", b.ledger_years[0], last, "tl-track-ledger",
                       f"{_span(b.ledger_years)}{partial}"))
    tracks[0] += (f"{_span(years)}, {_group(b.buyers)} kjøpere",)
    track_html = "".join(
        f'<div class="tl-track {cls}" style="grid-column:{years.index(a) + 1} / {years.index(z) + 2}">'
        f'{escape(t)}<span class="tl-detail"> {escape(detail)}</span></div>'
        for t, a, z, cls, detail in tracks if a in years and z in years
    )
    rows = "".join(f"<tr><th scope=\"row\">{y}</th><td>{_group(b.contracts_by_year[y])}</td></tr>" for y in years)
    return f"""
<section aria-labelledby="tidslinje">
  <p class="eyebrow eyebrow-dark">Tidslinje</p>
  <h2 id="tidslinje" class="section-title">{len(years)} år med kontrakter</h2>
  <p class="muted">Doffin-kontrakter med tildeling per år, for {_group(b.buyers)} statlige kjøpere{f" og {b.municipal} kommuner og fylkeskommuner" if b.municipal else ""}. Der statsregnskapet
  også finnes, sammenligner Kronesporet kontraktene med det virksomhetene faktisk betalte.</p>
  <figure class="timeline" style="--cols:{len(years)}">
    <div class="tl-chart" aria-hidden="true">{''.join(cols)}</div>
    <div class="tl-tracks" aria-hidden="true">{track_html}</div>
    <table class="visually-hidden"><caption>Doffin-kontrakter per tildelingsår</caption>
      <thead><tr><th scope="col">År</th><th scope="col">Kontrakter</th></tr></thead><tbody>{rows}</tbody></table>
  </figure>
</section>
"""


def index_page(ds: Dataset, root: str = "", years: list[int] | None = None,
               breadth: Breadth | None = None) -> str:
    n = len(ds.agencies)
    n_flag = sum(a.flagged for a in ds.agencies)
    stats = [
        (f"{n} av {n + len(ds.unmatched)}", '<a href="#uten-doffin">virksomheter i regnskapet</a> har Doffin-kontrakter i året')
        if ds.unmatched else (str(n), "statlige virksomheter med tall"),
        (pct(statistics.median(a.deviation for a in ds.agencies), signed=True) if n else "–",
         "median avvik, Doffin mot kjøp"),
        (f"{n_flag} av {n}", f"flagget for avvik over {pct(ds.threshold, decimals=0)}"),
    ]
    if ds.has_coverage:
        covered = [a for a in ds.agencies if a.coverage]
        n_cov = sum(a.coverage.flagged for a in covered)
        persistent = any(a.coverage.category for a in covered)
        stats.append((f"{n_cov} av {len(covered)}", "har uvanlig dekning flere år på rad" if persistent
                       else "har uvanlig høy eller lav dekning"))
    stat_html = "".join(f'<div class="stat"><div class="stat-value">{v}</div><div class="stat-label">{l}</div></div>'
                        for v, l in stats)

    doffin_label = f"Doffin {ds.year}" + (" (årsandel)" if ds.annualized else "")
    head = [
        ("Virksomhet", "text", ""),
        ("Kontrakter", "num", "Antall Doffin-kontrakter som løper i året" if ds.annualized
         else "Antall kontrakter tildelt i året"),
        (doffin_label, "num", "Den delen av kontraktsverdien som faller i regnskapsåret" if ds.annualized
         else "Sum tildelt kontraktsverdi i året"),
        (f"Kjøp {year_text(ds.year, short=True)}", "num", "Kjøp av varer og tjenester fra regnskapet"),
        ("Avvik", "num", "(Doffin − kjøp) / kjøp"),
    ]
    if ds.has_coverage:
        head.append(("Dekning", "num", "Doffin-summen som andel av kjøpene"))
    head.append(("Flagg", "text", ""))
    thead = "".join(
        f'<th scope="col" data-type="{t}"' + (f' title="{escape(tip)}"' if tip else "")
        + f'><button type="button">{escape(label)}</button></th>'
        for label, t, tip in head
    )

    rows = []
    for a in sorted(ds.agencies, key=lambda a: a.deviation):
        cov = ""
        if ds.has_coverage:
            cov = (f'<td class="num" data-sort="{_num(a.coverage.ratio)}">{pct(a.coverage.ratio)}</td>'
                   if a.coverage else '<td class="num" data-sort="">–</td>')
        flag_sort = ((4 if a.coverage and a.coverage.flagged else
                      2 if a.coverage and a.coverage.category in COVERAGE_NOTES else 0) + (1 if a.flagged else 0))
        rows.append(
            f'<tr data-name="{escape(a.name.lower())} {a.orgnr}" data-flags="{escape(_row_flags(a))}">'
            f'<td><a href="{root}virksomhet/{a.slug}.html">{escape(a.name)}</a></td>'
            f'<td class="num" data-sort="{a.contract_count}">{a.contract_count}</td>'
            f'<td class="num" data-sort="{_num(a.contract_sum)}" title="{kr_full(a.contract_sum)}">{kr(a.contract_sum)}</td>'
            f'<td class="num" data-sort="{_num(a.spend_sum)}" title="{kr_full(a.spend_sum)}">{kr(a.spend_sum)}</td>'
            f'<td class="num dev dev-{a.direction}" data-sort="{_num(a.deviation)}">{pct(a.deviation, signed=True)}</td>'
            f"{cov}"
            f'<td data-sort="{flag_sort}">{_flag_badges(a)}</td>'
            "</tr>"
        )

    options = [("", "Alle virksomheter"), ("avvik", f"Avvik over {pct(ds.threshold, decimals=0)}"),
               ("over", "Mer i Doffin enn i regnskapet"), ("under", "Mindre i Doffin enn i regnskapet")]
    if ds.has_coverage:
        options[1:1] = [("dekning", "Uvanlig dekning (alle)"), ("dekning-hoy", "Uvanlig høy dekning"),
                        ("dekning-lav", "Uvanlig lav dekning")]
        present = {a.coverage.category for a in ds.agencies if a.coverage and not a.coverage.flagged}
        options[4:4] = [(k.replace("_", "-"), label) for k, (label, _) in COVERAGE_NOTES.items() if k in present]
        groups = sorted({a.coverage.peer_group for a in ds.agencies if a.coverage},
                        key=lambda g: ["små", "mellomstore", "store"].index(g) if g in ("små", "mellomstore", "store") else 9)
        options += [(f"gruppe-{g}", f"{g.capitalize()} virksomheter") for g in groups]
    select = "".join(f'<option value="{escape(v)}">{escape(label)}</option>' for v, label in options)

    intro = (
        f"<p class=\"lede\">Hvor mye av statens innkjøp kan du faktisk se i Doffin? Kronesporet legger "
        f"kunngjorte kontrakter for {n} statlige virksomheter ved siden av hva de selv rapporterer at de "
        f"kjøpte varer og tjenester for i {year_text(ds.year)}.</p>"
    )
    front = _FRONT["year"] or max(years or [ds.year])
    if breadth and breadth.contracts and ds.year == front:
        top = (_front_hero(breadth, ds, root) + _source_cards(breadth, root)
               + _timeline(breadth, years or [ds.year], root)
               + f'''<section id="virksomheter" class="year-head" aria-labelledby="aaret">
  <p class="eyebrow eyebrow-dark">Regnskapsåret {year_text(ds.year)}</p>
  <h2 id="aaret" class="section-title">Kunngjort mot betalt, virksomhet for virksomhet</h2>
  {intro}
  {_year_nav(years or [ds.year], ds.year, root)}
</section>''')
    else:
        top = f'''<section class="hero">
  <h1>Hvor går statens penger?</h1>
  {intro}
  {_year_nav(years or [ds.year], ds.year, root)}
</section>'''
    body = f"""
{top}
<div class="stats">{stat_html}</div>
{_caveat(ds, root)}
{_partial_caveat(ds)}
<section>
  <div class="table-tools">
    <label for="filter">Søk etter virksomhet</label>
    <input id="filter" data-filter="agencies" type="search" placeholder="F.eks. vegvesen eller 971032081" autocomplete="off">
    <label for="flag-filter" class="visually-hidden">Vis</label>
    <select id="flag-filter" data-flag-filter="agencies">{select}</select>
    <span class="muted small" data-count-for="agencies" aria-live="polite"></span>
    <a class="download" href="{root}data/virksomheter.csv" download>Last ned CSV</a>
  </div>
  <div class="table-scroll">
  <table class="sortable" id="agencies">
    <caption class="visually-hidden">Statlige virksomheter {ds.year}: Doffin-kontrakter mot kjøp</caption>
    <thead><tr>{thead}</tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  </div>
  <p class="muted small">Klikk på en kolonne for å sortere. Beløp i kortform; hold pekeren over for nøyaktig tall.</p>
</section>
{_unmatched_section(ds, root)}
"""
    title = SITE_NAME if ds.year == front else f"Statlige virksomheter {year_text(ds.year, short=True)}"
    path = "index.html" if ds.year == front else f"ar/{ds.year}.html"
    return page(title, body, root=root, active="index", path=path,
                description=f"Doffin-kontrakter mot kjøp for {n} statlige virksomheter i {ds.year}.")


def _unmatched_section(ds: Dataset, root: str) -> str:
    """Virksomhetene i regnskapet som ikke har kontrakter i Doffin i året, så oversikten er komplett."""
    if not ds.unmatched:
        return ""
    n = len(ds.unmatched)
    total = sum(u.spend_sum for u in ds.unmatched)
    has_joint = any(u.joint for u in ds.unmatched)
    rows = []
    for u in ds.unmatched:
        last = (f'<td class="num" data-sort="{u.last_notice}">{u.last_notice}</td>' if u.last_notice
                else '<td class="num" data-sort="">–</td>')
        joint = f'<td class="num" data-sort="{u.joint}">{u.joint or "–"}</td>' if has_joint else ""
        rows.append(
            f'<tr data-name="{escape(u.name.lower())} {u.orgnr}">'
            f'<td>{escape(u.name)} <span class="orgnr">{u.orgnr}</span></td>'
            f'<td class="num" data-sort="{_num(u.spend_sum)}" title="{kr_full(u.spend_sum)}">{kr(u.spend_sum)}</td>'
            f'<td class="num" data-sort="{u.notices}">{u.notices}</td>{last}{joint}'
            f'<td class="small">{escape(u.note)}</td></tr>'
        )
    cols = [("Virksomhet", "text"), (f"Kjøp {year_text(ds.year, short=True)}", "num"),
            ("Kunngjøringer, alle år", "num"), ("Siste", "num")]
    if has_joint:
        cols.append(("Felles", "num"))
    head = "".join(
        f'<th scope="col" data-type="{t}"><button type="button">{label}</button></th>'
        for label, t in cols + [("Merknad", "text")]
    )
    return f"""
<section aria-labelledby="uten-doffin">
  <h2 id="uten-doffin">{n} virksomheter med kjøp, men uten Doffin-kontrakter i {year_text(ds.year)}</h2>
  <p>Disse virksomhetene kjøpte varer og tjenester for til sammen {kr(total)} i {year_text(ds.year)} ifølge
  statsregnskapet, men Doffin har ingen kontrakter med verdi for dem som løper i året, verken på eget
  organisasjonsnummer eller på underenhetene. Det betyr ikke at noe er galt. Kjøp under terskelverdiene
  kunngjøres ikke, mange bruker avtaler andre har inngått, som statlige fellesavtaler fra DFØ, og noen kjøper
  gjennom en annen virksomhet. De er med her så oversikten over statens kjøp er komplett.</p>
  <div class="table-scroll">
  <table class="sortable" id="uten-doffin-tabell">
    <caption class="visually-hidden">Virksomheter med kjøp i {ds.year}, uten kontrakter i Doffin</caption>
    <thead><tr>{head}</tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  </div>
  <p class="muted small">Med kjøp over 1 mill. kr. «Kunngjøringer, alle år» teller alle kontraktstildelinger i
  Doffin som Kronesporet har hentet der virksomheten eller en underenhet står som kjøper, og «Siste» er året for
  den siste.{" «Felles» er felles anskaffelser der virksomheten er en av flere kjøpere, men ikke står først. De er ikke med i sammenligningen, fordi Doffin ikke viser hvor stor del hver kjøper står for." if has_joint else ""}
  <a href="{root}om.html#kobling">Slik kobles kildene</a>.</p>
</section>
"""


# --- virksomhetsside ----------------------------------------------------------

def _contract_link(c: Contract) -> str:
    return f'<a href="{escape(c.url)}">{escape(c.title)}</a>' if c.url else escape(c.title)


def _period(c: Contract) -> str:
    if c.start_date and c.end_date:
        return f"{c.start_date[:7]} – {c.end_date[:7]}"
    if c.first_year and c.last_year:
        return str(c.first_year) if c.first_year == c.last_year else f"{c.first_year}–{c.last_year}"
    return "–"


def _supplier_link(name: str, orgnr: str | None, root: str, linked: set[str]) -> str:
    """Navn med lenke til leverandørsiden når den finnes (bare leverandører med orgnr får egen side)."""
    if orgnr and orgnr in linked:
        return f'<a href="{root}leverandor/{escape(orgnr)}.html">{escape(name)}</a>'
    return escape(name)


def _suppliers_cell(c: Contract, linked: set[str], root: str = "../") -> str:
    if not c.suppliers:
        return '<td class="muted" data-sort="">–</td>'
    names = [_supplier_link(w.name, w.orgnr, root, linked) for w in c.suppliers]
    full = escape("; ".join(f"{w.name} ({w.orgnr})" if w.orgnr else w.name for w in c.suppliers))
    shown = names if len(names) <= 3 else names[:2] + [f"og {len(names) - 2} til"]
    return (f'<td class="suppliers" data-sort="{escape(c.suppliers[0].name)}" title="{full}">'
            f'{"<br>".join(shown)}</td>')


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


def _spend_label(key: str) -> str:
    if key in SPEND_PARTS:
        return SPEND_PARTS[key]
    if key in ARTSKONTO_GROUPS:
        return f"{key} {ARTSKONTO_GROUPS[key]}"
    return f"Artskonto {key}" if key.isdigit() else key


def _spend_source_label(source: str) -> str:
    """'raadata_desember_2025_bevilgningsregnskap.xlsx' -> 'Statsregnskapet (rådata, desember 2025)'."""
    month, year = source_month(source), re.search(r"\d{4}", source)
    if month and year:
        return f"Statsregnskapet (rådata, {MONTHS[month - 1]} {year.group()})"
    return f"Statsregnskapet ({source})"


def year_text(year: int, short: bool = False) -> str:
    """'2025', eller for delår '2026 (jan–aug)' / 'januar–august 2026'."""
    month = _PARTIAL.get(year)
    if not month:
        return str(year)
    if short:
        return f"{year} (jan–{MONTHS[month - 1][:3]})"
    return f"januar–{MONTHS[month - 1]} {year}"


def _partial_caveat(ds: Dataset) -> str:
    if not ds.partial:
        return ""
    return (f'<aside class="caveat"><strong>Delår.</strong> Regnskapstallene for {ds.year} gjelder bare '
            f"januar–{MONTHS[ds.through_month - 1]}, og kontraktverdiene er fordelt på de samme månedene. Tallene kan "
            f"likevel ikke sammenlignes direkte med hele år, siden innkjøp og fakturering ofte samles mot "
            f"slutten av året.</aside>")


def _history_table(history: list[tuple[int, Agency]], current: int) -> str:
    """Utvikling over år for én virksomhet. history er (år, Agency)."""
    has_cov = any(a.coverage for _, a in history)
    head = ["År", "Kontrakter", "Doffin", "Kjøp", "Avvik"] + (["Dekning"] if has_cov else []) + ["Flagg"]
    thead = "".join(f'<th scope="col"{NUM_TYPE if 0 < i < len(head) - 1 else ""}>{h}</th>'
                    for i, h in enumerate(head))
    rows = []
    for year, a in sorted(history, key=lambda t: -t[0]):
        cov = (f'<td class="num">{pct(a.coverage.ratio) if a.coverage else "–"}</td>' if has_cov else "")
        mark = ' aria-current="true"' if year == current else ""
        rows.append(
            f'<tr{mark}><td>{year_text(year, short=True)}</td><td class="num">{a.contract_count}</td>'
            f'<td class="num" title="{kr_full(a.contract_sum)}">{kr(a.contract_sum)}</td>'
            f'<td class="num" title="{kr_full(a.spend_sum)}">{kr(a.spend_sum)}</td>'
            f'<td class="num dev dev-{a.direction}">{pct(a.deviation, signed=True)}</td>{cov}'
            f"<td>{_flag_badges(a)}</td></tr>")
    return (f'<section><h2>Utvikling</h2><div class="table-scroll"><table class="history">'
            f'<thead><tr>{thead}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')


def _contract_parts(ds: Dataset, a: Agency, linked: set[str]) -> tuple[str, str, str]:
    """(største leverandører, Doffin-kontraktene, felles anskaffelser) for en virksomhets- eller foretaksside."""
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
        if c.buyer_unit:
            tag_html += f'<span class="tag" title="Underenheten som står som kjøper i Doffin">{escape(c.buyer_unit)}</span>'
        cells = [f"<td>{link}{tag_html}</td>"]
        if ds.has_suppliers:
            cells.append(_suppliers_cell(c, linked))
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
            f"<tr><td>{_supplier_link(name, orgnr, '../', linked)}"
            + (f' <span class="orgnr">{escape(orgnr)}</span>' if orgnr else "")
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

    joint_html = ""
    if a.joint:
        jrows = "".join(
            f'<tr><td>{_contract_link(c)}'
            + ('<span class="tag">rammeavtale</span>' if c.is_framework else "")
            + f'</td><td>{escape(c.lead_buyer or "–")}</td><td class="num">{c.n_buyers}</td>'
            f'<td class="num" data-sort="{escape(c.published or "")}">{escape((c.published or "–")[:10])}</td>'
            f'<td class="num" data-sort="{_num(c.value)}" title="{kr_full(c.value)}">{kr(c.value)}</td></tr>'
            for c in a.joint)
        joint_html = f"""
<section>
  <h2>Felles anskaffelser ({len(a.joint)})</h2>
  <p class="muted">Kunngjøringer der {escape(a.name)} er en av flere kjøpere, men ikke står først. Verdien gjelder
  hele avtalen for alle kjøperne. Den er ikke med i Doffin-summen over, fordi Doffin ikke viser hvor stor del hver
  kjøper står for.</p>
  <div class="table-scroll"><table class="sortable" id="joint"><thead><tr>
  <th scope="col" data-type="text"><button type="button">Kontrakt</button></th>
  <th scope="col" data-type="text"><button type="button">Første kjøper</button></th>
  <th scope="col" data-type="num"><button type="button">Kjøpere</button></th>
  <th scope="col" data-type="num"><button type="button">Tildelt</button></th>
  <th scope="col" data-type="num"><button type="button">Hele verdien</button></th></tr></thead>
  <tbody>{jrows}</tbody></table></div>
</section>"""

    contracts_html = f"""<section>
  <h2>Doffin-kontrakter ({len(a.contracts)})</h2>
  <p class="muted">{explain}</p>
  {table}
</section>"""
    return suppliers_html, contracts_html, joint_html


def _follow(kind: str, orgnr: str, name: str) -> str:
    """Lenke til varselpåmeldingen med kjøperen eller leverandøren fylt inn, når varslene er satt opp."""
    if not ALERTS_API or not orgnr:
        return ""
    return (f'<p class="follow"><a href="../varsler.html?{kind}={quote(orgnr)}#meld-interesse">Få varsel om nye avtaler '
            f'{"hos" if kind == "kjoper" else "med"} {escape(name)}</a></p>')


def agency_page(ds: Dataset, a: Agency, history: list[tuple[int, Agency]] | None = None,
                linked: set[str] = frozenset()) -> str:
    facts = [
        ("Organisasjonsnummer", f'<a href="https://data.brreg.no/enhetsregisteret/oppslag/enheter/{escape(a.orgnr)}">'
                                f'{escape(a.orgnr)}</a>'),
        (f"Kjøp av varer og tjenester {year_text(ds.year)}", kr_full(a.spend_sum)),
    ]
    parts = [(f"\u2003{escape(_spend_label(k))}", kr_full(v))
             for k, v in sorted(a.spend_parts.items(), key=lambda kv: (not kv[0].isdigit(), kv[0].zfill(4)))]
    if len(parts) <= 3:
        facts += parts
    if a.spend_source:
        facts.append(("\u2003Kilde", f'<a href="https://statsregnskapet.dfo.no">'
                                      f'{escape(_spend_source_label(a.spend_source))}</a>'))
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
    parts_html = ""
    if len(parts) > 3:
        parts_html = ('<details class="parts"><summary>Kjøpene fordelt på artskonto</summary><dl class="facts">'
                      + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in parts) + "</dl></details>")

    notes = []
    if a.coverage and a.coverage.flagged:
        c = a.coverage
        side = "høyere" if c.z > 0 else "lavere"
        notes.append(
            f"<li><strong>Uvanlig {'høy' if c.z > 0 else 'lav'} Doffin-dekning.</strong> Doffin dekker {pct(c.ratio)} "
            f"av kjøpene, tydelig {side} enn de {c.peer_count} {escape(c.peer_group)} virksomhetene i samme "
            f"størrelsesgruppe (median {pct(c.peer_median)}, z\u00a0=\u00a0{_z(c.z)})"
            + (f", og dekningen har avveket {c.years_outlier} av de siste tre årene" if c.years_outlier > 1 else "")
            + ".</li>")
    elif a.coverage and a.coverage.category == "observasjon":
        c = a.coverage
        side = "høyere" if c.z > 0 else "lavere"
        notes.append(
            f"<li><strong>Avvik i dekning i år.</strong> Doffin dekker {pct(c.ratio)} av kjøpene, {side} enn de "
            f"{c.peer_count} {escape(c.peer_group)} virksomhetene i samme størrelsesgruppe (median "
            f"{pct(c.peer_median)}, z\u00a0=\u00a0{_z(c.z)}). Avviket gjelder bare dette året, så det er ikke "
            f"flagget; én stor rammeavtale kan være nok.</li>")
    elif a.coverage and a.coverage.category in ("tilskudd", "avtaler_for_andre"):
        c = a.coverage
        why = ("Tilskuddene ut av staten er minst like store som Doffin-andelen, så kontraktene betales trolig "
               "som tilskudd, for eksempel kjøp av fly- og togruter." if c.category == "tilskudd" else
               "Typisk inngår virksomheten avtaler som andre statlige virksomheter bruker, eller kjøpene føres "
               "utenfor kjøp av varer og tjenester.")
        notes.append(
            f"<li><strong>Dekning over 100 %.</strong> Doffin-andelen ({pct(c.ratio)} av kjøpene) er større enn "
            f"virksomhetens egne kjøp. {why} Det gir ikke flagg.</li>")
    if a.flagged:
        word = "høyere" if a.deviation > 0 else "lavere"
        notes.append(
            f"<li><strong>Avvik over {pct(ds.threshold, decimals=0)}.</strong> Doffin-summen er "
            f"{pct(abs(a.deviation))} {word} enn kjøpene i regnskapet. Det gjelder nesten alle virksomhetene, "
            f"se <a href=\"../om.html#forbehold\">forbeholdene</a>.</li>")
    notes_html = f'<ul class="notes">{"".join(notes)}</ul>' if notes else ""

    report = (f'<a class="button" href="{escape(a.report_url)}">Årsrapport {ds.year} i Kudos (PDF)</a>'
              if a.report_url else "")

    suppliers_html, contracts_html, joint_html = _contract_parts(ds, a, linked)
    body = f"""
<p class="crumbs"><a href="../index.html">Alle virksomheter</a></p>
<section class="hero">
  <h1>{escape(a.name)}</h1>
  <div class="badges">{_flag_badges(a)}</div>{_follow("kjoper", a.orgnr, a.name)}
</section>
<div class="two-col">
  <div><dl class="facts">{facts_html}</dl>{parts_html}</div>
  <div>
    {notes_html}
    <p class="actions">{report}</p>
  </div>
</div>
{_history_table(history, ds.year) if history and len(history) > 1 else ""}
{suppliers_html}
{contracts_html}
{joint_html}
"""
    return page(a.name, body, root="../", description=f"Doffin-kontrakter og kjøp for {a.name} i {ds.year}.",
                path=f"virksomhet/{a.slug}.html")


# --- helseforetak -------------------------------------------------------------

HEALTH_ACCOUNTS_URL = "https://virksomhet.brreg.no/nb/oppslag/enheter/{}"
HEALTH_COSTS = "Driftskostnader utenom lønn"
HEALTH_NOTES = {
    "916879067": "Sykehusinnkjøp inngår nasjonale avtaler som helseforetakene bruker. Doffin-andelen blir derfor "
                 "høy her og lavere hos foretakene som handler på avtalene.",
}


def _is_rhf(a: Agency) -> bool:
    return a.name.endswith(" RHF")


def _health_share(a: Agency) -> float | None:
    return a.contract_sum / a.spend_sum if a.spend_sum else None


def _health_caveat(root: str = "") -> str:
    return (
        '<aside class="caveat"><strong>Ikke samme mål som for virksomhetene.</strong> Helseforetakene står ikke i '
        "statsregnskapet. Årsregnskapet viser ikke kjøp av varer og tjenester for seg, så Kronesporet bruker "
        f"driftskostnadene utenom lønn. De tar også med avskrivninger, gjestepasienter og kjøp fra private "
        "sykehus. Doffin-andelen er derfor lavere enn den ville vært mot rene kjøp, og foretakene flagges ikke. "
        f'<a href="{root}om.html#helseforetak">Mer om helseforetakene</a>.</aside>')


def health_index_page(datasets: list[Dataset], front: Dataset) -> str:
    year = front.year
    totals = []
    for d in sorted(datasets, key=lambda d: -d.year):
        if not d.health:
            continue
        doffin = sum(a.contract_sum for a in d.health)
        costs = sum(a.spend_sum for a in d.health if not _is_rhf(a))  # RHF-ene betaler mest foretakene sine
        n = sum(a.contract_count for a in d.health)
        totals.append(
            f'<tr{ROW_CURRENT if d is front else ""}><td>{year_text(d.year, short=True)}</td><td class="num">{_group(n)}</td>'
            f'<td class="num" title="{kr_full(doffin)}">{kr(doffin)}</td>'
            f'<td class="num" title="{kr_full(costs)}">{kr(costs) if costs else "–"}</td>'
            f'<td class="num">{pct(doffin / costs) if costs else "–"}</td></tr>')
    rows = []
    for a in sorted(front.health, key=lambda a: -a.spend_sum):
        share = _health_share(a)
        rows.append(
            f'<tr data-name="{escape(a.name.lower())} {a.orgnr} {escape(a.owner.lower())}">'
            f'<td><a href="{a.path}">{escape(a.name)}</a></td><td>{escape(a.owner)}</td>'
            f'<td class="num" data-sort="{a.contract_count}">{a.contract_count}</td>'
            f'<td class="num" data-sort="{_num(a.contract_sum)}" title="{kr_full(a.contract_sum)}">{kr(a.contract_sum)}</td>'
            f'<td class="num" data-sort="{_num(a.spend_sum)}" title="{kr_full(a.spend_sum)}">'
            f'{kr(a.spend_sum) if a.spend_sum else "–"}</td>'
            f'<td class="num" data-sort="{_num(share)}">{pct(share) if share is not None else "–"}</td></tr>')
    n_contracts = len({c.id for d in datasets for a in d.health for c in a.contracts})
    lead = max(front.health, key=lambda a: a.contract_count, default=None)
    total = sum(a.contract_count for a in front.health)
    missing = [d.year for d in sorted(datasets, key=lambda d: d.year)
               if d.health and not any(a.spend_source for a in d.health)]
    body = f"""
<section class="hero">
  <h1>Helseforetakene</h1>
  <p class="lede">Sykehusene kjøper for milliarder hvert år, men står ikke i statsregnskapet. Kronesporet henter
  kontraktene til {len(front.health)} helseforetak, de fire regionale og foretakene de eier, i Doffin, og
  driftskostnadene fra årsregnskapene i Regnskapsregisteret. {_group(n_contracts)} kontrakter løper i årene i
  oversikten.</p>
</section>
{f'<p>De fleste innkjøpene kunngjøres av {escape(lead.name)} på vegne av foretakene: {lead.contract_count} av '
 f'{total} kontrakter som løper i {year}. Derfor har mange sykehus få eller ingen kontrakter i eget navn.</p>'
 if lead and total and lead.contract_count * 2 > total else ""}
{_health_caveat()}
<section>
  <h2>Utvikling, alle foretakene</h2>
  <div class="table-scroll"><table class="history"><thead><tr><th scope="col">År</th>
  <th scope="col" data-type="num">Kontrakter</th><th scope="col" data-type="num">Doffin, årsandel</th>
  <th scope="col" data-type="num">{HEALTH_COSTS}</th><th scope="col" data-type="num">Doffin-andel</th></tr></thead>
  <tbody>{"".join(totals)}</tbody></table></div>
  <p class="muted small">Driftskostnadene i totalen gjelder helseforetakene. De fire RHF-enes kostnader er holdt
  utenfor, fordi de for det meste er betaling til foretakene de eier, og ville blitt telt to ganger.</p>
  {f'<p class="muted small">Årsregnskapet for {" og ".join(map(str, missing))} er ikke levert ennå, så '
     f'{"året" if len(missing) == 1 else "årene"} har bare Doffin-tall.</p>' if missing else ""}
</section>
<section>
  <h2>Foretakene i {year}</h2>
  <div class="table-tools"><label for="filter">Søk etter foretak</label>
  <input id="filter" data-filter="health" type="search" placeholder="Navn, orgnr eller eier" autocomplete="off">
  <span class="muted small" data-count-for="health" aria-live="polite"></span></div>
  <div class="table-scroll"><table class="sortable" id="health">
  <caption class="visually-hidden">Helseforetakene i {year}: Doffin-kontrakter og driftskostnader</caption>
  <thead><tr><th scope="col" data-type="text"><button type="button">Foretak</button></th>
  <th scope="col" data-type="text"><button type="button">Eier</button></th>
  <th scope="col" data-type="num"><button type="button">Kontrakter</button></th>
  <th scope="col" data-type="num"><button type="button">Doffin, andel {year}</button></th>
  <th scope="col" data-type="num"><button type="button">{HEALTH_COSTS}</button></th>
  <th scope="col" data-type="num"><button type="button">Doffin-andel</button></th></tr></thead>
  <tbody>{"".join(rows)}</tbody></table></div>
</section>
"""
    return page("Helseforetakene", body, active="helseforetak", path="helseforetak.html",
                description=f"Doffin-kontraktene og driftskostnadene til de {len(front.health)} helseforetakene.")


def health_page(ds: Dataset, a: Agency, history: list[tuple[int, Agency]], linked: set[str] = frozenset()) -> str:
    share = _health_share(a)
    accounts = (f'<a href="{HEALTH_ACCOUNTS_URL.format(escape(a.orgnr))}">{kr_full(a.spend_sum)}</a>'
                if a.spend_source else f'<span class="muted">Årsregnskapet for {ds.year} er ikke levert ennå</span>')
    facts = [
        ("Organisasjonsnummer", f'<a href="https://data.brreg.no/enhetsregisteret/oppslag/enheter/{escape(a.orgnr)}">'
                                f'{escape(a.orgnr)}</a>'),
        ("Eier", escape(a.owner) or "–"),
        (f"{HEALTH_COSTS} {year_text(ds.year)}", accounts),
        (f"Doffin-kontrakter som løper i {ds.year}", str(a.contract_count)),
        (f"Årsandel av kontraktsverdien {ds.year}", kr_full(a.contract_sum)),
        (f"Tildelt i {ds.year} (hele verdien, {a.awarded_in_year_count} stk.)", kr_full(a.awarded_in_year_sum)),
        ("Doffin-andel av driftskostnadene", pct(share) if share is not None else "–"),
    ]
    facts_html = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)
    note = HEALTH_NOTES.get(a.orgnr)
    hrows = "".join(
        f'<tr{ROW_CURRENT if y == ds.year else ""}><td>{year_text(y, short=True)}</td><td class="num">{h.contract_count}</td>'
        f'<td class="num" title="{kr_full(h.contract_sum)}">{kr(h.contract_sum)}</td>'
        f'<td class="num" title="{kr_full(h.spend_sum)}">{kr(h.spend_sum) if h.spend_sum else "–"}</td>'
        f'<td class="num">{pct(_health_share(h)) if _health_share(h) is not None else "–"}</td></tr>'
        for y, h in sorted(history, key=lambda t: -t[0]))
    suppliers_html, contracts_html, joint_html = _contract_parts(ds, a, linked)
    body = f"""
<p class="crumbs"><a href="../helseforetak.html">Alle helseforetak</a></p>
<section class="hero"><h1>{escape(a.name)}</h1>{_follow("kjoper", a.orgnr, a.name)}</section>
<div class="two-col">
  <div><dl class="facts">{facts_html}</dl></div>
  <div>{f'<ul class="notes"><li>{escape(note)}</li></ul>' if note else ""}{_health_caveat("../")}</div>
</div>
<section><h2>Utvikling</h2><div class="table-scroll"><table class="history"><thead><tr><th scope="col">År</th>
<th scope="col" data-type="num">Kontrakter</th><th scope="col" data-type="num">Doffin</th>
<th scope="col" data-type="num">{HEALTH_COSTS}</th><th scope="col" data-type="num">Doffin-andel</th></tr></thead>
<tbody>{hrows}</tbody></table></div></section>
{suppliers_html}
{contracts_html}
{joint_html}
"""
    return page(a.name, body, root="../", active="helseforetak", path=a.path,
                description=f"Doffin-kontrakter og driftskostnader for {a.name} i {ds.year}.")


# --- statlige selskaper ------------------------------------------------------

COMPANY_FORMS = {"AS": "Aksjeselskap", "ASA": "Allmennaksjeselskap", "SF": "Statsforetak", "SÆR": "Særlovsselskap",
                 "STI": "Stiftelse", "ANNA": "Annen juridisk person", "KIRK": "Kirkelig enhet", "FLI": "Forening",
                 "IKS": "Interkommunalt selskap", "KF": "Kommunalt foretak", "FKF": "Fylkeskommunalt foretak",
                 "SA": "Samvirkeforetak"}


def _company_caveat(root: str = "", level: str = "stat") -> str:
    examples = (("statsregnskapet", "bygger flyplasser eller kjøper tog (Avinor, Norske tog)", "Norges Bank")
                if level == "stat" else
                ("KOSTRA", "bygger anlegg eller kjøper busser og trikker", "mange interkommunale selskaper"))
    return (
        '<aside class="caveat"><strong>Ikke samme mål som for virksomhetene.</strong> Selskapene står ikke i '
        f"{examples[0]}. Kronesporet bruker driftskostnadene utenom lønn fra årsregnskapet i Regnskapsregisteret, "
        "som også tar med avskrivninger og andre kostnader enn kjøp. Investeringer er ikke med, så selskaper som "
        f"{examples[1]}, kan få en Doffin-andel over 100 %. Noen, som {examples[2]}, leverer ikke årsregnskap "
        "dit og har bare Doffin-tall. Selskapene flagges ikke. "
        f'<a href="{root}om.html#selskaper">Mer om selskapene</a>.</aside>')


COMPANY_PAGES = {"stat": ("selskaper.html", "Statlige selskaper", "Alle statlige selskaper"),
                 "kommune": ("kommunale-selskaper.html", "Kommunale selskaper", "Alle kommunale selskaper")}


def _companies_of(d: Dataset, level: str) -> list[Agency]:
    return d.companies if level == "stat" else d.municipal_companies


def company_index_page(datasets: list[Dataset], front: Dataset, level: str = "stat") -> str:
    year = front.year
    path, title, _ = COMPANY_PAGES[level]
    other_path, other_title, _ = COMPANY_PAGES["kommune" if level == "stat" else "stat"]
    current = _companies_of(front, level)
    totals = []
    for d in sorted(datasets, key=lambda d: -d.year):
        units = _companies_of(d, level)
        if not units:
            continue
        doffin = sum(a.contract_sum for a in units)
        costs = sum(a.spend_sum for a in units)
        with_costs = sum(a.contract_sum for a in units if a.spend_sum)
        n = sum(a.contract_count for a in units)
        totals.append(
            f'<tr{ROW_CURRENT if d is front else ""}><td>{year_text(d.year, short=True)}</td><td class="num">{_group(n)}</td>'
            f'<td class="num" title="{kr_full(doffin)}">{kr(doffin)}</td>'
            f'<td class="num" title="{kr_full(costs)}">{kr(costs) if costs else "–"}</td>'
            f'<td class="num">{pct(with_costs / costs) if costs else "–"}</td></tr>')
    rows = []
    for a in sorted(current, key=lambda a: (-a.contract_sum, a.name.lower())):
        share = _health_share(a)
        form = COMPANY_FORMS.get(a.owner, a.owner)
        rows.append(
            f'<tr data-name="{escape(a.name.lower())} {a.orgnr} {escape(form.lower())}">'
            f'<td><a href="{a.path}">{escape(a.name)}</a></td><td>{escape(form)}</td>'
            f'<td class="num" data-sort="{a.contract_count}">{a.contract_count}</td>'
            f'<td class="num" data-sort="{_num(a.contract_sum)}" title="{kr_full(a.contract_sum)}">{kr(a.contract_sum)}</td>'
            f'<td class="num" data-sort="{_num(a.spend_sum)}" title="{kr_full(a.spend_sum)}">'
            f'{kr(a.spend_sum) if a.spend_sum else "–"}</td>'
            f'<td class="num" data-sort="{_num(share)}">{pct(share) if share is not None else "–"}</td></tr>')
    n_contracts = len({c.id for d in datasets for a in _companies_of(d, level) for c in a.contracts})
    missing = [d.year for d in sorted(datasets, key=lambda d: d.year)
               if _companies_of(d, level) and not any(a.spend_source for a in _companies_of(d, level))]
    lede = (f"""Statsforetak og statlig eide selskaper som Bane NOR, Avinor, Nye Veier og NRK kjøper for store
  beløp, men står ikke i statsregnskapet. Kronesporet finner {len(current)} slike kjøpere i Doffin ut fra
  hvilken sektor Enhetsregisteret plasserer dem i, og henter driftskostnadene fra årsregnskapene."""
            if level == "stat" else
            f"""Interkommunale selskaper og selskaper kommunene eier, som Ruter, BIR, IVAR og Kolumbus, kjøper
  kollektivtransport, renovasjon, vann og avløp for kommunene. Kronesporet finner {len(current)} slike kjøpere i
  Doffin ut fra hvilken sektor Enhetsregisteret plasserer dem i, og henter driftskostnadene fra årsregnskapene.
  De regnes ikke til én kommune, fordi mange har flere eiere.""")
    other = _companies_of(front, "kommune" if level == "stat" else "stat")
    body = f"""
<section class="hero">
  <h1>{title}</h1>
  <p class="lede">{lede} {_group(n_contracts)} kontrakter løper i årene i oversikten.</p>
</section>
{f'<p>Se også <a href="{other_path}">{other_title.lower()}</a> ({len(other)}).</p>' if other else ""}
{_company_caveat(level=level)}
<section>
  <h2>Utvikling, alle selskapene</h2>
  <div class="table-scroll"><table class="history"><thead><tr><th scope="col">År</th>
  <th scope="col" data-type="num">Kontrakter</th><th scope="col" data-type="num">Doffin, årsandel</th>
  <th scope="col" data-type="num">{HEALTH_COSTS}</th><th scope="col" data-type="num">Doffin-andel</th></tr></thead>
  <tbody>{"".join(totals)}</tbody></table></div>
  <p class="muted small">Doffin-andelen regnes bare for selskapene med årsregnskap i Regnskapsregisteret.</p>
  {f'<p class="muted small">Årsregnskapene for {" og ".join(map(str, missing))} er ikke levert ennå, så '
     f'{"året" if len(missing) == 1 else "årene"} har bare Doffin-tall.</p>' if missing else ""}
</section>
<section>
  <h2>Selskapene i {year}</h2>
  <div class="table-tools"><label for="filter">Søk etter selskap</label>
  <input id="filter" data-filter="companies" type="search" placeholder="Navn, orgnr eller type" autocomplete="off">
  <span class="muted small" data-count-for="companies" aria-live="polite"></span></div>
  <div class="table-scroll"><table class="sortable" id="companies">
  <caption class="visually-hidden">{title} i {year}: Doffin-kontrakter og driftskostnader</caption>
  <thead><tr><th scope="col" data-type="text"><button type="button">Selskap</button></th>
  <th scope="col" data-type="text"><button type="button">Type</button></th>
  <th scope="col" data-type="num"><button type="button">Kontrakter</button></th>
  <th scope="col" data-type="num"><button type="button">Doffin, andel {year}</button></th>
  <th scope="col" data-type="num"><button type="button">{HEALTH_COSTS}</button></th>
  <th scope="col" data-type="num"><button type="button">Doffin-andel</button></th></tr></thead>
  <tbody>{"".join(rows)}</tbody></table></div>
</section>
"""
    return page(title, body, active="selskaper", path=path,
                description=f"Doffin-kontraktene og driftskostnadene til {len(current)} {title.lower()}.")


def company_page(ds: Dataset, a: Agency, history: list[tuple[int, Agency]], linked: set[str] = frozenset()) -> str:
    share = _health_share(a)
    accounts = (f'<a href="{HEALTH_ACCOUNTS_URL.format(escape(a.orgnr))}">{kr_full(a.spend_sum)}</a>'
                if a.spend_source else '<span class="muted">Ikke i Regnskapsregisteret for året</span>')
    facts = [
        ("Organisasjonsnummer", f'<a href="https://data.brreg.no/enhetsregisteret/oppslag/enheter/{escape(a.orgnr)}">'
                                f'{escape(a.orgnr)}</a>'),
        ("Type", escape(COMPANY_FORMS.get(a.owner, a.owner)) or "–"),
        (f"{HEALTH_COSTS} {year_text(ds.year)}", accounts),
        (f"Doffin-kontrakter som løper i {ds.year}", str(a.contract_count)),
        (f"Årsandel av kontraktsverdien {ds.year}", kr_full(a.contract_sum)),
        (f"Tildelt i {ds.year} (hele verdien, {a.awarded_in_year_count} stk.)", kr_full(a.awarded_in_year_sum)),
        ("Doffin-andel av driftskostnadene", pct(share) if share is not None else "–"),
    ]
    facts_html = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)
    hrows = "".join(
        f'<tr{ROW_CURRENT if y == ds.year else ""}><td>{year_text(y, short=True)}</td><td class="num">{h.contract_count}</td>'
        f'<td class="num" title="{kr_full(h.contract_sum)}">{kr(h.contract_sum)}</td>'
        f'<td class="num" title="{kr_full(h.spend_sum)}">{kr(h.spend_sum) if h.spend_sum else "–"}</td>'
        f'<td class="num">{pct(_health_share(h)) if _health_share(h) is not None else "–"}</td></tr>'
        for y, h in sorted(history, key=lambda t: -t[0]))
    suppliers_html, contracts_html, joint_html = _contract_parts(ds, a, linked)
    body = f"""
<p class="crumbs"><a href="../{COMPANY_PAGES[a.owner_level or "stat"][0]}">{COMPANY_PAGES[a.owner_level or "stat"][2]}</a></p>
<section class="hero"><h1>{escape(a.name)}</h1>{_follow("kjoper", a.orgnr, a.name)}</section>
<div class="two-col">
  <div><dl class="facts">{facts_html}</dl></div>
  <div>{_company_caveat("../", a.owner_level or "stat")}</div>
</div>
<section><h2>Utvikling</h2><div class="table-scroll"><table class="history"><thead><tr><th scope="col">År</th>
<th scope="col" data-type="num">Kontrakter</th><th scope="col" data-type="num">Doffin</th>
<th scope="col" data-type="num">{HEALTH_COSTS}</th><th scope="col" data-type="num">Doffin-andel</th></tr></thead>
<tbody>{hrows}</tbody></table></div></section>
{suppliers_html}
{contracts_html}
{joint_html}
"""
    return page(a.name, body, root="../", active="selskaper", path=a.path,
                description=f"Doffin-kontrakter og driftskostnader for {a.name} i {ds.year}.")


# --- kommuner -----------------------------------------------------------------

MUNICIPAL_PARTS = {"drift": "Kjøp av varer og tjenester", "erstatter": "Kjøp som erstatter egen produksjon",
                   "investering": "Investeringer i bygg, anlegg og utstyr"}
MUNICIPAL_KIND = {"kommune": "Kommune", "fylke": "Fylkeskommune"}
MUNICIPAL_REFORM = 2024  # fylkesdelingen; Ålesund og Haram ble delt, og sju fylkeskommuner opprettet


def _coverage_badge(a: Agency) -> str:
    c = a.coverage
    if c and c.category == "avtaler_for_andre":
        return ('<span class="badge badge-note" title="Doffin-andelen er større enn kommunens egne kjøp, typisk fordi '
                'kommunen kunngjør avtaler for et innkjøpssamarbeid">Over 100 %: avtaler for andre</span>')
    if not c or not c.outlier:
        return ""
    word = "høy" if c.z > 0 else "lav"
    if c.flagged:
        return (f'<span class="badge badge-strong" title="Avviker fra lignende kommuner to år på rad">'
                f'Uvanlig {word} dekning</span>')
    return f'<span class="badge badge-note" title="Avviker fra lignende kommuner dette året">Avvik i dekning i år</span>'


def _municipal_caveat(root: str = "") -> str:
    return (
        '<aside class="caveat"><strong>Les dette først.</strong> Kjøpene er konserntallene i KOSTRA: varer og '
        "tjenester, kjøp som erstatter egen produksjon (som private barnehager og interkommunale selskaper) og "
        "investeringer. Kommunene kjøper mye under terskelverdiene og gjennom avtaler andre har inngått, så "
        "Doffin dekker en liten del. Dekningen sammenlignes derfor med kommuner av lignende størrelse, og bare "
        "avvik som går igjen to år på rad, merkes som uvanlige. "
        f'<a href="{root}om.html#kommuner">Mer om kommunene</a>.</aside>')


def _municipal_missing(missing: list[int], have: list[int]) -> str:
    """Forklaring på år uten KOSTRA-tall: før dagens kommuneinndeling, eller ikke publisert ennå."""
    if not missing or not have:
        return ""
    before = [y for y in missing if y < min(have)]
    after = [y for y in missing if y > max(have)]
    parts = []
    if before:
        parts.append(f"{' og '.join(map(str, before))} er ikke med i sammenligningen ennå, fordi fylkene og noen "
                     f"kommuner ble delt i {min(have)} og KOSTRA-tallene følger den nye inndelingen")
    if after:
        parts.append(f"KOSTRA for {' og '.join(map(str, after))} er ikke publisert ennå")
    text = "; ".join(parts)
    return f'<p class="muted small">{text[:1].upper() + text[1:]}. Disse årene har bare Doffin-tall.</p>'


def municipal_index_page(datasets: list[Dataset], front: Dataset,
                         history: dict[str, list[tuple[int, Agency]]] | None = None) -> str:
    year = front.year
    by_year = {d.year: d.municipal for d in datasets if d.municipal}
    for rows_ in (history or {}).values():
        for y, a in rows_:
            if y not in {d.year for d in datasets}:
                by_year.setdefault(y, []).append(a)
    totals = []
    for y in sorted(by_year, reverse=True):
        units = by_year[y]
        n = sum(a.contract_count for a in units)
        # dekningen regnes bare for kommunene med KOSTRA-tall i året (før 2024 mangler de nye fylkeskommunene)
        doffin = sum(a.contract_sum for a in units if a.spend_sum)
        spend = sum(a.spend_sum for a in units)
        shown = sum(a.contract_sum for a in units)
        totals.append(
            f'<tr{ROW_CURRENT if y == front.year else ""}><td>{year_text(y, short=True)}</td><td class="num">{_group(n)}</td>'
            f'<td class="num" title="{kr_full(shown)}">{kr(shown)}</td>'
            f'<td class="num" title="{kr_full(spend)}">{kr(spend) if spend else "–"}</td>'
            f'<td class="num">{pct(doffin / spend) if spend else "–"}</td>'
            f'<td class="num">{sum(1 for a in units if a.spend_sum) or "–"}</td></tr>')
    missing = [d.year for d in sorted(datasets, key=lambda d: d.year)
               if d.municipal and not any(a.spend_source for a in d.municipal)]
    rows = []
    for a in sorted(front.municipal, key=lambda a: -a.spend_sum):
        c = a.coverage
        share = a.contract_sum / a.spend_sum if a.spend_sum else None
        flags = [a.owner] + ([("dekning-hoy" if c.z > 0 else "dekning-lav"), "avvik"] if c and c.outlier else []) + (
            ["over-100"] if c and c.category == "avtaler_for_andre" else []) + (
            ["dekning"] if c and c.flagged else []) + ([] if a.contract_count else ["ingen"])
        rows.append(
            f'<tr data-name="{escape(a.name.lower())} {a.orgnr}" data-flags="{" ".join(flags)}">'
            f'<td><a href="{a.path}">{escape(a.name)}</a></td>'
            f'<td class="num" data-sort="{a.contract_count}">{a.contract_count}</td>'
            f'<td class="num" data-sort="{_num(a.contract_sum)}" title="{kr_full(a.contract_sum)}">{kr(a.contract_sum)}</td>'
            f'<td class="num" data-sort="{_num(a.spend_sum)}" title="{kr_full(a.spend_sum)}">'
            f'{kr(a.spend_sum) if a.spend_sum else "–"}</td>'
            f'<td class="num" data-sort="{_num(share)}">{pct(share) if share is not None else "–"}</td>'
            f'<td class="num" data-sort="{_num(c.peer_median) if c else ""}" title="{escape(c.peer_group) if c else ""}">'
            f'{pct(c.peer_median) if c else "–"}</td>'
            f'<td>{_coverage_badge(a) or ""}</td></tr>')
    n_flag = sum(1 for a in front.municipal if a.coverage and a.coverage.flagged)
    n_none = sum(1 for a in front.municipal if not a.contract_count)
    n_contracts = len({c.id for d in datasets for a in d.municipal for c in a.contracts})
    n_k = sum(1 for a in front.municipal if a.owner == "kommune")
    split = {a.orgnr: a for d in datasets for a in d.municipal if a.until_year and d.year == a.until_year}
    split_html = ""
    if split:
        srows = "".join(
            f'<tr><td><a href="{a.path}">{escape(a.name)}</a></td><td>2020–{a.until_year}</td>'
            f'<td class="num">{a.contract_count}</td>'
            f'<td class="num" title="{kr_full(a.spend_sum)}">{kr(a.spend_sum) if a.spend_sum else "–"}</td>'
            f'<td class="num">{pct(a.contract_sum / a.spend_sum) if a.spend_sum else "–"}</td></tr>'
            for a in sorted(split.values(), key=lambda a: a.name))
        last = max(a.until_year for a in split.values())
        split_html = f"""
<section>
  <h2>Fylkeskommuner som ble delt i {last + 1}</h2>
  <p>Viken, Vestfold og Telemark og Troms og Finnmark ble delt i sju fylkeskommuner i {last + 1}. De er med for årene
  de fantes, med kontraktene sine og KOSTRA-tallene til og med {last}.</p>
  <div class="table-scroll"><table class="history"><thead><tr><th scope="col">Fylkeskommune</th><th scope="col">År</th>
  <th scope="col" data-type="num">Kontrakter {last}</th><th scope="col" data-type="num">Kjøp {last} (KOSTRA)</th>
  <th scope="col" data-type="num">Dekning {last}</th></tr></thead>
  <tbody>{srows}</tbody></table></div>
</section>"""
    body = f"""
<section class="hero">
  <h1>Kommuner og fylker</h1>
  <p class="lede">Kommunene og fylkeskommunene står for en stor del av offentlige innkjøp. Kronesporet kobler
  kontraktene til alle {n_k} kommuner og {len(front.municipal) - n_k} fylkeskommuner i Doffin med kjøpene de
  rapporterer til SSB (KOSTRA). {_group(n_contracts)} kontrakter løper i årene i oversikten.</p>
</section>
{_municipal_caveat()}
{f'<p>Interkommunale selskaper og selskaper kommunene eier, som Ruter og BIR, har en egen side: '
 f'<a href="kommunale-selskaper.html">{len(front.municipal_companies)} kommunale selskaper</a>.</p>'
 if front.municipal_companies else ""}
<div class="stats">
  <div class="stat"><div class="stat-value">{pct(sum(a.contract_sum for a in front.municipal) / (sum(a.spend_sum for a in front.municipal) or 1))}</div>
  <div class="stat-label">av kjøpene i {year} finnes igjen i Doffin</div></div>
  <div class="stat"><div class="stat-value">{n_flag}</div><div class="stat-label">har uvanlig dekning to år på rad</div></div>
  <div class="stat"><div class="stat-value">{n_none}</div><div class="stat-label">har ingen kontrakter som løper i {year}</div></div>
</div>
<section>
  <h2>Utvikling, alle kommunene og fylkene</h2>
  <div class="table-scroll"><table class="history"><thead><tr><th scope="col">År</th>
  <th scope="col" data-type="num">Kontrakter</th><th scope="col" data-type="num">Doffin, årsandel</th>
  <th scope="col" data-type="num">Kjøp (KOSTRA)</th><th scope="col" data-type="num">Dekning</th>
  <th scope="col" data-type="num">Med KOSTRA</th></tr></thead>
  <tbody>{"".join(totals)}</tbody></table></div>
  <p class="muted small">Før 2024 er Viken, Vestfold og Telemark og Troms og Finnmark med i stedet for de sju
  fylkeskommunene som ble opprettet i 2024, og Ålesund og Haram (delt i 2024) mangler. Dekningen regnes bare for kommunene og fylkeskommunene med KOSTRA-tall i året. Doffin-tallene går
  tilbake til 2017, så eldre kontrakter som fortsatt løpte i 2020 og 2021, mangler, og dekningen de første årene er
  derfor lavere.</p>
  {_municipal_missing(missing, [d.year for d in datasets if any(a.spend_source for a in d.municipal)])}
</section>
<section>
  <h2>Kommunene og fylkene i {year}</h2>
  <div class="table-tools">
    <label for="filter">Søk etter kommune</label>
    <input id="filter" data-filter="municipal" type="search" placeholder="Navn eller orgnr" autocomplete="off">
    <label for="flag-filter">Vis</label>
    <select id="flag-filter" data-flag-filter="municipal">
      <option value="">Alle</option><option value="kommune">Kommuner</option><option value="fylke">Fylkeskommuner</option>
      <option value="dekning">Uvanlig dekning to år på rad</option><option value="avvik">Avvik i dekning i år</option>
      <option value="over-100">Over 100 %: avtaler for andre</option>
      <option value="ingen">Ingen kontrakter i året</option>
    </select>
    <span class="muted small" data-count-for="municipal" aria-live="polite"></span>
  </div>
  <div class="table-scroll"><table class="sortable" id="municipal">
  <caption class="visually-hidden">Kommuner og fylkeskommuner i {year}: Doffin-kontrakter mot kjøp</caption>
  <thead><tr><th scope="col" data-type="text"><button type="button">Kommune</button></th>
  <th scope="col" data-type="num"><button type="button">Kontrakter</button></th>
  <th scope="col" data-type="num"><button type="button">Doffin, andel {year}</button></th>
  <th scope="col" data-type="num"><button type="button">Kjøp (KOSTRA)</button></th>
  <th scope="col" data-type="num"><button type="button">Dekning</button></th>
  <th scope="col" data-type="num" title="Mediandekning for kommuner av lignende størrelse"><button type="button">Lignende</button></th>
  <th scope="col" data-type="text"><button type="button">Merknad</button></th></tr></thead>
  <tbody>{"".join(rows)}</tbody></table></div>
</section>
{split_html}
"""
    return page("Kommuner og fylker", body, active="kommuner", path="kommuner.html",
                description=f"Doffin-kontraktene og kjøpene til {n_k} kommuner og fylkeskommunene i {year}.")


def municipal_page(ds: Dataset, a: Agency, history: list[tuple[int, Agency]], linked: set[str] = frozenset()) -> str:
    c = a.coverage
    share = a.contract_sum / a.spend_sum if a.spend_sum else None
    facts = [
        ("Organisasjonsnummer", f'<a href="https://data.brreg.no/enhetsregisteret/oppslag/enheter/{escape(a.orgnr)}">'
                                f'{escape(a.orgnr)}</a>'),
        ("Type", MUNICIPAL_KIND.get(a.owner, a.owner)),
        (f"Kjøp {year_text(ds.year)} (KOSTRA)", kr_full(a.spend_sum) if a.spend_source else
         f'<span class="muted">KOSTRA for {ds.year} er ikke publisert ennå</span>'),
    ] + [(f" {escape(MUNICIPAL_PARTS.get(k, k))}", kr_full(v)) for k, v in a.spend_parts.items()] + [
        (f"Doffin-kontrakter som løper i {ds.year}", str(a.contract_count)),
        (f"Årsandel av kontraktsverdien {ds.year}", kr_full(a.contract_sum)),
        (f"Tildelt i {ds.year} (hele verdien, {a.awarded_in_year_count} stk.)", kr_full(a.awarded_in_year_sum)),
        ("Doffin-dekning", (f"{pct(share)}" + (f" (lignende, {escape(c.peer_group)}: median {pct(c.peer_median)})" if c else ""))
         if share is not None else "–"),
    ]
    facts_html = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)
    notes = []
    if c and c.outlier:
        side = "høyere" if c.z > 0 else "lavere"
        lead = (f"<strong>Uvanlig {'høy' if c.z > 0 else 'lav'} Doffin-dekning to år på rad.</strong>" if c.flagged
                else "<strong>Avvik i dekning i år.</strong>")
        notes.append(f"<li>{lead} Doffin dekker {pct(c.ratio)} av kjøpene, tydelig {side} enn de {c.peer_count} "
                     f"{escape(c.peer_group)} (median {pct(c.peer_median)}, z = {_z(c.z)}). Det betyr "
                     "«verdt å se nærmere på», ikke at noe er galt.</li>")
    if c and c.category == "avtaler_for_andre":
        notes.append(f"<li><strong>Dekning over 100 %.</strong> Doffin-andelen ({pct(c.ratio)} av kjøpene) er større "
                     "enn kommunens egne kjøp. Som regel er kommunen vert for et innkjøpssamarbeid og kunngjør avtaler "
                     "som også nabokommunene bruker. Det gir ikke merknad om avvik.</li>")
    if a.spend_sum and not a.contract_count:
        notes.append("<li><strong>Ingen kontrakter i Doffin som løper i året.</strong> Mange små kommuner kjøper "
                     "gjennom interkommunale innkjøpssamarbeid eller avtaler andre har inngått, og da står ikke "
                     "kommunen selv som kjøper.</li>")
    notes_html = f'<ul class="notes">{"".join(notes)}</ul>' if notes else ""
    hrows = "".join(
        f'<tr{ROW_CURRENT if y == ds.year else ""}><td>{year_text(y, short=True)}</td><td class="num">{h.contract_count}</td>'
        f'<td class="num" title="{kr_full(h.contract_sum)}">{kr(h.contract_sum)}</td>'
        f'<td class="num" title="{kr_full(h.spend_sum)}">{kr(h.spend_sum) if h.spend_sum else "–"}</td>'
        f'<td class="num">{pct(h.contract_sum / h.spend_sum) if h.spend_sum else "–"}</td>'
        f'<td>{_coverage_badge(h)}</td></tr>'
        for y, h in sorted(history, key=lambda t: -t[0]))
    early = [y for y, h in history if not h.spend_sum and y < MUNICIPAL_REFORM]
    hnote = (f'<p class="muted small">{MUNICIPAL_KIND.get(a.owner, a.owner)}n ble opprettet ved kommune- og '
             f'fylkesendringene i {MUNICIPAL_REFORM}, så KOSTRA har ikke sammenlignbare tall for årene før.</p>'
             if early else "")
    if a.until_year:
        notes.insert(0, f"<li><strong>Delt i {a.until_year + 1}.</strong> {escape(a.name)} fantes fra 2020 til "
                        f"{a.until_year} og ble delt da fylkene ble delt. Siden viser {a.until_year}, det siste året. "
                        "Kontraktene fylkeskommunen og forgjengerne før 2020 inngikk, står her også når de løper "
                        "videre, og ikke hos fylkeskommunene som tok over.</li>")
        notes_html = f'<ul class="notes">{"".join(notes)}</ul>'
    suppliers_html, contracts_html, joint_html = _contract_parts(ds, a, linked)
    body = f"""
<p class="crumbs"><a href="../kommuner.html">Alle kommuner og fylker</a></p>
<section class="hero"><h1>{escape(a.name)}</h1><div class="badges">{_coverage_badge(a)}</div>{_follow("kjoper", a.orgnr, a.name)}</section>
<div class="two-col">
  <div><dl class="facts">{facts_html}</dl></div>
  <div>{notes_html}{_municipal_caveat("../")}</div>
</div>
<section><h2>Utvikling</h2><div class="table-scroll"><table class="history"><thead><tr><th scope="col">År</th>
<th scope="col" data-type="num">Kontrakter</th><th scope="col" data-type="num">Doffin</th>
<th scope="col" data-type="num">Kjøp (KOSTRA)</th><th scope="col" data-type="num">Dekning</th>
<th scope="col">Merknad</th></tr></thead><tbody>{hrows}</tbody></table></div>{hnote}</section>
{suppliers_html}
{contracts_html}
{joint_html}
"""
    return page(a.name, body, root="../", active="kommuner", path=a.path,
                description=f"Doffin-kontrakter og kjøp for {a.name} i {ds.year}.")


# --- leverandører -------------------------------------------------------------

@dataclass
class SupplierSummary:
    orgnr: str
    name: str
    # år -> [(kjøper, kontrakt, andel av årets verdi)]; andelen er delt likt når flere vant
    years: dict[int, list[tuple[Agency, Contract, float]]] = field(default_factory=dict)

    def total(self, year: int) -> float:
        return sum(v for _, _, v in self.years.get(year, ()))

    @property
    def buyers(self) -> set[str]:
        return {a.orgnr for rows in self.years.values() for a, _, _ in rows}

    @property
    def contract_ids(self) -> set[str]:
        return {c.id for rows in self.years.values() for _, c, _ in rows}


def _public() -> str:
    """«offentlige» når kommunene er med, ellers «statlige»."""
    return "offentlige" if _SITE["municipal"] else "statlige"


def _buyers_phrase(ds: Dataset) -> str:
    parts = ["statlige virksomheter"] + (["helseforetak"] if ds.health else []) + (
        ["statlige selskaper"] if ds.companies else []) + (
        ["kommuner og fylkeskommuner"] if ds.municipal else []) + (
        ["kommunale selskaper"] if ds.municipal_companies else [])
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " og " + parts[-1]


def supplier_index(datasets: list[Dataset], front: int) -> list[SupplierSummary]:
    """Leverandører med orgnr på tvers av årene, sortert etter verdien i forsideåret."""
    by_orgnr: dict[str, SupplierSummary] = {}
    names: dict[str, Counter[str]] = {}
    for ds in datasets:
        for a in ds.agencies + ds.health + ds.companies + ds.municipal_companies + ds.municipal:
            for c in a.contracts:
                for w in c.suppliers:
                    if not w.orgnr:
                        continue
                    sup = by_orgnr.setdefault(w.orgnr, SupplierSummary(orgnr=w.orgnr, name=w.name))
                    sup.years.setdefault(ds.year, []).append((a, c, (c.year_value or 0.0) / len(c.suppliers)))
                    names.setdefault(w.orgnr, Counter())[w.name] += 1
    for orgnr, sup in by_orgnr.items():
        sup.name = names[orgnr].most_common(1)[0][0]  # vanligste skrivemåte
    return sorted(by_orgnr.values(),
                  key=lambda s: (-s.total(front), -sum(s.total(y) for y in s.years), s.name.lower()))


def _value_label(ds: Dataset) -> str:
    return f"Andel {year_text(ds.year, short=True)}" if ds.annualized else f"Verdi {ds.year}"


SUPPLIERS_INLINE = 100  # rader i HTML-en; resten hentes fra data/leverandorer.json ved søk, sortering eller «Vis alle»


def suppliers_json(datasets: list[Dataset], suppliers: list[SupplierSummary]) -> str:
    """Hele leverandørlisten i kompakt form for leverandorer.html: én rad per leverandør, samme kolonner."""
    shown = sorted(datasets, key=lambda d: -d.year)
    rows = [[s.name, s.orgnr, len(s.contract_ids), len(s.buyers)]
            + [round(s.total(d.year)) if d.year in s.years else None for d in shown] for s in suppliers]
    return json.dumps({"rows": rows}, ensure_ascii=False, separators=(",", ":"))


def suppliers_page(datasets: list[Dataset], suppliers: list[SupplierSummary], front: Dataset) -> str:
    shown = sorted(datasets, key=lambda d: -d.year)
    year_heads = "".join(
        f'<th scope="col" data-type="num">'
        f'<button type="button">{escape(_value_label(d))}</button></th>' for d in shown)
    rows = []
    for s in suppliers[:SUPPLIERS_INLINE]:
        cells = "".join(
            f'<td class="num" data-sort="{round(s.total(d.year)) if d.year in s.years else ""}">'
            f'{kr(s.total(d.year)) if d.year in s.years else "–"}</td>' for d in shown)
        rows.append(
            f'<tr data-name="{escape(s.name.lower())} {s.orgnr}">'
            f'<td><a href="leverandor/{escape(s.orgnr)}.html">{escape(s.name)}</a> <span class="orgnr">{s.orgnr}</span></td>'
            f'<td class="num" data-sort="{len(s.contract_ids)}">{len(s.contract_ids)}</td>'
            f'<td class="num" data-sort="{len(s.buyers)}">{len(s.buyers)}</td>{cells}</tr>')
    years = [d.year for d in datasets]
    span = str(years[0]) if len(years) == 1 else f"{min(years)}–{max(years)}"
    body = f"""
<section class="hero">
  <h1>Leverandører</h1>
  <p class="lede">Hvem vinner kontraktene? {_group(len(suppliers))} leverandører med organisasjonsnummer har kontrakter
  som løper hos {_buyers_phrase(front)} i oversikten, {span}. Søk på navn eller organisasjonsnummer, og sorter på
  hvert år.</p>
</section>
<p class="muted">Verdien er {"årsandelen: den delen av kontraktsverdien som faller i året" if front.annualized
else "kontraktsverdien"}. Når flere leverandører vant samme kontrakt (typisk rammeavtaler), er verdien delt likt
mellom dem, fordi Doffin sjelden viser fordelingen. Leverandører uten organisasjonsnummer i kunngjøringen (ofte
utenlandske) er ikke med her.</p>
<section>
  <div class="table-tools">
    <label for="filter">Søk etter leverandør</label>
    <input id="filter" data-filter="suppliers" type="search" placeholder="Navn eller orgnr" autocomplete="off">
    <span class="muted small" data-count-for="suppliers" aria-live="polite"></span>
    <a class="download" href="data/kontrakter.csv" download>Last ned kontraktene (CSV)</a>
  </div>
  <div class="table-scroll">
  <table class="sortable" id="suppliers" data-source="data/leverandorer.json" data-total="{len(suppliers)}">
    <caption class="visually-hidden">Leverandører med kontrakter hos {_buyers_phrase(front)}, {span}</caption>
    <thead><tr><th scope="col" data-type="text"><button type="button">Leverandør</button></th>
    <th scope="col" data-type="num" title="Ulike kontrakter, alle år"><button type="button">Kontrakter</button></th>
    <th scope="col" data-type="num" title="Ulike {_public()} kjøpere, alle år"><button type="button">Kjøpere</button></th>
    {year_heads}</tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
  </div>
  {f'''<p class="table-more" data-more-for="suppliers"><span class="muted small" data-shown-for="suppliers">Viser de
  {SUPPLIERS_INLINE} største av {_group(len(suppliers))}. Søk eller sorter for å se alle.</span>
  <button type="button" class="button button-quiet" data-show-all="suppliers">Vis alle {_group(len(suppliers))}</button></p>'''
  if len(suppliers) > SUPPLIERS_INLINE else ""}
</section>
"""
    return page("Leverandører", body, active="leverandorer", path="leverandorer.html",
                description=f"Leverandørene som vinner {_public()} kontrakter i Doffin, {span}.")


def supplier_page(datasets: list[Dataset], s: SupplierSummary, front: Dataset) -> str:
    shown = [d for d in sorted(datasets, key=lambda d: -d.year) if d.year in s.years]
    year_rows = "".join(
        f'<tr{ROW_CURRENT if d is front else ""}><td>{year_text(d.year, short=True)}</td>'
        f'<td class="num">{len({c.id for _, c, _ in s.years[d.year]})}</td>'
        f'<td class="num">{len({a.orgnr for a, _, _ in s.years[d.year]})}</td>'
        f'<td class="num" title="{kr_full(s.total(d.year))}">{kr(s.total(d.year))}</td></tr>' for d in shown)

    # Én rad per kjøper og per kontrakt, med andelen i forsideåret (eller siste år kontrakten løper)
    per_buyer: dict[str, list] = {}
    contracts: dict[str, list] = {}
    for d in sorted(datasets, key=lambda d: d.year):
        for a, c, v in s.years.get(d.year, ()):
            b = per_buyer.setdefault(a.orgnr, [a, set(), set()])
            b[0] = a
            b[1].add(c.id)
            b[2].add(d.year)
            row = contracts.setdefault(c.id, [a, c, None])
            row[0], row[1] = a, c
            if d.year == front.year:
                row[2] = v
    front_label = _value_label(front)

    def years_span(ys: set[int]) -> str:
        return str(min(ys)) if len(ys) == 1 else f"{min(ys)}–{max(ys)}"

    buyer_rows = "".join(
        f'<tr><td><a href="../{a.path}">{escape(a.name)}</a></td><td class="num">{len(ids)}</td>'
        f'<td class="num">{years_span(ys)}</td>'
        f'<td class="num" title="{kr_full(s_v)}">{kr(s_v) if s_v else "–"}</td></tr>'
        for a, ids, ys, s_v in sorted(
            ((a, ids, ys, sum(v for b, _, v in s.years.get(front.year, ()) if b.orgnr == a.orgnr))
             for a, ids, ys in per_buyer.values()), key=lambda r: (-r[3], -len(r[1]))))
    rows = []
    for a, c, v in sorted(contracts.values(), key=lambda t: (-(t[2] or 0.0), -(t[1].value or 0.0))):
        others = len(c.suppliers) - 1
        tag = f'<span class="tag">delt med {others} andre</span>' if others else ""
        title = f'<a href="{escape(c.url)}">{escape(c.title)}</a>' if c.url else escape(c.title)
        rows.append(
            f'<tr data-name="{escape((c.title + " " + a.name).lower())}"><td>{title}{tag}</td>'
            f'<td><a href="../{a.path}">{escape(a.name)}</a></td>'
            f'<td class="num" data-sort="{escape(c.published or "")}">{escape((c.published or "–")[:10])}</td>'
            f'<td class="num" data-sort="{c.first_year or ""}">{_period(c)}</td>'
            f'<td class="num" data-sort="{_num(c.value)}" title="{kr_full(c.value)}">{kr(c.value)}</td>'
            f'<td class="num" data-sort="{_num(v)}" title="{kr_full(v)}">{kr(v) if v is not None else "–"}</td></tr>')
    facts = [
        ("Organisasjonsnummer", f'<a href="https://data.brreg.no/enhetsregisteret/oppslag/enheter/{s.orgnr}">'
                                f'{s.orgnr}</a>'),
        ("Kontrakter med det offentlige" if _SITE["municipal"] else "Kontrakter med staten", str(len(contracts))),
        (f"{_public().capitalize()} kjøpere", str(len(per_buyer))),
    ]
    if front.year in s.years:
        facts.append((f"{front_label} (delt likt ved flere vinnere)", kr_full(s.total(front.year))))
    facts_html = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)
    body = f"""
<p class="crumbs"><a href="../leverandorer.html">Alle leverandører</a></p>
<section class="hero"><h1>{escape(s.name)}</h1>{_follow("leverandor", s.orgnr, s.name)}</section>
<div class="two-col">
  <dl class="facts">{facts_html}</dl>
  <div class="table-scroll"><table class="history"><thead><tr><th scope="col">År</th>
  <th scope="col" data-type="num">Kontrakter</th><th scope="col" data-type="num">Kjøpere</th>
  <th scope="col" data-type="num">Årsandel</th></tr></thead><tbody>{year_rows}</tbody></table></div>
</div>
<section>
  <h2>Kjøpere ({len(per_buyer)})</h2>
  <div class="table-scroll"><table><thead><tr><th scope="col">Kjøper</th>
  <th scope="col" data-type="num">Kontrakter</th><th scope="col" data-type="num">År</th>
  <th scope="col" data-type="num">{escape(front_label)}</th></tr></thead>
  <tbody>{buyer_rows}</tbody></table></div>
</section>
<section>
  <h2>Kontrakter ({len(rows)})</h2>
  <div class="table-tools"><label for="contract-filter">Søk i kontraktene</label>
  <input id="contract-filter" data-filter="contracts" type="search" autocomplete="off" placeholder="Tittel eller kjøper">
  <span class="muted small" data-count-for="contracts" aria-live="polite"></span></div>
  <div class="table-scroll"><table class="sortable" id="contracts"><thead><tr>
  <th scope="col" data-type="text"><button type="button">Kontrakt</button></th>
  <th scope="col" data-type="text"><button type="button">Kjøper</button></th>
  <th scope="col" data-type="num"><button type="button">Tildelt</button></th>
  <th scope="col" data-type="num"><button type="button">Løper</button></th>
  <th scope="col" data-type="num"><button type="button">Hele verdien</button></th>
  <th scope="col" data-type="num"><button type="button">{escape(front_label)}</button></th></tr></thead>
  <tbody>{"".join(rows)}</tbody></table></div>
</section>
"""
    return page(s.name, body, root="../", active="leverandorer", path=f"leverandor/{s.orgnr}.html",
                description=f"{_public().capitalize()} kontrakter i Doffin vunnet av {s.name}.")


# --- om dataene ---------------------------------------------------------------

def about_page(ds: Dataset) -> str:
    coverage = ""
    if ds.has_coverage:
        persist = ("<p>Avviker den samme vei i minst to av de tre siste årene, får den flagget «uvanlig høy» eller "
                   "«uvanlig lav dekning». Ett år alene kan skyldes én stor rammeavtale og vises bare som "
                   "«avvik i dekning i år». Dekning over 100 % betyr at Doffin-andelen er større enn egne kjøp. Det "
                   "har som regel en kjent forklaring og blir merket, ikke flagget: «tilskudd» når tilskuddene ut "
                   "av staten er minst like store (Samferdselsdepartementet kunngjør fly- og togruter, men betaler "
                   "dem som tilskudd), ellers «avtaler for andre» (som DFØ og DSS, som inngår avtaler andre "
                   "bruker).</p>"
                   if any(a.coverage and a.coverage.category for a in ds.agencies) else
                   "<p>Da får den flagget «uvanlig høy» eller «uvanlig lav dekning».</p>")
        coverage = f"""
<h3>Doffin-dekning mot lignende virksomheter</h3>
<p>Fordi Doffin bare viser en del av innkjøpene, er det mer nyttig å spørre om en virksomhet kunngjør
<em>uvanlig mye eller lite</em> sammenlignet med lignende virksomheter. Dekningen er Doffin-summen delt på kjøpene.
Virksomhetene deles i tre størrelsesgrupper etter kjøp (små, mellomstore, store), og hver virksomhet
sammenlignes med medianen i sin gruppe på logaritmisk skala (robust z-verdi). Avviker den med mer enn
to standardavvik, er det et avvik.</p>
{persist}
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
""" + ("""<p>For et år som ikke er ferdig (som 2026, der regnskapet går til og med august), tas bare
kontraktsdagene i de samme månedene med, så Doffin og regnskapet dekker samme periode.</p>
""" if _PARTIAL else "")
    statsregnskap = ""
    if _SITE["statsregnskap"]:
        groups = ", ".join(sorted(ARTSKONTO_GROUPS))
        statsregnskap = f"""
<dt><a href="https://statsregnskapet.dfo.no">Statsregnskapet</a> (DFØ)</dt>
<dd>Bevilgningsregnskapet for alle statlige virksomheter, som DFØ publiserer som rådata per måned.
Desembertallene gir hele året. Kjøp av varer og tjenester er summen av artskontogruppene {groups}
(varer, fremmedytelser, investeringer og driftskostnader), uten lønn, avskrivninger, reiser og tilskudd.
Når en virksomhet har tall både her og i årsrapporten, brukes statsregnskapet, og årsrapporten lenkes til
når den finnes.</dd>"""
    suppliers = ""
    if ds.has_suppliers:
        suppliers = """
<h3>Leverandører</h3>
<p>Leverandørene er vinnerne slik de står i kunngjøringen. Er det flere vinnere på samme kontrakt, typisk en
rammeavtale, deles verdien likt mellom dem, fordi Doffin sjelden viser fordelingen. Leverandører uten
organisasjonsnummer i kunngjøringen (ofte utenlandske) vises ved kontrakten, men får ikke egen side.</p>"""
    units = ""
    if any(c.buyer_unit for a in ds.agencies for c in a.contracts):
        units = """
<p>Noen virksomheter kunngjør gjennom underenheter med eget organisasjonsnummer, for eksempel
politidistriktene og Politiets fellestjenester for Politiet, eller avdelinger i Forsvaret. Slike kontrakter
regnes til virksomheten som fører regnskapet, og underenheten vises ved kontrakten. Har et overordnet ledd
samme navn som virksomheten, slik Tolletaten har i Enhetsregisteret, regnes også det leddet til virksomheten.</p>"""
    if ds.unmatched:
        units += f"""
<p>Virksomheter i regnskapet som ikke har kontrakter i Doffin i et år, tas ikke med i sammenligningen, men står
i en egen liste nederst på årssiden med kjøpene og hvor mange kunngjøringer de har hatt. For
{year_text(ds.year)} gjelder det {len(ds.unmatched)} virksomheter. Slik er alle virksomhetene i regnskapet med
i oversikten, også de som ikke kunngjør selv.</p>"""
    if any(a.joint for a in ds.agencies):
        units += """
<p>Noen kunngjøringer har flere kjøpere, for eksempel når Politiets fellestjenester kjøper bekledning sammen med
Tolletaten og Kriminalomsorgen. Kontrakten regnes til den første kjøperen, slik Doffin viser den. De andre
kjøperne får kontrakten i en egen liste over felles anskaffelser på virksomhetssiden, men verdien regnes ikke med
hos dem, fordi Doffin ikke viser hvor stor del hver kjøper står for.</p>"""
    coverage_caveat = (
        ('Helseforetakene, de <a href="selskaper.html">statlige selskapene</a> og <a href="kommuner.html">kommunene</a> '
         "er ikke i statsregnskapet, men har egne sider med tall fra Regnskapsregisteret og KOSTRA."
         if ds.health and ds.companies and ds.municipal else
         "Statlige selskaper (aksjeselskaper og statsforetak) er ikke en del av statsregnskapet og er ikke med. "
         'Helseforetakene og <a href="kommuner.html">kommunene</a> er heller ikke i statsregnskapet, men har egne sider '
         "med tall fra Regnskapsregisteret og KOSTRA."
         if ds.health and ds.municipal else
         "Kommuner, fylker og statlige selskaper er ikke en del av statsregnskapet og er ikke med. Helseforetakene "
         'er heller ikke i statsregnskapet, men har <a href="helseforetak.html">egne sider</a> med årsregnskapet.'
         if ds.health else
         "Kommuner, fylker, helseforetak og statlige selskaper er ikke en del av statsregnskapet og er ikke med.")
        if _SITE["statsregnskap"] else
        "Kommuner, fylker, helseforetak og selskaper har ikke årsrapport i Kudos. Forvaltningsbedrifter som "
        "Statsbygg og universitetene bruker et annet oppsett som foreløpig ikke leses.")
    enhetsregisteret = (
        '\n<dt><a href="https://www.brreg.no/om-oss/registrene-vare/om-enhetsregisteret/">Enhetsregisteret</a> '
        "(Brønnøysundregistrene)</dt>\n<dd>Registeret over norske virksomheter og underenhetene deres. Vi henter "
        "underenhetene til de statlige virksomhetene, slik at en kontrakt kunngjort av for eksempel et "
        "politidistrikt regnes til Politiet.</dd>" if _SITE["units"] else "")
    if ds.health:
        enhetsregisteret += (
            '\n<dt><a href="https://www.brreg.no/om-oss/registrene-vare/om-regnskapsregisteret/">Regnskapsregisteret</a> '
            "(Brønnøysundregistrene)</dt>\n<dd>Årsregnskapene til helseforetakene"
            + (" og de statlige og kommunale selskapene" if ds.municipal_companies else
               " og de statlige selskapene" if ds.companies else "") + ", som ikke er med i statsregnskapet. "
            "Vi bruker driftskostnadene og lønnskostnadene for de siste tre årene.</dd>")
    if ds.municipal:
        enhetsregisteret += (
            '\n<dt><a href="https://www.ssb.no/offentlig-sektor/kostra">KOSTRA</a> (SSB)</dt>\n<dd>Kommunenes og '
            "fylkeskommunenes regnskap etter art, konserntall (tabell 13551, 13552, 13547 og 13548). Vi bruker kjøp av "
            "varer og tjenester, kjøp som erstatter egen produksjon og investeringer i varige driftsmidler.</dd>")
    municipal = ""
    if ds.municipal:
        n_k = sum(1 for a in ds.municipal if a.owner == "kommune")
        municipal = f"""
<h3 id="kommuner">Kommunene og fylkeskommunene</h3>
<p>Alle {n_k} kommuner og {len(ds.municipal) - n_k} fylkeskommuner er med. Kjøpene er KOSTRA-tallene for
kommunekonsernet, altså med kommunale foretak og interkommunale selskaper: kjøp av varer og tjenester, kjøp som
erstatter egen tjenesteproduksjon (som private barnehager) og investeringer i varige driftsmidler. Det tilsvarer
omtrent kjøp av varer og tjenester i statsregnskapet. Tallene går tilbake til 2020. Mange kommuner fikk nytt
kommunenummer da fylkene ble delt i 2024, og de gamle numrene kobles til dagens kommuner med SSBs klassifikasjon.
Ålesund og Haram ble delt i 2024 og har bare tall fra da. Det samme gjelder de sju fylkeskommunene som ble
opprettet i 2024. Forgjengerne deres, Viken, Vestfold og Telemark og Troms og Finnmark, er med som egne
fylkeskommuner for 2020–2023 med kontraktene sine.</p>
<p>Kontraktene kobles på kjøperen i Doffin. Etater, bydeler og kommunale foretak som kunngjør med eget
organisasjonsnummer, slås opp i Enhetsregisteret og regnes til kommunen de hører til. Kunngjøringer fra kommuner og
fylkeskommuner som er slått sammen med andre (som Skedsmo, nå Lillestrøm, og Hordaland, nå Vestland), og fra
kommunale foretak og etater som er lagt ned (som Omsorgsbygg Oslo KF), regnes til kommunen de ble en del av. Det
går på navnet og SSBs oversikt over kommuneendringer, fordi Enhetsregisteret fjerner koblingen når en enhet slettes.
Kunngjøringer fra fylkeskommunene før 2020 som ble en del av Viken, Vestfold og Telemark eller Troms og Finnmark
(som Akershus og Buskerud), regnes til dem. Kontraktene til de delte fylkeskommunene som løper etter 2023, kan ikke
fordeles på fylkeskommunene som tok over, og står derfor bare på sidene til de delte. Interkommunale selskaper og
innkjøpssamarbeid har flere eiere og regnes ikke til én kommune.</p>
<p>Dekningen sammenlignes med kommuner av lignende størrelse: kommunene deles i tre like store grupper etter kjøp,
og fylkeskommunene er én gruppe. En kommune merkes når dekningen avviker tydelig fra gruppen (robust z-verdi over
2 på logaritmen til dekningen), og som uvanlig når avviket går samme vei to år på rad. Kommuner uten kontrakter i
året er ikke med i sammenligningen. Dekning over 100 % gir ikke merknad om avvik: da er kommunen som regel vert for
et innkjøpssamarbeid og kunngjør avtaler som nabokommunene også bruker, og nabokommunenes dekning blir tilsvarende
lav.</p>"""
    health = ""
    if ds.health:
        n_units = sum(1 for a in ds.health for c in a.contracts if c.buyer_unit)
        health = f"""
<h3 id="helseforetak">Helseforetakene</h3>
<p>De fire regionale helseforetakene og de {len(ds.health) - 4} helseforetakene som eies av dem, er statlige,
men står ikke i statsregnskapet. Kontraktene deres hentes i Doffin på samme måte som for virksomhetene, og
sykehusene og avdelingene som kunngjør med eget organisasjonsnummer, regnes til foretaket de hører til{
f" ({n_units} av kontraktene i {ds.year})" if n_units else
", men i praksis står nesten alle kunngjøringene på foretaket selv"}. Listen over foretakene ligger i
<a href="{REPO_URL}/blob/main/site-data/helseforetak.csv">helseforetak.csv</a>.</p>
<p>Årsregnskapet skiller ikke ut kjøp av varer og tjenester. Nærmeste tall er driftskostnadene utenom lønn, som
også tar med avskrivninger, gjestepasientoppgjør og kjøp av helsetjenester fra private. Doffin-andelen for
helseforetakene er derfor ikke sammenlignbar med dekningen for virksomhetene i statsregnskapet, og foretakene
flagges ikke. Sykehusinnkjøp inngår nasjonale avtaler som foretakene bruker, så andelen blir høy der og lavere
hos foretakene.</p>"""
    companies = ""
    if ds.companies:
        no_accounts = sum(1 for a in ds.companies if not a.spend_source)
        companies = f"""
<h3 id="selskaper">Selskapene</h3>
<p>Statsforetak og statlig eide selskaper som Bane NOR, Avinor, Nye Veier og NRK kunngjør i Doffin, men står ikke
i statsregnskapet. Kronesporet finner dem blant kjøperne i Doffin ut fra den institusjonelle sektoren i
Enhetsregisteret, altså SSBs inndeling: statlig eide aksjeselskaper og statsforetak, Norges Bank, statlige
låneinstitutter og særlovsselskaper, og selskaper og stiftelser som SSB regner til statsforvaltningen. Dermed
trengs ingen liste som må holdes ved like for hånd, og nye kjøpere kommer med av seg selv. {len(ds.companies)}
selskaper er med i {ds.year}. Underenhetene deres regnes til selskapet som for virksomhetene. Datterselskaper med
eget organisasjonsnummer, som Avinor Flysikring AS, står for seg.</p>
<p>Som for helseforetakene er sammenligningstallet driftskostnadene utenom lønn fra årsregnskapet i
Regnskapsregisteret. Investeringer er ikke med i driftskostnadene, så selskaper som bygger veier, flyplasser og
jernbane eller kjøper tog (Nye Veier, Avinor, Bane NOR, Norske tog) kan få en Doffin-andel over 100 %. Andre har
store kostnader som ikke er kjøp, som premiene i Norsk Tipping. {no_accounts} av selskapene, blant dem Norges Bank,
har ikke årsregnskap der for {ds.year} og har bare Doffin-tall. Andelen sier derfor mindre her enn for
virksomhetene, og selskapene flagges ikke.</p>"""
    if ds.municipal_companies:
        companies += f"""
<p>De {len(ds.municipal_companies)} <a href="kommunale-selskaper.html">kommunale selskapene</a> finnes på samme måte,
ut fra sektorene for kommunale foretak, kommunalt eide aksjeselskaper og kommuneforvaltningen. Det er
interkommunale selskaper (IKS), kommunale oppgavefellesskap og aksjeselskaper som Ruter, BIR og Kolumbus, som
kjøper kollektivtransport, renovasjon og vann og avløp. De regnes ikke til én kommune, fordi mange har flere
eiere. KOSTRA tar med kommunenes andel av IKS-ene, men ikke aksjeselskapene, så kontraktene deres er heller ikke
med i dekningen for kommunene. Nedlagte IKS og kommunale foretak har mistet sektorkoden i Enhetsregisteret og tas
med ut fra organisasjonsformen.</p>"""
    ledger = "Statsregnskapet" if _SITE["statsregnskap"] else "Årsrapportene"
    compare_rows = [
        ("Kunngjorte kontrakter med verdi og vinnere", "Ja", "Nei", "Nei"),
        ("Hva virksomheten faktisk betalte for varer og tjenester", "Nei", "Ja", "Nei"),
        ("Underenheter regnet til virksomheten som betaler", "Nei", "Nei",
         "Bare registeret" if _SITE["units"] else "Nei"),
        ("Kontraktsverdi fordelt på årene kontrakten løper", "Nei", "Nei", "Nei"),
        ("Hvor stor del av kjøpene som er kunngjort", "Nei", "Nei", "Nei"),
        ("Sammenligning med lignende virksomheter, flere år på rad", "Nei", "Nei", "Nei"),
        (f"Hver leverandørs andel hos alle {_public()} kjøpere, år for år", "Nei", "Nei", "Nei"),
    ] + ([("Helseforetakenes kontrakter sammen med årsregnskapet", "Nei", "Nei", "Nei")] if ds.health else []) + (
        [("Statlige selskapers kontrakter sammen med årsregnskapet", "Nei", "Nei", "Nei")] if ds.companies else []) + (
        [("Kommunenes kontrakter mot kjøpene i KOSTRA", "Nei", "Nei", "Nei")] if ds.municipal else [])
    cell = lambda v: f'<td class="cmp cmp-{"ja" if v == "Ja" else "nei" if v == "Nei" else "delvis"}">{v}</td>'  # noqa: E731
    compare = "".join(f'<tr><th scope="row">{escape(r)}</th><td class="cmp cmp-ja cmp-us">Ja</td>'
                      f'{"".join(cell(v) for v in vals)}</tr>' for r, *vals in compare_rows)
    body = f"""
<section class="hero"><h1>Om dataene og metoden</h1>
<p class="lede">Kronesporet er et åpenhetsverktøy for offentlig pengebruk. Vi henter inn kunngjorte kontrakter
fra Doffin, regnskapstall fra {"statsregnskapet" if _SITE["statsregnskap"] else "årsrapportene"}{", KOSTRA og Regnskapsregisteret" if ds.municipal and ds.health else ""}{" og virksomhetsstrukturen fra Enhetsregisteret" if _SITE["units"] else ""},
kobler dem på organisasjonsnummer og viser resultatet virksomhet for virksomhet og leverandør for leverandør.</p></section>
<div class="prose">
<h2 id="hvorfor">Hvorfor Kronesporet</h2>
<p>Hver kilde svarer på sitt spørsmål. Doffin viser hva som er kunngjort, men ikke hva som ble betalt.
{ledger} viser hva som ble betalt, men ikke til hvem eller gjennom hvilke kontrakter. Først når de kobles,
kan du se hvor mye av statens innkjøp som faktisk er synlig, og hvem som vinner kontraktene.</p>
</div>
<div class="table-scroll compare-wrap">
<table class="compare">
<caption class="visually-hidden">Hva du finner i hver kilde og i Kronesporet</caption>
<thead><tr><th scope="col"></th><th scope="col" class="cmp-us">Kronesporet</th><th scope="col">Doffin</th>
<th scope="col">{ledger}</th><th scope="col">Enhetsregisteret</th></tr></thead>
<tbody>{compare}</tbody>
</table>
</div>
<div class="prose">
<p class="muted small">Kildene har egne søk og rådata. Tabellen gjelder hva de viser hver for seg, ikke hva som
er mulig å regne ut fra dem.</p>
<h2 id="kilder">Kilder</h2>
<dl class="sources">
<dt><a href="https://doffin.no">Doffin</a></dt>
<dd>Den nasjonale kunngjøringsdatabasen for offentlige anskaffelser, drevet av Digdir. Vi henter
kunngjøringer om tildelte kontrakter, med kjøperens organisasjonsnummer, tittel og tildelt verdi.
Mangler tildelt verdi, brukes anslått verdi (merket «anslått verdi»).{" Leverandørene er vinnerne slik de står i kunngjøringen, med organisasjonsnummer når det er oppgitt." if ds.has_suppliers else ""} Noen kunngjøringer, mest fra 2017–2022, mangler kjøperens organisasjonsnummer. Da kjennes kjøperen igjen på navnet, når navnet bare har vært brukt av ett organisasjonsnummer.</dd>
<dt><a href="https://kudos.dfo.no">Kudos</a> (DFØ)</dt>
<dd>Kunnskapsdatabasen for offentlige utredninger, der statlige virksomheter publiserer årsrapportene sine.
Fra den standardiserte «Oppstilling av artskontorapportering» bruker vi
<em>Andre utbetalinger til drift</em> + <em>Utbetaling til investeringer</em> som «kjøp av varer og tjenester».
Lønn, tilskudd og finansposter er holdt utenfor.</dd>{statsregnskap}{enhetsregisteret}
</dl>
<p>{"Kildene" if _SITE["statsregnskap"] else "Begge kildene"} er lisensiert under <a href="https://data.norge.no/nlod/no/2.0">Norsk lisens for offentlige data
(NLOD 2.0)</a>, som tillater gjenbruk, også kommersielt, med kildehenvisning. Inneholder data under NLOD
tilgjengeliggjort av Digitaliseringsdirektoratet (Doffin) og Direktoratet for forvaltning og økonomistyring
({"statsregnskapet og Kudos" if _SITE["statsregnskap"] else "Kudos"}){" og Brønnøysundregistrene (Enhetsregisteret" + (" og Regnskapsregisteret" if ds.health else "") + ")" if _SITE["units"] else ""}.</p>

<h2 id="kobling">Slik kobles tallene</h2>
<p>Regnskapet har ikke leverandører, så koblingen går på <em>kjøper</em>: organisasjonsnummeret til kjøperen i
Doffin mot virksomheten i regnskapet. Avviket er (Doffin − kjøp) / kjøp, og virksomheter
der avviket er større enn {pct(ds.threshold, decimals=0)} i den ene eller andre retningen, blir flagget.</p>{units}
{annual}
{coverage}
{suppliers}
{health}
{companies}
{municipal}
<h2 id="forbehold">Forbehold</h2>
<ul>
<li><strong>Doffin dekker bare en del av kjøpene.</strong> Kjøp under terskelverdiene, avrop på eksisterende
rammeavtaler (også Statens innkjøpssenters fellesavtaler), husleie og kjøp via andre virksomheter kunngjøres
ikke. Doffin-summen blir derfor som regel mye lavere enn kjøpene, og {pct(ds.threshold, decimals=0)}-terskelen
flagger nesten alle. Den er beholdt fordi den er den opprinnelige målestokken, ikke fordi et slikt avvik i seg
selv tyder på feil.</li>
<li><strong>Kontraktverdier er anslag.</strong> For rammeavtaler er verdien ofte et tak, ikke det som faktisk
blir kjøpt.</li>
<li><strong>Ikke alle virksomheter er med.</strong> {coverage_caveat}</li>
<li><strong>Feil i kildene følger med.</strong> Navn og tall vises slik de står i kildene. Åpenbare feil i
kontraktsperioden, som en startdato i 1900 eller en varighet over 20 år, erstattes med standard varighet.
Andre kjente feil, som at en kunngjøring står med feil organisasjonsnummer på kjøperen, rettes og er listet i
<a href="{REPO_URL}/blob/main/site-data/rettinger.csv">rettinger.csv</a>.</li>
</ul>

<h2>Last ned dataene</h2>
<p>Tallene bak forsiden kan lastes ned som
<a href="data/virksomheter.csv">CSV</a> eller <a href="data/virksomheter.json">JSON</a>, og alle kontraktene
med leverandører som <a href="data/kontrakter.csv">kontrakter.csv</a>{', helseforetakene som <a href="data/helseforetak.csv">helseforetak.csv</a>' if ds.health else ""}{', selskapene som <a href="data/selskaper.csv">selskaper.csv</a>' if ds.companies else ""}{' og kommunene som <a href="data/kommuner.csv">kommuner.csv</a>' if ds.municipal else ""}. Dataene fra kildene er fortsatt
underlagt NLOD 2.0; oppgi kildene ved gjenbruk.</p>
</div>
"""
    return page("Om dataene", body, active="om", path="om.html",
                description="Hvorfor Kronesporet, kildene, metoden og forbeholdene: Doffin, statsregnskapet, "
                            "Enhetsregisteret og Kudos.",
                head=_dataset_jsonld())


# --- nedlastbare data ---------------------------------------------------------

EXPIRING_SHOWN = 20


def _signup_form() -> str:
    """Påmeldingsskjemaet. Sendes som vanlig skjema til Workeren (worker/src/index.js), som sender en e-post for å
    bekrefte og så videre til betaling. app.js fyller inn ?kjoper= og ?leverandor= og viser ?status= etterpå."""
    horizons = "".join(f'<label class="check"><input type="checkbox" name="horisont" value="{h}" checked> {h} måneder'
                       "</label>" for h in (3, 6, 12))
    return f"""<h2 id="meld-interesse">Meld deg på</h2>
<form class="signup" id="varsel-skjema" method="post" action="{escape(ALERTS_API.rstrip("/"))}/pamelding">
  <p class="signup-status" role="status" hidden></p>
  <label for="v-epost">E-post</label>
  <input id="v-epost" name="epost" type="email" autocomplete="email" required>
  <label for="v-kjoper">Kjøpere <span class="muted">(organisasjonsnummer, ett per linje)</span></label>
  <textarea id="v-kjoper" name="kjoper" rows="2" placeholder="915429785"></textarea>
  <label for="v-leverandor">Leverandører <span class="muted">(organisasjonsnummer eller navn)</span></label>
  <textarea id="v-leverandor" name="leverandor" rows="2"></textarea>
  <label for="v-cpv">Kategorier <span class="muted">(CPV-kode eller begynnelsen av den, f.eks. 72 for IT-tjenester
  eller 45 for bygg og anlegg)</span></label>
  <input id="v-cpv" name="cpv" inputmode="numeric">
  <label for="v-sokeord">Søkeord i tittelen <span class="muted">(skill med komma)</span></label>
  <input id="v-sokeord" name="sokeord">
  <fieldset><legend>Varsle om avtaler som løper ut innen</legend>{horizons}</fieldset>
  <label class="check"><input type="checkbox" name="csv" value="1" checked> Legg ved alle treffene med historikk fra
  2017 som CSV</label>
  <div class="hp" aria-hidden="true"><label>Nettside <input name="nettside" tabindex="-1" autocomplete="off"></label></div>
  <p><button class="button" type="submit">Meld meg på</button></p>
  <p class="muted small">Velger du både kjøpere eller leverandører og kategorier eller søkeord, får du avtalene som
  treffer begge. Kjøpere tar med underenhetene sine. Du får en e-post for å bekrefte adressen, og hver e-post har en
  lenke for å avslutte. Se <a href="vilkar.html#personvern">personvern</a>.</p>
</form>"""


def alerts_page(ref_date: str, expiring: list[Expiring]) -> str:
    """Varsler (første betalte tilbud): påmeldingsskjemaet når Workeren er satt opp (ALERTS_API), ellers
    interessepåmelding på e-post, og avtalene som løper ut."""
    if ALERTS_API:
        signup = _signup_form()
    elif ALERTS_EMAIL:
        mail = (f"mailto:{ALERTS_EMAIL}?subject=" + quote("Interesse for varsler fra Kronesporet")
                + "&body=" + quote("Virksomhet:\nHva vil dere følge (kjøpere, leverandører eller kategorier):\n"))
        signup = ('<h2 id="meld-interesse">Meld interesse</h2>\n<p>Påmeldingen åpner snart. Fortell oss hvem du vil '
                  'følge, så sier vi fra når den er klar.</p>\n'
                  f'<p><a class="button" href="{escape(mail)}">Meld interesse på e-post</a></p>'
                  '<p class="muted small">Det er gratis og forplikter ikke. Vi bruker adressen bare til å si fra når '
                  'varslene er klare, og sletter den hvis du ber om det. Se <a href="vilkar.html#personvern">'
                  "personvern</a>.</p>")
    else:
        signup = '<h2 id="meld-interesse">Meld interesse</h2>\n<p class="muted">Påmeldingen åpner snart.</p>'
    ref = f"{ref_date[8:10]}.{ref_date[5:7]}.{ref_date[:4]}" if ref_date else ""
    rows = "".join(
        f'<tr><td>{_contract_link(e)}' + ('<span class="tag">rammeavtale</span>' if e.is_framework else "")
        + f'</td><td>{escape(e.buyer)}</td>'
        f'<td class="num" data-sort="{e.end}">{e.end[8:10]}.{e.end[5:7]}.{e.end[:4]}</td>'
        f'<td class="num" data-sort="{_num(e.value)}" title="{kr_full(e.value)}">{kr(e.value)}</td>'
        f'<td>{"sluttdato" if e.source == "datoer" else "varighet"}</td></tr>'
        for e in expiring[:EXPIRING_SHOWN])
    n_fw = sum(e.is_framework for e in expiring)
    table = f"""
<section aria-labelledby="utlop">
  <h2 id="utlop">{len(expiring)} avtaler kan løpe ut de neste 12 månedene</h2>
  <p>Så mange kontrakter hos statlige kjøpere har en grunnperiode som slutter innen ett år etter {ref}, ifølge
  sluttdatoen eller varigheten i Doffin. {n_fw} av dem er rammeavtaler. Her er de {min(EXPIRING_SHOWN, len(expiring))}
  største. Mange avtaler har opsjoner på forlengelse, så en avtale kan løpe lenger. Avtaler der Doffin ikke oppgir
  sluttdato eller varighet, er ikke med.</p>
  <div class="table-scroll"><table class="sortable" id="utlop-tabell">
  <caption class="visually-hidden">Avtaler som kan løpe ut de neste 12 månedene</caption>
  <thead><tr><th scope="col" data-type="text"><button type="button">Avtale</button></th>
  <th scope="col" data-type="text"><button type="button">Kjøper</button></th>
  <th scope="col" data-type="num"><button type="button">Slutter</button></th>
  <th scope="col" data-type="num"><button type="button">Verdi</button></th>
  <th scope="col" data-type="text"><button type="button">Ut fra</button></th></tr></thead>
  <tbody>{rows}</tbody></table></div>
</section>""" if expiring else ""
    body = f"""<section class="hero">
  <p class="eyebrow eyebrow-dark">{"Nytt" if ALERTS_API else "Planlagt"}</p>
  <h1>Varsler om statens avtaler</h1>
  <p class="lede">{"Kronesporet sender" if ALERTS_API else "Kronesporet planlegger"} e-postvarsler for leverandører og
  rådgivere som følger statens innkjøp. Nettsiden og tallene her forblir gratis. Varslene sparer deg for å lete selv.</p>
</section>
<div class="prose">
<h2>Dette får du</h2>
<ul>
<li><strong>Nye tildelinger</strong> hos kjøperne, leverandørene eller kategoriene du følger, med vinner, verdi og
løpetid.</li>
<li><strong>Avtaler som løper ut</strong> om 3, 6 eller 12 måneder, så du kan forberede deg før neste
konkurranse.</li>
<li><strong>Fullt uttrekk</strong> av kontraktene med leverandører og historikk fra 2017 som CSV.</li>
</ul>
<p>Redaksjoner, forskere og offentlige virksomheter får varslene gratis.</p>
{signup}
</div>
{table}
"""
    return page("Varsler", body, active="varsler", path="varsler.html",
                description="Planlagte e-postvarsler om nye tildelinger og avtaler som løper ut hos statlige kjøpere.")


def terms_page() -> str:
    """Vilkår for bruk, forbehold, retting og personvern."""
    issue = f"{REPO_URL}/issues/new"
    contact = (f'Send en e-post til <a href="mailto:{escape(PRIVACY_EMAIL)}">{escape(PRIVACY_EMAIL)}</a>.'
               if PRIVACY_EMAIL else
               f'Opprett en <a href="{issue}">sak på GitHub</a> og be om kontakt. Ikke skriv personopplysninger i '
               "saken, for den er offentlig.")
    signup = ("<li><strong>Påmelding til varsler.</strong> Melder du interesse for varsler på e-post, bruker vi "
              "adressen bare til å si fra når varslene er klare. Vi deler den ikke og sletter den når du ber om det.</li>"
              if ALERTS_EMAIL else "")
    body = f"""<section class="hero">
  <h1>Vilkår og personvern</h1>
  <p class="lede">Kronesporet er gratis å bruke. Tallene kommer fra åpne offentlige kilder og er satt sammen av
  Kronesporet. Her står hva du kan bruke dem til, hvilke forbehold som gjelder, og hvordan vi behandler
  opplysninger om personer.</p>
</section>
<div class="prose">
<h2 id="hvem">Hvem står bak</h2>
<p>Kronesporet drives av Markemus-byte og er ikke tilknyttet DFØ, Digdir eller Brønnøysundregistrene.
Kildekoden og endringsloggen ligger på <a href="{REPO_URL}">GitHub</a>.{
f' Kontakt oss på <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>' if CONTACT_EMAIL else ""}{
f', og presse kan skrive til <a href="mailto:{PRESS_EMAIL}">{PRESS_EMAIL}</a>.' if CONTACT_EMAIL and PRESS_EMAIL else
"." if CONTACT_EMAIL else ""}</p>

<h2 id="bruk">Bruk av tallene</h2>
<p>Dataene fra kildene er lisensiert under <a href="https://data.norge.no/nlod/no/2.0">Norsk lisens for offentlige
data (NLOD 2.0)</a>. Du kan bruke dem fritt, også kommersielt, så lenge du oppgir kildene: Doffin (Digdir),
statsregnskapet og Kudos (DFØ) og Enhetsregisteret (Brønnøysundregistrene). Bruker du sammenstillingen, flaggene
eller dekningsgraden, ber vi om at du nevner Kronesporet. Tekst, design og kode på nettsiden: {COPYRIGHT}</p>

<h2 id="forbehold">Ingen garanti</h2>
<p>Tallene vises slik de står i kildene, og koblingene og beregningene er beskrevet på
<a href="om.html">metodesiden</a>. Det kan være feil både i kildene og i koblingen mellom dem. Et flagg betyr
«verdt å se nærmere på», ikke at noe er galt. Kronesporet tar ikke ansvar for beslutninger som tas på grunnlag
av tallene. Sjekk mot kilden før du bruker et tall; hver kontrakt lenker til kunngjøringen i Doffin.</p>

<h2 id="retting">Feil i tallene</h2>
<p>Mener du at tallene for en virksomhet eller leverandør er feil, kan du <a href="{issue}">melde det på
GitHub</a>{f' eller sende en e-post til <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>' if CONTACT_EMAIL else ""}. Skriv hvilken side det gjelder og hva som er feil. Rettelser står i
<a href="{REPO_URL}/blob/main/CHANGELOG.md">endringsloggen</a>.</p>

<h2 id="personvern">Personvern</h2>
<ul>
<li><strong>Besøk på nettsiden.</strong> Siden har ingen innlogging, ingen informasjonskapsler og ingen analyse-
eller sporingsverktøy, og den henter ikke noe fra andre nettsteder. Siden ligger på GitHub Pages, og GitHub
<a href="https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages#data-collection">lagrer
IP-adressen til besøkende av sikkerhetshensyn</a>.</li>
<li><strong>Leverandører som er enkeltpersonforetak.</strong> Noen leverandører er enkeltpersonforetak, og
navnet på foretaket kan være innehaverens navn. Vi viser navn og organisasjonsnummer slik de står i Doffin og
Enhetsregisteret, og bare sammen med kontrakter med staten. Grunnlaget er berettiget interesse
(personvernforordningen artikkel 6 nr. 1 bokstav f): allmennheten har interesse av å se hvem som får offentlige
kontrakter.</li>
<li><strong>Du kan be om å bli skjult.</strong> Har du et enkeltpersonforetak og vil ikke vises med navn, viser
vi foretaket som «Enkeltpersonforetak (skjult)» uten organisasjonsnummer, og leverandørsiden fjernes. Kontraktene
står fortsatt i Doffin. {contact}</li>
{signup}<li><strong>Klage.</strong> Du kan klage til <a href="https://www.datatilsynet.no">Datatilsynet</a>.</li>
</ul>
</div>
"""
    return page("Vilkår og personvern", body, path="vilkar.html",
                description="Vilkår for bruk av Kronesporet, forbehold, retting av feil og personvern.")


def not_found_page() -> str:
    """404-siden. Den vises på vilkårlige adresser, så lenkene må være absolutte når adressen er kjent."""
    root = _SITE["base_url"]
    body = f"""<section class="hero">
  <h1>Fant ikke siden</h1>
  <p class="lede">Adressen finnes ikke på Kronesporet. Kanskje virksomheten har fått ny side,
  eller lenken er skrevet feil.</p>
</section>
<ul class="notes">
  <li><a href="{root}index.html">Se alle virksomhetene</a></li>
  <li><a href="{root}leverandorer.html">Se leverandørene</a></li>
  <li><a href="{root}om.html">Les om dataene og metoden</a></li>
</ul>"""
    return page("Fant ikke siden", body, root=root, head='<meta name="robots" content="noindex">\n')


def _dataset_jsonld() -> str:
    """Strukturerte data (schema.org/Dataset) så søkemotorene forstår de nedlastbare tallene."""
    base = _SITE["base_url"]
    if not base:
        return ""
    data = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Kronesporet: statlige kontrakter i Doffin sammenlignet med kjøp i regnskapet",
        "description": DEFAULT_DESCRIPTION,
        "url": base + "om.html",
        "inLanguage": "nb",
        "isAccessibleForFree": True,
        "creator": {"@type": "Organization", "name": SITE_NAME, "url": base},
        "isBasedOn": ["https://doffin.no", "https://kudos.dfo.no"]
        + (["https://statsregnskapet.dfo.no"] if _SITE["statsregnskap"] else [])
        + (["https://www.brreg.no"] if _SITE["units"] else []),
        "license": "https://data.norge.no/nlod/no/2.0",
        "temporalCoverage": _SITE["years"],
        "spatialCoverage": "Norge",
        "distribution": [
            {"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": base + "data/virksomheter.csv"},
            {"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": base + "data/kontrakter.csv"},
            {"@type": "DataDownload", "encodingFormat": "application/json",
             "contentUrl": base + "data/virksomheter.json"},
        ],
    }
    if _SITE["data_date"]:
        d, m, y = _SITE["data_date"].split(".")
        data["dateModified"] = f"{y}-{m}-{d}"
    text = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
    return f'<script type="application/ld+json">\n{text}\n</script>\n'


def export_rows(ds: Dataset) -> list[dict[str, object]]:
    rows = []
    for a in ds.agencies:
        row: dict[str, object] = {
            "virksomhet": a.name, "orgnr": a.orgnr, "år": a.year,
            "regnskap_maaneder": ds.through_month,
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
                "dekningskategori": c.category if c else None,
                "aar_med_avvik_siste_3": c.years_outlier if c else None,
            })
        row["kilde_arsrapport"] = a.report_url or ""
        row["kilde_regnskap"] = a.spend_source or ""
        rows.append(row)
    return rows


def health_rows(ds: Dataset) -> list[dict[str, object]]:
    return [{"foretak": a.name, "orgnr": a.orgnr, "eier": a.owner, "år": a.year,
             "antall_kontrakter": a.contract_count, "doffin_sum_kr": round(a.contract_sum),
             "driftskostnader_utenom_lonn_kr": round(a.spend_sum) if a.spend_source else "",
             "doffin_andel_prosent": round(_health_share(a) * 100, 1) if _health_share(a) is not None else ""}
            for a in ds.health]


def company_rows(ds: Dataset) -> list[dict[str, object]]:
    return [{"selskap": a.name, "orgnr": a.orgnr, "type": COMPANY_FORMS.get(a.owner, a.owner),
             "eier": "staten" if a.owner_level == "stat" else "kommunene", "år": a.year,
             "antall_kontrakter": a.contract_count, "doffin_sum_kr": round(a.contract_sum),
             "driftskostnader_utenom_lonn_kr": round(a.spend_sum) if a.spend_source else "",
             "doffin_andel_prosent": round(_health_share(a) * 100, 1) if _health_share(a) is not None else ""}
            for a in ds.companies + ds.municipal_companies]


def municipal_rows(ds: Dataset) -> list[dict[str, object]]:
    return [{"kommune": a.name, "orgnr": a.orgnr, "type": a.owner, "år": a.year,
             "antall_kontrakter": a.contract_count, "doffin_sum_kr": round(a.contract_sum),
             "kjop_kostra_kr": round(a.spend_sum) if a.spend_source else "",
             **{f"kjop_{k}_kr": round(v) for k, v in a.spend_parts.items()},
             "dekning_prosent": round(a.contract_sum / a.spend_sum * 100, 2) if a.spend_sum else "",
             "storrelsesgruppe": a.coverage.peer_group if a.coverage else "",
             "gruppens_mediandekning_prosent": round(a.coverage.peer_median * 100, 2) if a.coverage else "",
             "avvik_i_aar": int(a.coverage.outlier) if a.coverage else "",
             "uvanlig_to_aar": int(a.coverage.flagged) if a.coverage else ""}
            for a in ds.municipal]


def contract_rows(ds: Dataset) -> list[dict[str, object]]:
    rows = []
    for a in ds.agencies + ds.health + ds.companies + ds.municipal_companies + ds.municipal:
        for c in a.contracts:
            rows.append({
                "regnskapsår": ds.year, "virksomhet": a.name, "virksomhet_orgnr": a.orgnr,
                "kontrakt_id": c.id, "tittel": c.title,
                "leverandorer": "; ".join(w.name for w in c.suppliers),
                "leverandor_orgnr": "; ".join(w.orgnr or "" for w in c.suppliers),
                "publisert": c.published or "", "rammeavtale": int(c.is_framework),
                "verdi_kr": round(c.value) if c.value is not None else "",
                "verditype": c.value_type or "",
                "andel_i_året_kr": round(c.year_value) if c.year_value is not None else "",
                "lenke": c.url or "",
            })
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    buf = io.StringIO()
    if rows:
        fields = list(dict.fromkeys(k for r in rows for k in r))  # årene kan ha ulike kolonner
        w = csv.DictWriter(buf, fieldnames=fields, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    path.write_text("\ufeff" + buf.getvalue(), encoding="utf-8")


def build_site(datasets: Dataset | list[Dataset], out: Path, base_url: str = "",
               breadth: Breadth | None = None, expiring: tuple[str, list[Expiring]] | None = None,
               municipal_history: dict[str, list[tuple[int, Agency]]] | None = None) -> int:
    """Skriver hele nettstedet til `out`. Returnerer antall HTML-sider.

    Med flere år blir index.html siste år og ar/<år>.html de andre årene. Hver
    virksomhet får én side for det siste året den har tall, med utviklingen over
    alle årene. `municipal_history` gir kommunesidene eldre KOSTRA-år uten egen årsside.
    """
    if isinstance(datasets, Dataset):
        datasets = [datasets]
    datasets = sorted(datasets, key=lambda d: d.year)
    years = [d.year for d in datasets]
    # Forsiden viser siste hele år; et delår (som 2026 jan–aug) får egen årsside.
    _PARTIAL.clear()
    _PARTIAL.update({d.year: d.through_month for d in datasets if d.partial})
    latest = next((d for d in reversed(datasets) if not d.partial), datasets[-1])
    _FRONT["year"] = latest.year
    _SITE["base_url"] = base_url.rstrip("/") + "/" if base_url else ""
    _SITE["data_date"] = max((d.data_date for d in datasets if d.data_date), default="",
                             key=lambda d: d.split(".")[::-1])
    _SITE["statsregnskap"] = "1" if any(a.spend_source for d in datasets for a in d.agencies) else ""
    _SITE["units"] = "1" if breadth and breadth.units else ""
    _SITE["health"] = "1" if any(d.health for d in datasets) else ""
    _SITE["companies"] = "1" if any(d.companies or d.municipal_companies for d in datasets) else ""
    _SITE["municipal"] = "1" if any(d.municipal for d in datasets) else ""
    _SITE["years"] = str(years[0]) if len(years) == 1 else f"{years[0]}/{years[-1]}"

    for sub in ("virksomhet", "ar", "leverandor", "helseforetak", "selskap", "kommune"):
        if (out / sub).exists():
            shutil.rmtree(out / sub)
    (out / "virksomhet").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)
    shutil.copytree(ASSETS, out / "assets", dirs_exist_ok=True)
    (out / ".nojekyll").write_text("")

    pages = 2
    (out / "index.html").write_text(index_page(latest, years=years, breadth=breadth), encoding="utf-8")
    (out / "om.html").write_text(about_page(latest), encoding="utf-8")
    (out / "404.html").write_text(not_found_page(), encoding="utf-8")
    (out / "vilkar.html").write_text(terms_page(), encoding="utf-8")
    (out / "varsler.html").write_text(alerts_page(*(expiring or ("", []))), encoding="utf-8")
    pages += 2
    if len(datasets) > 1:
        (out / "ar").mkdir()
        for ds in (d for d in datasets if d is not latest):
            (out / "ar" / f"{ds.year}.html").write_text(index_page(ds, root="../", years=years), encoding="utf-8")
            pages += 1

    suppliers = supplier_index(datasets, latest.year) if any(d.has_suppliers for d in datasets) else []
    (out / "leverandor").mkdir()
    (out / "leverandorer.html").write_text(suppliers_page(datasets, suppliers, latest), encoding="utf-8")
    (out / "data" / "leverandorer.json").write_text(suppliers_json(datasets, suppliers), encoding="utf-8")
    for sup in suppliers:
        (out / "leverandor" / f"{sup.orgnr}.html").write_text(supplier_page(datasets, sup, latest),
                                                                encoding="utf-8")
    pages += 1 + len(suppliers)
    linked = {sup.orgnr for sup in suppliers}

    history: dict[str, list[tuple[int, Agency]]] = {}
    newest: dict[str, tuple[Dataset, Agency]] = {}
    for ds in datasets:
        for a in ds.agencies:
            history.setdefault(a.slug, []).append((ds.year, a))
            if not ds.partial or a.slug not in newest:
                newest[a.slug] = (ds, a)
    for slug, (ds, a) in newest.items():
        (out / "virksomhet" / f"{slug}.html").write_text(agency_page(ds, a, history[slug], linked), encoding="utf-8")
        pages += 1

    if _SITE["health"]:
        (out / "helseforetak").mkdir()
        (out / "helseforetak.html").write_text(health_index_page(datasets, latest), encoding="utf-8")
        pages += 1
        h_history: dict[str, list[tuple[int, Agency]]] = {}
        for ds in datasets:
            for a in ds.health:
                h_history.setdefault(a.slug, []).append((ds.year, a))
        for a in latest.health:
            (out / a.path).write_text(health_page(latest, a, h_history[a.slug], linked), encoding="utf-8")
            pages += 1

    if _SITE["companies"]:
        (out / "selskap").mkdir()
        (out / "selskaper.html").write_text(company_index_page(datasets, latest), encoding="utf-8")
        pages += 1
        if any(d.municipal_companies for d in datasets):
            (out / "kommunale-selskaper.html").write_text(company_index_page(datasets, latest, "kommune"),
                                                          encoding="utf-8")
            pages += 1
        c_history: dict[str, list[tuple[int, Agency]]] = {}
        for ds in datasets:
            for a in ds.companies + ds.municipal_companies:
                c_history.setdefault(a.slug, []).append((ds.year, a))
        for a in latest.companies + latest.municipal_companies:
            (out / a.path).write_text(company_page(latest, a, c_history[a.slug], linked), encoding="utf-8")
            pages += 1

    if _SITE["municipal"]:
        (out / "kommune").mkdir()
        (out / "kommuner.html").write_text(municipal_index_page(datasets, latest, municipal_history),
                                           encoding="utf-8")
        pages += 1
        m_history: dict[str, list[tuple[int, Agency]]] = {}
        m_newest: dict[str, tuple[Dataset, Agency]] = {}
        for ds in datasets:
            for a in ds.municipal:
                m_history.setdefault(a.slug, []).append((ds.year, a))
                if a.until_year or ds is latest:  # de delte fylkeskommunene får siden fra siste år de fantes
                    m_newest[a.slug] = (ds, a)
        for slug, (ds, a) in m_newest.items():
            history = m_history[slug] + (municipal_history or {}).get(a.orgnr, [])
            (out / a.path).write_text(municipal_page(ds, a, history, linked), encoding="utf-8")
            pages += 1

    rows = [r for ds in datasets for r in export_rows(ds)]
    _write_csv(out / "data" / "virksomheter.csv", rows)
    _write_csv(out / "data" / "kontrakter.csv", [r for ds in datasets for r in contract_rows(ds)])
    if _SITE["health"]:
        _write_csv(out / "data" / "helseforetak.csv", [r for ds in datasets for r in health_rows(ds)])
    if _SITE["companies"]:
        _write_csv(out / "data" / "selskaper.csv", [r for ds in datasets for r in company_rows(ds)])
    if _SITE["municipal"]:
        _write_csv(out / "data" / "kommuner.csv", [r for ds in datasets for r in municipal_rows(ds)])
    meta = {"år": years, "årsfordelt": latest.annualized, "terskel": latest.threshold,
            "lisens": "NLOD 2.0", "kilder": ["Doffin (Digdir)", "Kudos (DFØ)", "Statsregnskapet (DFØ)"]
            + (["Enhetsregisteret (Brønnøysundregistrene)"] if _SITE["units"] else []),
            "virksomheter": rows}
    (out / "data" / "virksomheter.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    write_sitemap(out)
    return pages


def write_sitemap(out: Path) -> None:
    """sitemap.xml og robots.txt for søkemotorene (bare når offentlig adresse er kjent)."""
    base = _SITE["base_url"]
    for name in ("sitemap.xml", "robots.txt"):
        (out / name).unlink(missing_ok=True)
    if not base:
        return
    paths = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.html") if p.name != "404.html")
    urls = "".join(f"<url><loc>{escape(base + ('' if p == 'index.html' else p))}</loc></url>\n" for p in paths)
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n', encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}sitemap.xml\n", encoding="utf-8")
