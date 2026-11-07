"""Tallene i pressesakene (plan/salg.md, «Pressesaker»), regnet ut på nytt fra primærkildene.

Hver vinkel kan kjøres for seg, så en journalist eller vi selv kan etterprøve hvert tall:

  barnevern   Statsregnskapet: kap. 0855 post 22 «Kjøp av private barnevernstjenester» og kap. 0856
              post 22 «Kjøp av plasser i private omsorgssentre», per år (rådata-xlsx fra
              https://statsregnskapet.dfo.no/last-ned). Doffin: alle kunngjøringer der Bufdir/Bufetat
              er kjøper, og hvilke av dem som gjelder kjøp av private omsorgs- og barnevernstjenester.
  kjoper      For én kjøper i Doffin: konkurranser og kunngjøringer om tildeling per år, og hvor mange
              av konkurransene som er sendt til TED (over EØS-terskelverdien).
  landet      Alle konkurranser og tildelinger i Doffin for valgte år, summert per kjøper. Tar rundt en
              time, fordi Doffin bare gir 1000 treff per søk.

Doffin-søket er det samme som nettsiden doffin.no bruker (ingen nøkkel), med 1,2 sekunder mellom kall.

Eksempler (fra repo-roten):
  python scripts/presse_tall.py barnevern --raadata /mnt/project-files/kronesporet/data/statsregnskap_raadata
  python scripts/presse_tall.py kjoper --navn "Nordland fylkeskommune" --orgnr 964982953
  python scripts/presse_tall.py landet --ar 2023,2024,2025 --ut /tmp/landet.csv
"""
from __future__ import annotations

import argparse
import calendar
import csv
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.collectors.statsregnskap import read_xlsx  # noqa: E402

SEARCH = "https://api.doffin.no/webclient/api/v2/search-api/search"
PAUSE = 1.2
COMPETITION = "ANNOUNCEMENT_OF_COMPETITION"
AWARD = "ANNOUNCEMENT_OF_CONCLUSION_OF_CONTRACT"

BUFDIR_ORGNR = "986128433"
# Kjøp av private omsorgstjenester i Doffin-titlene til Bufdir/Bufetat. Forskning, evaluering og opplæring
# om barnevern er ikke kjøp av plasser, og telles ikke.
OMSORG = re.compile(r"plasser|senter for foreldre og barn|sentre for foreldre og barn|samvær med støttet tilsyn|"
                    r"institusjonsplass|fosterhjemsplass|kjøp av (?:tjenester i )?private", re.I)
IKKE_OMSORG = re.compile(r"evaluering|forskning|følge|kunnskap|kartlegging|studie|opplæring|utredning|"
                         r"rekruttering|kommunikasjon|renhold|fou-|veileder|vikar", re.I)

session = requests.Session()


