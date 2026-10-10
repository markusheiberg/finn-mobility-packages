"""Private cars in Sweden, Tradera vs Blocket: listing age and price distribution.

    python3 private_cars_se_compare.py            # full census of both sites
    python3 private_cars_se_compare.py --test     # 2 pages per site, to check parsing

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

Writes aggregates only to data/private_cars_se/ (committed); per-listing rows go
to runs/ (gitignored), as for the package scrapers. Polite: one request at a
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
    f = (lambda x: f"{x // 1000:,}k".replace(",", " ")) if unit == "kr" else str
    return f"{f(lo)}+" if hi is None else f"{f(lo)}–{f(hi)}"


def write_outputs(tr, tr_total, bl, bl_total, bands, now: datetime, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    tr_age = [(now - datetime.fromisoformat(t["startDate"].replace("Z", "+00:00")[:26] + "+00:00"
                                            if "." in t["startDate"] else t["startDate"].replace("Z", "+00:00"))
               ).total_seconds() / 86400 for t in tr if t["startDate"]]
    tr_price = [int(t["price"]) for t in tr if t["price"] and int(t["price"]) > 0]
    bl_card = [c["card_age_min"] / 1440 for c in bl if c["card_age_min"] is not None]
    bl_id = [c["id_age_min"] / 1440 for c in bl if c.get("id_age_min") is not None]
    bl_price = [c["price"] for c in bl if c["price"]]

    series = {
        "Tradera, days since published": tr_age,
        "Blocket, days since published (ID estimate)": bl_id,
        "Blocket, days since published or renewed": bl_card,
    }
    with open(out_dir / "listing_age.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["age_days", *series])
        for i, (lo, hi) in enumerate(AGE_BANDS):
            w.writerow([label(lo, hi), *[round(dist(v, AGE_BANDS)[i], 1) for v in series.values()]])
    with open(out_dir / "price.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["price_sek", "tradera_pct", "blocket_pct"])
        dt, db = dist(tr_price, PRICE_BANDS), dist(bl_price, PRICE_BANDS)
        for i, (lo, hi) in enumerate(PRICE_BANDS):
            w.writerow([label(lo, hi, "kr"), round(dt[i], 1), round(db[i], 1)])

    sp = lambda x: f"{x:,.0f}".replace(",", " ")            # 12 345, Swedish style
    med = lambda xs: statistics.median(xs) if xs else float("nan")
    q = lambda xs, p: statistics.quantiles(xs, n=4)[p] if len(xs) > 3 else float("nan")
    lines = [
        "# Private cars in Sweden: Tradera vs Blocket",
        "",
        f"Census taken {now:%Y-%m-%d %H:%M} UTC by `private_cars_se_compare.py`.",
        "",
        "| | Tradera | Blocket |",
        "|---|---:|---:|",
        f"| Private car listings (site count) | {sp(tr_total)} | {sp(bl_total)} |",
        f"| Listings read | {sp(len(tr))} | {sp(len(bl))} |",
        f"| Median price, kr | {sp(med(tr_price))} | {sp(med(bl_price))} |",
        f"| Price, 25th–75th percentile, kr | {sp(q(tr_price, 0))}–{sp(q(tr_price, 2))} | "
        f"{sp(q(bl_price, 0))}–{sp(q(bl_price, 2))} |",
        f"| Median days since published | {med(tr_age):.0f} | {med(bl_id):.0f} (ID estimate) |",
        f"| Median days since published or renewed | n/a | {med(bl_card):.0f} |",
        "",
        "## Days since published, % of listings",
        "",
        "| days | Tradera | Blocket (ID estimate) | Blocket (published or renewed) |",
        "|---|---:|---:|---:|",
    ]
    for i, (lo, hi) in enumerate(AGE_BANDS):
        lines.append(f"| {label(lo, hi)} | " + " | ".join(f"{dist(v, AGE_BANDS)[i]:.1f}" for v in series.values()) + " |")
    lines += ["", "## Price, % of listings", "", "| price, kr | Tradera | Blocket |", "|---|---:|---:|"]
    dt, db = dist(tr_price, PRICE_BANDS), dist(bl_price, PRICE_BANDS)
    for i, (lo, hi) in enumerate(PRICE_BANDS):
        lines.append(f"| {label(lo, hi, 'kr')} | {dt[i]:.1f} | {db[i]:.1f} |")
    lines += ["", "## How to read this", "",
              "- **Tradera** dates are exact: `startDate` of a 60-day classified. A car relisted after 60 days restarts its clock.",
              "- **Blocket (ID estimate)** is the closest Blocket gets to a publish date: ad IDs are issued in order, so an ad was created no later than the newest card time among ads with a higher ID. It can only overstate freshness slightly, never invent age.",
              "- **Blocket (published or renewed)** is the card's own time. Renewals and paid bumps reset it, so it understates age; the gap between the two Blocket columns is how much renewing goes on.",
              "- Paid placements (\"Betald placering\") are left out of Blocket; they repeat across pages.",
              f"- Blocket was read through {len(bands)} price bands so every listing is reachable past its 50-page search limit."]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    now = NOW
    tr, tr_total = tradera_census(a.test)
    bl, bl_total, bands = blocket_census(a.test)
    blocket_id_ages(bl)
    runs = Path("runs"); runs.mkdir(exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    for name, rows in (("tradera", tr), ("blocket", bl)):
        if rows:
            with open(runs / f"private_cars_se_{name}_{stamp}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) + (["id_age_min"] if name == "blocket" and "id_age_min" not in rows[0] else []))
                w.writeheader(); w.writerows(rows)
    out = Path("runs/test_private_cars_se") if a.test else Path("data/private_cars_se")
    print(write_outputs(tr, tr_total, bl, bl_total, bands, now, out))


if __name__ == "__main__":
    sys.exit(main())
