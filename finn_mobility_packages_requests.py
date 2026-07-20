import math
import os
import random
import re
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from google.cloud import bigquery

SEARCH_URL = "https://www.finn.no/mobility/search/car"
AD_URL = "https://www.finn.no/mobility/item/{}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nb-NO,nb;q=0.9,en-US;q=0.8,en;q=0.7",
}

SAMPLE_FRACTION = 0.10
MAX_PAGES = 50  # only sample the newest listings; deep pages may not exist anyway
RUNS_DIR = "runs"
BQ_PROJECT = "vend-scrapers-v2"
BQ_DATASET = "market_scraper"
BQ_TABLE = "mobility_packages"

# Shared HTTP session: reuses TCP/TLS connections across requests.
_session = requests.Session()
_session.headers.update(HEADERS)

# Global dealer cache: org_name -> "premium" | "pluss" | "basis"
_dealer_cache: dict[str, str] = {}


def log(*args):
    print(*args, flush=True)


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------

def search_url(page, price_from, price_to):
    params = [("dealer_segment", "1"), ("dealer_segment", "2")]
    if price_from is not None:
        params.append(("price_from", str(price_from)))
    if price_to is not None:
        params.append(("price_to", str(price_to)))
    if page > 1:
        params.append(("page", str(page)))
    qs = "&".join(f"{k}={v}" for k, v in params)
    return f"{SEARCH_URL}?{qs}"


# ---------------------------------------------------------------------------
# Phase 1: collect (finnkode, org_name) from search result pages
# ---------------------------------------------------------------------------

