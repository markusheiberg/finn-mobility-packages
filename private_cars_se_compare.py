"""Private cars in Sweden, Tradera vs Blocket: listing age and price distribution.

    python3 private_cars_se_compare.py --bigquery           # what Cloud Run runs (run_all.py)
    python3 private_cars_se_compare.py --test               # 2 pages per site, to check parsing
    python3 private_cars_se_compare.py --render HISTORY_CSV # the Monday export: report, no scraping

Question it answers: when Tradera's private car count grows, is it the same kind
of stock as Blocket's (similar prices, similar freshness), or something else?

What "published" means on each site — they are NOT the same thing, so both
Blocket measures are reported side by side:

  Tradera   `startDate` on every listing in the search results. Private car
            listings are 60-day classifieds (itemType ContactOnly), so this is the
            day the ad went up. A car relisted after 60 days gets a new date.
  Blocket   no publish date anywhere. Two proxies, read off the search cards:
            - card age ("2 min", "5 dagar"): time since the ad was published OR
              last renewed/bumped. Renewals reset it, so it understates age.
            - ID estimate: Blocket ad IDs are issued in sequence, and an ad's card
              time is never earlier than its creation. So creation(ID) is at most
              the earliest card time among all ads with an ID >= it. With ~26k
              cards, most never renewed, that bound is tight. This is the closest
              thing to a true publish date Blocket exposes.

Blocket caps search pagination at 50 pages and lists newest first, so the census
walks narrow price bands (split until each fits under the cap) — sampling the
unfiltered search would only ever see the newest ads.

Runs weekly in Cloud Run, after the package scrapes, and appends summary rows
(one per site x metric x band) to market_scraper.private_cars_se. The Monday
export copies that table to data/private_cars_se/history.csv and renders
summary.md from the latest run. Per-listing rows are never stored. Polite: one request at a
time with a delay; both sites' robots.txt allow these paths (checked 2026-10-10).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
}
DELAY = 1.0
TRADERA_URL = "https://www.tradera.com/category/1001?sellerType=Private"
BLOCKET_URL = "https://www.blocket.se/mobility/search/car?dealer_segment=3"
BLOCKET_PAGE_SIZE = 50
BLOCKET_MAX_PAGES = 50
BLOCKET_BAND_CAP = BLOCKET_PAGE_SIZE * BLOCKET_MAX_PAGES - 50   # headroom for churn

PRICE_BANDS = [(0, 25_000), (25_000, 50_000), (50_000, 75_000), (75_000, 100_000),
               (100_000, 150_000), (150_000, 200_000), (200_000, 300_000),
               (300_000, 500_000), (500_000, None)]
AGE_BANDS = [(0, 1), (1, 7), (7, 14), (14, 30), (30, 60), (60, 90), (90, 180),
             (180, 365), (365, None)]

_session = requests.Session()
_session.headers.update(HEADERS)


def log(*a):
    print(*a, flush=True)


def get(url: str) -> str:
    for attempt in range(4):
        try:
            r = _session.get(url, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            time.sleep(DELAY)
            return r.text
        except Exception as e:
            wait = 2 ** (attempt + 1)
            log(f"  [WARN] {url} -> {e}; retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


# ── Tradera ──────────────────────────────────────────────────────────────────

ITEM_FIELDS = {
    "price": r'"price":(\d+)',
    "itemType": r'"itemType":"([^"]+)"',
    "startDate": r'"startDate":"([^"]+)"',
    "sellerIsCompany": r'"sellerIsCompany":(true|false)',
}


def tradera_page(n: int) -> tuple[list[dict], int, int]:
    raw = get(f"{TRADERA_URL}&paging={n}" if n > 1 else TRADERA_URL)
    t = raw.replace('\\"', '"')
    total = int(re.search(r'"totalItemCount":(\d+)', t).group(1))
    pages = int(re.search(r'"pageCount":(\d+)', t).group(1))
    items = []
    starts = [m.start() for m in re.finditer(r'\{"itemId":\d+', t)]
    for i, s in enumerate(starts):
        block = t[s:starts[i + 1] if i + 1 < len(starts) else s + 6000]
        it = {"id": re.match(r'\{"itemId":(\d+)', block).group(1)}
        for k, pat in ITEM_FIELDS.items():
            m = re.search(pat, block)
            it[k] = m.group(1) if m else ""
        items.append(it)
    return items, total, pages


def tradera_census(test: bool) -> tuple[list[dict], int]:
    items, total, pages = tradera_page(1)
    log(f"[TRADERA] total={total} pages={pages}")
    for n in range(2, (2 if test else pages) + 1):
        more, _, _ = tradera_page(n)
        items += more
        log(f"[TRADERA] page {n}/{pages}: {len(more)} items")
    seen, out = set(), []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        out.append(it)
    return out, total


# ── Blocket ──────────────────────────────────────────────────────────────────

REL_UNITS = [  # (pattern, minutes per unit)
    (r"(\d+)\s*min", 1), (r"(\d+)\s*tim", 60), (r"(\d+)\s*dag", 1440),
    (r"(\d+)\s*(?:v\b|veck)", 10080), (r"(\d+)\s*mån", 43200), (r"(\d+)\s*år", 525600),
]
REL_RE = re.compile(r"^\d+\s*(min|tim|timmar|dag|dagar|v|veckor|vecka|mån|månad|månader|år)$")


# Older cards show a date instead: "17 sep", or "17 sep 2024" past a year.
MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
DATE_RE = re.compile(r"^(\d{1,2})\s+(" + "|".join(MONTHS) + r")\.?(?:\s+(\d{4}))?$", re.I)
NOW = datetime.now(timezone.utc)


def card_minutes(s: str) -> float | None:
    """Minutes since a card's own time: relative ("5 dagar") or a date ("17 sep")."""
    if REL_RE.match(s):
        return rel_minutes(s)
    m = DATE_RE.match(s)
    if not m:
        return None
    day, mon = int(m.group(1)), MONTHS.index(m.group(2).lower()) + 1
    year = int(m.group(3)) if m.group(3) else NOW.year
    d = datetime(year, mon, day, 12, tzinfo=timezone.utc)   # noon: the day is all we know
    if not m.group(3) and d > NOW:                           # no year = its latest past occurrence
        d = d.replace(year=year - 1)
    return (NOW - d).total_seconds() / 60