def _post(body: dict[str, Any]) -> dict[str, Any]:
    for attempt in range(5):
        try:
            r = session.post(SEARCH, json=body, timeout=120)
            r.raise_for_status()
            time.sleep(PAUSE)
            return r.json()
        except requests.RequestException as e:
            print(f"  Doffin svarte ikke ({e}), prøver igjen", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise SystemExit("Doffin svarer ikke. Prøv igjen senere.")


def search(text: str = "", facets: dict[str, Any] | None = None, size: int = 100) -> list[dict[str, Any]]:
    """Alle treff for et søk (maks 1000, som Doffin tillater)."""
    hits: list[dict[str, Any]] = []
    for page in range(1, 11):
        d = _post({"numHitsPerPage": size, "page": page, "searchString": text,
                   "sortBy": "PUBLICATION_DATE_DESC", "facets": facets or {}})
        hits += d["hits"]
        if len(d["hits"]) < size or len(hits) >= d["numHitsTotal"]:
            break
    return hits


def _clean(orgnr: str | None) -> str:
    return re.sub(r"\D", "", orgnr or "")


def buyer_ids(name: str, orgnr: str) -> set[str]:
    """Doffins interne kjøper-id-er for et orgnr. Samme kjøper kan ha flere, ett per navnevariant."""
    ids: set[str] = set()
    for h in search(name):
        for b in h.get("buyer") or []:
            if _clean(b.get("organizationId")) == orgnr:
                ids.add(b["id"])
    return ids


def notices_for_buyer(hid: str, year_from: int = 2017, year_to: int = 2026) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for y in range(year_from, year_to + 1):
        out += search(facets={"buyer": {"checkedItems": [hid]},
                              "publicationDate": {"from": f"{y}-01-01", "to": f"{y}-12-31"}})
    return out


def _notice_url(nid: str) -> str:
    return f"https://doffin.no/notices/{nid}"


# ---------------------------------------------------------------------------
# Barnevern
# ---------------------------------------------------------------------------

def statsregnskap_poster(folder: Path, poster: list[tuple[str, str]]) -> dict[tuple[str, str, int], float]:
    """Sum per (kap, post, år) fra rådatafilene. Desemberfilen gir hele året; augustfilen jan–aug."""
    sums: dict[tuple[str, str, int], float] = defaultdict(float)
    for f in sorted(folder.glob("*.xlsx")):
        rows = read_xlsx(f)
        head_i = next(i for i, r in enumerate(rows[:20]) if "Artskonto" in r.values())
        col = {v: k for k, v in rows[head_i].items()}
        year = int(re.search(r"(20\d\d)", f.name).group(1))
        for r in rows[head_i + 1:]:
            key = (r.get(col["Kap"], ""), r.get(col["Post"], ""))
            if key in poster:
                sums[(key[0], key[1], year)] += float(r.get(col["Regnskap HIÅ"], "0") or 0)
        print(f"  lest {f.name}", file=sys.stderr)
    return sums


def cmd_barnevern(args: argparse.Namespace) -> None:
    poster = [("0855", "22"), ("0856", "22")]
    if args.raadata:
        sums = statsregnskap_poster(Path(args.raadata), poster)
        print("Statsregnskapet (rådata, bevilgningsregnskap), mill. kr")
        for kap, post in poster:
            for y in sorted({k[2] for k in sums}):
                if (kap, post, y) in sums:
                    print(f"  kap. {kap} post {post}  {y}: {sums[(kap, post, y)] / 1e6:10.1f}")
    ids = buyer_ids("Barne-, ungdoms- og familiedirektoratet", BUFDIR_ORGNR) | \
        buyer_ids("Bufetat", BUFDIR_ORGNR) | buyer_ids("Bufdir", BUFDIR_ORGNR)
    seen: dict[str, dict[str, Any]] = {}
    for hid in sorted(ids):
        for h in notices_for_buyer(hid):
            seen[h["id"]] = h
    print(f"\nDoffin: {len(seen)} kunngjøringer med Bufdir/Bufetat (orgnr {BUFDIR_ORGNR}) som kjøper, "
          f"{len(ids)} kjøper-id-er")
    print("  " + ", ".join(f"{t}: {n}" for t, n in Counter(h["type"] for h in seen.values()).most_common()))
    print("\nKunngjøringer om kjøp av private omsorgstjenester:")
    rows = []
    for h in sorted(seen.values(), key=lambda h: h["publicationDate"]):
        title = h.get("heading") or ""
        if OMSORG.search(title) and not IKKE_OMSORG.search(title):
            value = (h.get("estimatedValue") or {}).get("amount")
            rows.append(h)
            print(f"  {h['publicationDate']}  {h['type']:<40} {value or '':>12}  {title[:80]}  {_notice_url(h['id'])}")
    if args.ut:
        with open(args.ut, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["doffin_id", "publisert", "type", "oppgitt_verdi_nok", "tittel", "lenke"])
            for h in rows:
                w.writerow([h["id"], h["publicationDate"], h["type"],
                            (h.get("estimatedValue") or {}).get("amount") or "", h.get("heading"), _notice_url(h["id"])])
        print(f"\nSkrev {args.ut}")


# ---------------------------------------------------------------------------
# Én kjøper
# ---------------------------------------------------------------------------

def cmd_kjoper(args: argparse.Namespace) -> None:
    ids = buyer_ids(args.navn, args.orgnr)
    seen: dict[str, dict[str, Any]] = {}
    for hid in sorted(ids):
        for h in notices_for_buyer(hid, args.fra, args.til):
            seen[h["id"]] = h
    print(f"{args.navn} ({args.orgnr}): {len(seen)} kunngjøringer, kjøper-id-er {sorted(ids)}")
    print(f"{'år':<6}{'konkurranser':>14}{'  herav til TED':>16}{'tildelinger':>13}")
    by_year: dict[str, Counter] = defaultdict(Counter)
    for h in seen.values():
        y = h["publicationDate"][:4]
        by_year[y][h["type"]] += 1
        if h["type"] == COMPETITION and h.get("sentToTed"):
            by_year[y]["ted"] += 1
    for y in sorted(by_year):
        c = by_year[y]
        print(f"{y:<6}{c[COMPETITION]:>14}{c['ted']:>16}{c[AWARD]:>13}")


# ---------------------------------------------------------------------------
# Hele landet
# ---------------------------------------------------------------------------

def crawl(notice_type: str, years: list[int]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for y in years:
        for m in range(1, 13):
            last = calendar.monthrange(y, m)[1]
            for a, b in ((1, 10), (11, 20), (21, last)):
                d = _post({"numHitsPerPage": 1000, "page": 1, "searchString": "", "sortBy": "PUBLICATION_DATE_DESC",
                           "facets": {"type": {"checkedItems": [notice_type]},
                                      "publicationDate": {"from": f"{y}-{m:02d}-{a:02d}", "to": f"{y}-{m:02d}-{b:02d}"}}})
                if d["numHitsTotal"] > len(d["hits"]):
                    print(f"  {y}-{m:02d}-{a:02d}: {d['numHitsTotal']} treff, fikk {len(d['hits'])}", file=sys.stderr)
                for h in d["hits"]:
                    out[h["id"]] = h
            print(f"  {notice_type} {y}-{m:02d}: {len(out)}", file=sys.stderr)
    return out


def cmd_landet(args: argparse.Namespace) -> None:
    years = [int(y) for y in args.ar.split(",")]
    comps = crawl(COMPETITION, years)
    awards = crawl(AWARD, years)
    per: dict[str, dict[str, Any]] = defaultdict(lambda: {"navn": "", "orgnr": "", "konk": 0, "ted": 0,
                                                          "ted_tildelt": 0, "tildelinger": 0})
    # Første kjøper i kunngjøringen, slått sammen på orgnr (Doffin har ofte flere id-er per kjøper).
    def key(b: dict[str, Any]) -> str:
        return _clean(b.get("organizationId")) or b["id"]

    for h in comps.values():
        for b in (h.get("buyer") or [])[:1]:
            p = per[key(b)]
            p["navn"], p["orgnr"] = p["navn"] or b.get("name") or "", _clean(b.get("organizationId"))
            p["konk"] += 1
            if h.get("sentToTed"):
                p["ted"] += 1
                p["ted_tildelt"] += h.get("status") == "AWARDED"
    for h in awards.values():
        for b in (h.get("buyer") or [])[:1]:
            per[key(b)]["tildelinger"] += 1
            per[key(b)]["navn"] = per[key(b)]["navn"] or b.get("name") or ""
    n_ted = sum(p["ted"] for p in per.values())
    print(f"{len(comps)} konkurranser ({n_ted} sendt til TED) og {len(awards)} tildelinger i {args.ar}")
    with open(args.ut, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["orgnr_eller_doffin_id", "navn", "orgnr", "konkurranser", "konkurranser_til_ted",
                    "ted_med_status_tildelt", "tildelinger"])
        for hid, p in sorted(per.items(), key=lambda kv: -kv[1]["ted"]):
            w.writerow([hid, p["navn"], p["orgnr"], p["konk"], p["ted"], p["ted_tildelt"], p["tildelinger"]])
    print(f"Skrev {args.ut}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("barnevern")
    b.add_argument("--raadata", help="mappe med rådata-xlsx fra statsregnskapet")
    b.add_argument("--ut", help="skriv Doffin-kunngjøringene om omsorgstjenester til denne CSV-fila")
    k = sub.add_parser("kjoper")
    k.add_argument("--navn", required=True)
    k.add_argument("--orgnr", required=True)
    k.add_argument("--fra", type=int, default=2017)
    k.add_argument("--til", type=int, default=2026)
    land = sub.add_parser("landet")
    land.add_argument("--ar", default="2023,2024,2025")
    land.add_argument("--ut", required=True)
    args = ap.parse_args()
    {"barnevern": cmd_barnevern, "kjoper": cmd_kjoper, "landet": cmd_landet}[args.cmd](args)


if __name__ == "__main__":
    main()