def collect_listings(price_from, price_to) -> list[tuple[str, str]]:
    """
    Sample 10% of listings for this price bucket from randomly chosen pages.
    Page 1 is always fetched first to get the total count; remaining pages
    are drawn at random from the first MAX_PAGES pages (newest listings).
    """
    label = bucket_label(price_from, price_to)

    # --- Page 1: get total count and first batch of articles ---
    url = search_url(1, price_from, price_to)
    log(f"  [SEARCH] {url}")
    try:
        r = _session.get(url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log(f"  [ERR] {label} page=1 -> {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    articles = soup.find_all("article")
    if not articles:
        log(f"  [STOP] {label} no articles on page 1")
        return []

    total = _parse_total_count(soup)
    page_size = len(articles)

    if total is None:
        log(f"  [WARN] {label} could not parse total count, using page 1 only")
        total = page_size

    total_pages = min(math.ceil(total / page_size), MAX_PAGES)
    target = math.ceil(total * SAMPLE_FRACTION)
    pages_needed = math.ceil(target / page_size)

    max_reachable = total_pages * page_size
    if target > max_reachable:
        log(f"  [WARN] {label} target={target} exceeds ~{max_reachable} listings "
            f"reachable within the first {total_pages} pages; sample will be capped")

    if pages_needed >= total_pages:
        selected_pages = list(range(1, total_pages + 1))
    else:
        # Randomly pick remaining pages across the full range (excluding page 1)
        extra = random.sample(range(2, total_pages + 1), pages_needed - 1)
        selected_pages = sorted([1] + extra)

    log(f"  [SAMPLE] {label} total={total} target={target} "
        f"pages={len(selected_pages)}/{total_pages} selected={selected_pages}")

    listings: list[tuple[str, str]] = []
    seen_fk: set[str] = set()

    for page in selected_pages:
        page_soup = soup if page == 1 else _fetch_page(label, page, price_from, price_to)
        if page_soup is None:
            continue

        new = 0
        for art in page_soup.find_all("article"):
            fk, org = _parse_article(art)
            if fk and fk not in seen_fk:
                seen_fk.add(fk)
                listings.append((fk, org))
                new += 1

        log(f"  [PAGE] {label} page={page} new={new} total={len(listings)}")

    if len(listings) < target:
        log(f"  [WARN] {label} collected {len(listings)} of target={target} "
            f"(page errors, duplicates, or page cap)")

    # Trim to target by random subsampling to remove within-page ordering bias
    if len(listings) > target:
        collected = len(listings)
        listings = random.sample(listings, target)
        log(f"  [TRIM] {label} sampled {target} from {collected}")

    return listings


def _fetch_page(label, page, price_from, price_to):
    url = search_url(page, price_from, price_to)
    log(f"  [SEARCH] {url}")
    try:
        r = _session.get(url, timeout=30)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log(f"  [ERR] {label} page={page} -> {e}")
        return None


def _parse_total_count(soup) -> int | None:
    """Extract the total listing count shown as 'X treff' on the search page."""
    for tag in soup.find_all(["h1", "h2", "span", "p", "div"]):
        text = tag.get_text(" ", strip=True)
        m = re.search(r"([\d\s\xa0]+)\s*treff", text)
        if m:
            try:
                return int(re.sub(r"\s", "", m.group(1)))
            except ValueError:
                pass
    return None


def _parse_article(article) -> tuple[str | None, str]:
    """Extract (finnkode, org_name) from a search result <article>."""
    link = article.select_one('a[href*="/mobility/item/"]')
    if not link:
        return None, ""

    # Finnkode from <a id="461145507"> or from the href
    finnkode = link.get("id") or ""
    if not finnkode:
        m = re.search(r"/mobility/item/(\d+)", link.get("href", ""))
        finnkode = m.group(1) if m else ""

    if not finnkode:
        return None, ""

    # Org name: first "location ∙ DealerName" span, take the part after ∙
    org = ""
    for span in article.select("span.truncate"):
        text = span.get_text(" ", strip=True)
        if "∙" in text:
            parts = text.split("∙", 1)
            if len(parts) == 2:
                candidate = parts[1].strip()
                # Ignore lines that look like feature lists ("Forhandler ∙ Service")
                if not any(kw in candidate.lower() for kw in
                           ["forhandler", "service", "garanti", "merkeforhandler"]):
                    org = candidate
                    break

    return finnkode, org


# ---------------------------------------------------------------------------
# Phase 2: classify a dealer by visiting one individual ad page
# ---------------------------------------------------------------------------

def classify_dealer(finnkode: str, org_name: str) -> str | None:
    """
    Fetch the individual ad page and determine package:
      premium  = logo img present AND "type":"inventory" in static HTML
      pluss    = logo img present AND no inventory podlet
      basis    = no logo img in seller section
    Result is cached by org_name so each dealer is fetched only once.
    Returns None on fetch errors so throttling doesn't inflate basis counts.
    """
    if org_name and org_name in _dealer_cache:
        return _dealer_cache[org_name]

    url = AD_URL.format(finnkode)
    log(f"    [AD] {url}  ({org_name or 'unknown'})")
    try:
        r = _session.get(url, timeout=30)
        r.raise_for_status()
        raw_html = r.text
        soup = BeautifulSoup(raw_html, "html.parser")
        has_logo = _seller_has_logo(soup)
        if has_logo:
            has_inventory = '"type":"inventory"' in raw_html
            package = "premium" if has_inventory else "pluss"
        else:
            package = "basis"
    except Exception as e:
        log(f"    [ERR] ad fetch -> {e}")
        return None  # don't cache errors, exclude from results

    if org_name:
        _dealer_cache[org_name] = package
    return package


def _seller_has_logo(soup: BeautifulSoup) -> bool:
    """
    Pluss and Premium dealers have their company logo hosted on dealerhub.cdn-vend.com.
    Basis dealers have no such image — only a bold text company name.
    Confirmed from live pages: both Pluss and Premium have dealerhub logos;
    Basis does not.
    """
    for img in soup.find_all("img"):
        src = (img.get("src") or "").lower()
        if "dealerhub.cdn-vend.com" in src:
            return True
        # Some dealers may use finn's own profile CDN (exclude the NBF badge)
        if "mobility-company-profile" in src and "nbf" not in src:
            return True
    return False


# ---------------------------------------------------------------------------
# Bucket scraper (combines both phases)
# ---------------------------------------------------------------------------

def scrape_bucket(price_from, price_to) -> tuple[dict, list[dict]]:
    label = bucket_label(price_from, price_to)
    counts = {"premium": 0, "pluss": 0, "basis": 0}
    debug_rows = []

    # Phase 1: collect listings from search pages
    listings = collect_listings(price_from, price_to)
    if not listings:
        return counts, debug_rows

    log(f"  Classifying {len(listings)} listings across "
        f"{len({o for _, o in listings})} unique dealers...")

    # Phase 2: classify each listing via dealer cache
    for finnkode, org_name in listings:
        package = classify_dealer(finnkode, org_name)
        if package is None:
            continue
        counts[package] += 1
        debug_rows.append({
            "price_bracket": label,
            "finnkode": finnkode,
            "org_name": org_name,
            "package": package,
        })

    log(
        f"  [{label}] Premium={counts['premium']} "
        f"Pluss={counts['pluss']} Basis={counts['basis']}"
    )
    return counts, debug_rows


# ---------------------------------------------------------------------------
# Price buckets
# ---------------------------------------------------------------------------

def price_buckets(step=10_000, upper=1_000_000):
    intervals = []
    start = 0
    while start < upper:
        end = start + step
        p_from = 0 if start == 0 else start + 1
        intervals.append((p_from, end))
        start = end
    intervals.append((upper + 1, None))
    return intervals


def bucket_label(p_from, p_to):
    return f"{p_from}-plus" if p_to is None else f"{p_from}-{p_to}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    run_dt = datetime.now()
    today_str = run_dt.date().isoformat()
    ts = run_dt.strftime("%Y-%m-%d_%H-%M-%S")
    os.makedirs(RUNS_DIR, exist_ok=True)
    output_csv = os.path.join(RUNS_DIR, f"finn_mobility_packages_summary_{ts}.csv")
    debug_csv = os.path.join(RUNS_DIR, f"finn_mobility_debug_{ts}.csv")

    buckets = price_buckets()
    log(f"Starting scrape: {len(buckets)} buckets (10k NOK steps, cap=1M)")
    log(f"Strategy: collect finnkodes from search pages, "
        f"classify dealers via individual ad pages (cached per dealer)")

    summary_rows = []
    all_debug: list[dict] = []

    for idx, (p_from, p_to) in enumerate(buckets, start=1):
        label = bucket_label(p_from, p_to)
        log(f"\n=== BUCKET {idx}/{len(buckets)}: {label} "
            f"(dealer cache size: {len(_dealer_cache)}) ===")

        counts, debug_rows = scrape_bucket(p_from, p_to)
        all_debug.extend(debug_rows)

        summary_rows.append({
            "date_collected": today_str,
            "price_bracket": label,
            "premium_count": counts["premium"],
            "pluss_count": counts["pluss"],
            "basis_count": counts["basis"],
            "total_count": sum(counts.values()),
        })

    df = pd.DataFrame(summary_rows)
    df.to_csv(output_csv, index=False, encoding="utf-8-sig")
    df.to_csv("finn_mobility_packages_summary.csv", index=False, encoding="utf-8-sig")
    log(f"\n[CSV] Summary -> {output_csv}  ({len(df)} rows)")
    log(df.to_string())

    if all_debug:
        pd.DataFrame(all_debug).to_csv(debug_csv, index=False, encoding="utf-8-sig")
        log(f"[CSV] Debug   -> {debug_csv}  ({len(all_debug)} rows)")

    _write_to_bigquery(run_dt, df)
    log(f"\nDealer cache final size: {len(_dealer_cache)}")


def _write_to_bigquery(run_dt: datetime, df: pd.DataFrame):
    total_premium = int(df["premium_count"].sum())
    total_pluss = int(df["pluss_count"].sum())
    total_basis = int(df["basis_count"].sum())
    total = total_premium + total_pluss + total_basis

    client = bigquery.Client(project=BQ_PROJECT)
    table_ref = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"

    client.create_table(
        bigquery.Table(table_ref, schema=[
            bigquery.SchemaField("run_timestamp", "TIMESTAMP"),
            bigquery.SchemaField("site", "STRING"),
            bigquery.SchemaField("premium_count", "INTEGER"),
            bigquery.SchemaField("pluss_count", "INTEGER"),
            bigquery.SchemaField("basis_count", "INTEGER"),
            bigquery.SchemaField("total_count", "INTEGER"),
        ]),
        exists_ok=True,
    )

    row = [{
        "run_timestamp": run_dt.astimezone(timezone.utc).isoformat(),
        "site": "finn",
        "premium_count": total_premium,
        "pluss_count": total_pluss,
        "basis_count": total_basis,
        "total_count": total,
    }]
    errors = client.insert_rows_json(table_ref, row)
    if errors:
        log(f"[BQ] Insert errors: {errors}")
    else:
        log(f"[BQ] Appended 1 row to {table_ref} "
            f"(Premium={total_premium} Pluss={total_pluss} Basis={total_basis} Total={total})")


def test_run(n=50):
    """Fetch the first page of results (no price filter) and classify n listings."""
    log(f"=== TEST RUN: classifying first {n} listings ===")
    url = f"{SEARCH_URL}?dealer_segment=1&dealer_segment=2"
    log(f"[SEARCH] {url}")
    r = _session.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    listings = []
    seen = set()
    for art in soup.find_all("article"):
        fk, org = _parse_article(art)
        if fk and fk not in seen:
            seen.add(fk)
            listings.append((fk, org))
        if len(listings) >= n:
            break

    log(f"Found {len(listings)} listings on page 1\n")

    counts = {"premium": 0, "pluss": 0, "basis": 0}
    for fk, org in listings:
        pkg = classify_dealer(fk, org)
        if pkg is None:
            continue
        counts[pkg] += 1
        log(f"  {fk:>12}  {pkg:<8}  {org}")

    log(f"\nResult: Premium={counts['premium']}  Pluss={counts['pluss']}  Basis={counts['basis']}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        test_run(n)
    else:
        main()