def rel_minutes(s: str) -> float | None:
    for pat, mult in REL_UNITS:
        m = re.search(pat, s)
        if m:
            return int(m.group(1)) * mult
    return None


def blocket_search(params: str, page: int = 1) -> tuple[int | None, list[dict]]:
    url = f"{BLOCKET_URL}&{params}" + (f"&page={page}" if page > 1 else "")
    soup = BeautifulSoup(get(url), "html.parser")
    total = None
    for tag in soup.find_all(["h1", "h2", "span", "p", "div"]):
        m = re.search(r"([\d\s\xa0 ]+)\s*(resultat|träffar|annonser)", tag.get_text(" ", strip=True))
        if m:
            try:
                total = int(re.sub(r"\D", "", m.group(1)))
                break
            except ValueError:
                pass
    cards = []
    for a in soup.find_all("article"):
        link = a.select_one('a[href*="/mobility/item/"]')
        if not link:
            continue
        m = re.search(r"/mobility/item/(\d+)", link.get("href", ""))
        parts = [p.strip() for p in a.get_text("|", strip=True).split("|")]
        price, age = None, None
        for i, p in enumerate(parts):
            if p == "kr" and i and price is None:
                digits = re.sub(r"\D", "", parts[i - 1])
                price = int(digits) if digits else None
            if age is None:
                age = card_minutes(p)
        cards.append({"id": m.group(1) if m else "", "price": price, "card_age_min": age,
                      "paid": "Betald placering" in parts})
    return total, cards


def blocket_bands(lo: int, hi: int | None) -> list[tuple[int, int | None, int]]:
    """Split [lo, hi) until each band fits under the pagination cap."""
    q = f"price_from={lo}" + (f"&price_to={hi - 1}" if hi else "")
    total, _ = blocket_search(q)
    total = total or 0
    if total <= BLOCKET_BAND_CAP or (hi is not None and hi - lo <= 1000):
        return [(lo, hi, total)]
    mid = (lo + (hi if hi else lo * 2 + 100_000)) // 2
    mid = round(mid, -3)
    return blocket_bands(lo, mid) + blocket_bands(mid, hi)


