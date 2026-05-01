import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import date

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

MAX_PAGES_PER_BUCKET = 200
OUTPUT_CSV = "finn_mobility_packages_summary.csv"
DEBUG_CSV = "finn_mobility_debug.csv"

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
    Scrape all search pages for this price bucket.
    Returns list of (finnkode, org_name).
    """
    listings: list[tuple[str, str]] = []
    seen_fk: set[str] = set()
    label = bucket_label(price_from, price_to)

    for page in range(1, MAX_PAGES_PER_BUCKET + 1):
        url = search_url(page, price_from, price_to)
        log(f"  [SEARCH] {url}")
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception as e:
            log(f"  [ERR] {label} page={page} -> {e}")
            break

        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.find_all("article")
        if not articles:
            log(f"  [STOP] {label} page={page} no articles")
            break

        new = 0
        for art in articles:
            fk, org = _parse_article(art)
            if not fk or fk in seen_fk:
                continue
            seen_fk.add(fk)
            listings.append((fk, org))
            new += 1

        log(f"  [PAGE] {label} page={page} new={new} total={len(listings)}")

        if new == 0:
            log(f"  [STOP] {label} page={page} no new listings")
            break
        if new < 5 and page > 3:
            log(f"  [STOP] {label} page={page} low activity")
            break

        time.sleep(0.05)

    return listings


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

def classify_dealer(finnkode: str, org_name: str) -> str:
    """
    Fetch the individual ad page and determine package:
      premium  = logo img present  AND  own inventory via recommendations API
      pluss    = logo img present  AND  no own inventory
      basis    = no logo img in seller section
    Result is cached by org_name so each dealer is fetched only once.
    """
    global _dealer_cache

    if org_name in _dealer_cache:
        return _dealer_cache[org_name]

    url = AD_URL.format(finnkode)
    log(f"    [AD] {url}  ({org_name or 'unknown'})")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log(f"    [ERR] ad fetch -> {e}")
        package = "basis"
        if org_name:
            _dealer_cache[org_name] = package
        return package

    soup = BeautifulSoup(r.text, "html.parser")

    # Signal 1: dealerhub logo img → Pluss or Premium (not Basis)
    has_logo = _seller_has_logo(soup)

    if has_logo:
        # Signal 2: Premium dealers have their own inventory at the bottom.
        # The Aurora recommendations podlet fetches this via an API endpoint.
        # We call it directly; a non-empty response means Premium.
        has_inventory = _check_inventory_api(finnkode)
        package = "premium" if has_inventory else "pluss"
    else:
        package = "basis"

    if org_name:
        _dealer_cache[org_name] = package

    time.sleep(0.05)
    return package


def _check_inventory_api(finnkode: str) -> bool:
    """
    Call the Aurora recommendations proxy to check if this dealer has own inventory.
    Premium dealers return items; Pluss dealers return empty/no results.
    The endpoint is the same one the JS podlet uses client-side.
    """
    url = "https://www.finn.no/mobility/item/podium-resource/recommendations"
    headers = {
        **HEADERS,
        "Referer": f"https://www.finn.no/mobility/item/{finnkode}",
        "Origin": "https://www.finn.no",
        "Accept": "application/json, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    params = {"adId": finnkode, "type": "inventory"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            log(f"      [INV-API] {r.status_code} for {finnkode}")
            return False
        data = r.json()
        # Response shape varies; check common envelope keys first
        for key in ("items", "results", "ads", "data", "documents"):
            val = data.get(key)
            if val:
                return True
        # Top-level list
        if isinstance(data, list) and data:
            return True
    except Exception as e:
        log(f"      [INV-API-ERR] {e}")
    return False


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
    today_str = date.today().isoformat()
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

        time.sleep(0.1)

    df = pd.DataFrame(summary_rows)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    log(f"\n[CSV] Summary -> {OUTPUT_CSV}  ({len(df)} rows)")
    log(df.to_string())

    if all_debug:
        pd.DataFrame(all_debug).to_csv(DEBUG_CSV, index=False, encoding="utf-8-sig")
        log(f"[CSV] Debug   -> {DEBUG_CSV}  ({len(all_debug)} rows)")

    log(f"\nDealer cache final size: {len(_dealer_cache)}")


def test_run(n=50):
    """Fetch the first page of results (no price filter) and classify n listings."""
    log(f"=== TEST RUN: classifying first {n} listings ===")
    url = f"{SEARCH_URL}?dealer_segment=1&dealer_segment=2"
    log(f"[SEARCH] {url}")
    r = requests.get(url, headers=HEADERS, timeout=30)
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