def blocket_census(test: bool) -> tuple[list[dict], int, list]:
    total, _ = blocket_search("")
    log(f"[BLOCKET] total={total}")
    bands = blocket_bands(0, 200_000) + blocket_bands(200_000, None)
    log(f"[BLOCKET] {len(bands)} price bands, counts sum {sum(b[2] for b in bands)}")
    cards = []
    for lo, hi, n in bands:
        q = f"price_from={lo}" + (f"&price_to={hi - 1}" if hi else "")
        pages = min(-(-n // BLOCKET_PAGE_SIZE), BLOCKET_MAX_PAGES)
        for p in range(1, (min(pages, 1) if test else pages) + 1):
            _, cs = blocket_search(q, p)
            cards += cs
        log(f"[BLOCKET] band {lo}-{hi or '+'}: {n} listed, {len(cards)} cards so far")
        if test and len(cards) > 100:
            break
    seen, out = set(), []
    for c in cards:
        if c["id"] and c["id"] not in seen and not c["paid"]:
            seen.add(c["id"])
            out.append(c)
    return out, total or 0, bands


def blocket_id_ages(cards: list[dict]) -> None:
    """Estimated days since ORIGINAL publish from the ID sequence (see module doc)."""
    best = None
    for c in sorted((c for c in cards if c["card_age_min"] is not None),
                    key=lambda c: int(c["id"]), reverse=True):
        # card age in minutes is "how long ago"; creation is at least that long ago
        # for this ad, and at most as long ago as any later-issued ad's card age.
        best = c["card_age_min"] if best is None else max(best, c["card_age_min"])
        c["id_age_min"] = best


# ── summaries ────────────────────────────────────────────────────────────────
#
# One set of numbers, three destinations: summarise() turns a census into long
# rows (run_timestamp, site, metric, band, value); those rows go to BigQuery
# (production, the Cloud Run job), to a history CSV (local runs), and render()
# draws the report from them. The Monday export re-renders the report from the
# BigQuery history, so the committed summary can never disagree with the table.

BQ_TABLE = "vend-scrapers-v2.market_scraper.private_cars_se"
ROW_COLS = ["run_timestamp", "site", "metric", "band", "value"]
AGE_MEASURES = [  # (site, metric stem, column title)
    ("tradera", "age_published", "Tradera"),
    ("blocket", "age_published_id_estimate", "Blocket (ID estimate)"),
    ("blocket", "age_published_or_renewed", "Blocket (published or renewed)"),
]


def band_of(v: float, bands) -> int:
    for i, (lo, hi) in enumerate(bands):
        if v >= lo and (hi is None or v < hi):
            return i
    return len(bands) - 1


def dist(values: list[float], bands) -> list[float]:
    n = len(values) or 1
    counts = [0] * len(bands)
    for v in values:
        counts[band_of(v, bands)] += 1
    return [c / n * 100 for c in counts]


def label(lo, hi, unit=""):
    f = (lambda x: f"{x // 1000}k") if unit == "kr" else str
    return f"{f(lo)}+" if hi is None else f"{f(lo)}–{f(hi)}"


def iso_utc(s: str) -> datetime:
    """Tradera writes 7 fractional digits; Python reads at most 6."""
    s = re.sub(r"(\.\d{6})\d+", r"\1", s).replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def summarise(tr, tr_total, bl, bl_total, bands, now: datetime) -> list[dict]:
    ts = now.isoformat()
    rows = []

    def add(site, metric, band, v):
        rows.append({"run_timestamp": ts, "site": site, "metric": metric, "band": band,
                     "value": None if v is None or v != v else round(float(v), 2)})

    ages = {
        "tradera": {"age_published": [(now - iso_utc(t["startDate"])).total_seconds() / 86400
                                      for t in tr if t["startDate"]]},
        "blocket": {"age_published_id_estimate": [c["id_age_min"] / 1440 for c in bl
                                                  if c.get("id_age_min") is not None],
                    "age_published_or_renewed": [c["card_age_min"] / 1440 for c in bl
                                                 if c["card_age_min"] is not None]},
    }
    prices = {"tradera": [int(t["price"]) for t in tr if t["price"] and int(t["price"]) > 0],
              "blocket": [c["price"] for c in bl if c["price"]]}
    quart = lambda xs, i: statistics.quantiles(xs, n=4)[i] if len(xs) > 3 else None
    for site, total, read in (("tradera", tr_total, len(tr)), ("blocket", bl_total, len(bl))):
        p = prices[site]
        add(site, "listings_site_count", "", total)
        add(site, "listings_read", "", read)
        add(site, "median_price_sek", "", statistics.median(p) if p else None)
        add(site, "p25_price_sek", "", quart(p, 0))
        add(site, "p75_price_sek", "", quart(p, 2))
        for i, (lo, hi) in enumerate(PRICE_BANDS):
            add(site, "price_pct", label(lo, hi, "kr"), dist(p, PRICE_BANDS)[i])
    add("blocket", "price_bands_walked", "", len(bands))
    for site, stem, _ in AGE_MEASURES:
        a = ages[site][stem]
        add(site, f"median_{stem}_days", "", statistics.median(a) if a else None)
        for i, (lo, hi) in enumerate(AGE_BANDS):
            add(site, f"{stem}_pct", label(lo, hi), dist(a, AGE_BANDS)[i])
    return rows


def render(rows: list[dict], out_dir: Path) -> str:
    """Report for ONE run from its summary rows."""
    v = {}
    for r in rows:
        val = r["value"]
        v[(r["site"], r["metric"], r["band"])] = None if val in (None, "") else float(val)
    g = lambda site, metric, band="": v.get((site, metric, band))
    sp = lambda x: "–" if x is None else f"{x:,.0f}".replace(",", " ")
    d1 = lambda x: "–" if x is None else f"{x:.0f}"
    ts = rows[0]["run_timestamp"]
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "listing_age.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["age_days", *[t for _, _, t in AGE_MEASURES]])
        for lo, hi in AGE_BANDS:
            w.writerow([label(lo, hi), *[g(s, f"{m}_pct", label(lo, hi)) for s, m, _ in AGE_MEASURES]])
    with open(out_dir / "price.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["price_sek", "tradera_pct", "blocket_pct"])
        for lo, hi in PRICE_BANDS:
            b = label(lo, hi, "kr")
            w.writerow([b, g("tradera", "price_pct", b), g("blocket", "price_pct", b)])

    lines = [
        "# Private cars in Sweden: Tradera vs Blocket",
        "",
        f"Census of {str(ts)[:16].replace('T', ' ')} UTC, by `private_cars_se_compare.py` in the "
        "weekly Cloud Run job. History: `history.csv` (one block per week).",
        "",
        "| | Tradera | Blocket |",
        "|---|---:|---:|",
        f"| Private car listings (site count) | {sp(g('tradera', 'listings_site_count'))} | {sp(g('blocket', 'listings_site_count'))} |",
        f"| Listings read | {sp(g('tradera', 'listings_read'))} | {sp(g('blocket', 'listings_read'))} |",
        f"| Median price, kr | {sp(g('tradera', 'median_price_sek'))} | {sp(g('blocket', 'median_price_sek'))} |",
        f"| Price, 25th–75th percentile, kr | {sp(g('tradera', 'p25_price_sek'))}–{sp(g('tradera', 'p75_price_sek'))} | "
        f"{sp(g('blocket', 'p25_price_sek'))}–{sp(g('blocket', 'p75_price_sek'))} |",
        f"| Median days since published | {d1(g('tradera', 'median_age_published_days'))} | "
        f"{d1(g('blocket', 'median_age_published_id_estimate_days'))} (ID estimate) |",
        f"| Median days since published or renewed | – | {d1(g('blocket', 'median_age_published_or_renewed_days'))} |",
        "",
        "## Days since published, % of listings",
        "",
        "| days | " + " | ".join(t for _, _, t in AGE_MEASURES) + " |",
        "|---|---:|---:|---:|",
    ]
    for lo, hi in AGE_BANDS:
        lines.append(f"| {label(lo, hi)} | " + " | ".join(
            f"{g(s, f'{m}_pct', label(lo, hi)) or 0:.1f}" for s, m, _ in AGE_MEASURES) + " |")
    lines += ["", "## Price, % of listings", "", "| price, kr | Tradera | Blocket |", "|---|---:|---:|"]
    for lo, hi in PRICE_BANDS:
        b = label(lo, hi, "kr")
        lines.append(f"| {b} | {g('tradera', 'price_pct', b) or 0:.1f} | {g('blocket', 'price_pct', b) or 0:.1f} |")
    lines += ["", "## How to read this", "",
              "- **Tradera** dates are exact: `startDate` of a 60-day classified. A car relisted after 60 days restarts its clock.",
              "- **Blocket (ID estimate)** is the closest Blocket gets to a publish date: ad IDs are issued in order, so an ad was created no later than the earliest card time among ads with a higher ID. It can only overstate freshness slightly, never invent age.",
              "- **Blocket (published or renewed)** is the card's own time. Renewals and paid bumps reset it, so it understates age; the gap between the two Blocket columns is how much renewing goes on.",
              "- Paid placements (\"Betald placering\") are left out of Blocket; they repeat across pages.",
              f"- Blocket was read through {d1(g('blocket', 'price_bands_walked'))} price bands so every listing is reachable past its 50-page search limit."]
    text = "\n".join(lines) + "\n"
    (out_dir / "summary.md").write_text(text, encoding="utf-8")
    return text


def upsert_history(path: Path, rows: list[dict]) -> None:
    """Add a run to the history CSV; a re-run on the same date replaces that date."""
    day = rows[0]["run_timestamp"][:10]
    old = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            old = [r for r in csv.DictReader(f) if r["run_timestamp"][:10] != day]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ROW_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(old + rows, key=lambda r: r["run_timestamp"]))


def write_bigquery(rows: list[dict]) -> None:
    """Append one run. A load job, not streaming inserts: streaming into a table
    created seconds earlier can fail with 'not found', and this runs weekly."""
    from google.cloud import bigquery
    client = bigquery.Client(project=BQ_TABLE.split(".")[0])
    cfg = bigquery.LoadJobConfig(
        schema=[bigquery.SchemaField("run_timestamp", "TIMESTAMP"),
                bigquery.SchemaField("site", "STRING"),
                bigquery.SchemaField("metric", "STRING"),
                bigquery.SchemaField("band", "STRING"),
                bigquery.SchemaField("value", "FLOAT")],
        write_disposition="WRITE_APPEND",
        create_disposition="CREATE_IF_NEEDED")
    client.load_table_from_json(rows, BQ_TABLE, job_config=cfg).result()
    log(f"[BQ] Appended {len(rows)} rows to {BQ_TABLE}")


def render_from_history(path: Path, out_dir: Path) -> str:
    """The Monday export: draw the report from the latest run in the history."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    latest = max(r["run_timestamp"] for r in rows)
    return render([r for r in rows if r["run_timestamp"] == latest], out_dir)


def run(test: bool = False, bigquery: bool = False, out: Path | None = None,
        history: Path | None = None) -> list[dict]:
    now = NOW
    tr, tr_total = tradera_census(test)
    bl, bl_total, bands = blocket_census(test)
    blocket_id_ages(bl)
    rows = summarise(tr, tr_total, bl, bl_total, bands, now)
    if bigquery:
        write_bigquery(rows)
    if history:
        upsert_history(history, rows)
    if out:
        print(render(rows, out))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--test", action="store_true", help="2 pages per site, no BigQuery")
    ap.add_argument("--bigquery", action="store_true", help="append the run to " + BQ_TABLE)
    ap.add_argument("--out", default=None, help="write summary.md + CSVs here")
    ap.add_argument("--history", default=None, help="upsert the run into a history CSV")
    ap.add_argument("--render", default=None, metavar="HISTORY_CSV",
                    help="no scraping: render --out from the latest run in this history")
    a = ap.parse_args()
    if a.render:
        print(render_from_history(Path(a.render), Path(a.out or "data/private_cars_se")))
        return
    run(test=a.test, bigquery=a.bigquery and not a.test,
        out=Path(a.out) if a.out else (Path("runs/test_private_cars_se") if a.test else None),
        history=Path(a.history) if a.history else None)


if __name__ == "__main__":
    sys.exit(main())
