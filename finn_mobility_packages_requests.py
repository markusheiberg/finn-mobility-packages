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

        time.sleep(0.3)

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
      premium  = logo img present  AND  "se annonsen på selgerens side"
      pluss    = logo img present  AND  no "se annonsen"
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
    page_text = soup.get_text(" ", strip=True).lower()

    # Signal 1: "Se annonsen på selgerens side" link → Premium
    has_seller_page_link = "selgerens side" in page_text

    # Signal 2: logo img in the seller/contact info section
    has_logo = _seller_has_logo(soup)

    if has_seller_page_link:
        package = "premium"
    elif has_logo:
        package = "pluss"
    else:
        package = "basis"

    if org_name:
        _dealer_cache[org_name] = package

    time.sleep(0.2)
    return package


def _seller_has_logo(soup: BeautifulSoup) -> bool:
    """
    Return True if the seller/dealer info box on the ad page contains a logo image.

    Premium and Pluss dealers show their company logo (Toyota, SULLAND, etc.)
    in the seller info card. Basis dealers show only bold text (no img).
    """
    # Try specific seller-section selectors first
    seller_selectors = [
        '[data-testid*="seller"]',
        '[data-testid*="contact"]',
        '[class*="seller-info"]',
        '[class*="sellerInfo"]',
        '[class*="contact-info"]',
        '[class*="dealer-info"]',
    ]
    for sel in seller_selectors:
        section = soup.select_one(sel)
        if section:
            return _has_non_icon_img(section)

    # Fallback: find the card that contains "Skriv til selger" or "org.nr"
    # and check for imgs nearby
    for section in soup.select("section, aside, div"):
        text = section.get_text(" ", strip=True).lower()
        if "org.nr" in text or "skriv til selger" in text or "brreg" in text:
            if _has_non_icon_img(section):
                return True

    return False


def _has_non_icon_img(element) -> bool:
    """True if element contains an <img> that isn't a tiny icon or NXBF badge."""
    for img in element.find_all("img"):
        src = (img.get("src") or "").lower()
        alt = (img.get("alt") or "").lower()

        # Skip NXBF / Norges Bilbransjeforbund badge
        if "nxbf" in src or "nxbf" in alt or "bilbransje" in alt:
            continue
        # Skip map thumbnails
        if "staticmap" in src or "maptile" in src:
            continue
        # Skip tiny icons by size attribute
        try:
            w = int(img.get("width") or 0)
            h = int(img.get("height") or 0)
            if (w and w < 40) or (h and h < 40):
                continue
        except (ValueError, TypeError):
            pass

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

        time.sleep(0.5)

    df = pd.DataFrame(summary_rows)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    log(f"\n[CSV] Summary -> {OUTPUT_CSV}  ({len(df)} rows)")
    log(df.to_string())

    if all_debug:
        pd.DataFrame(all_debug).to_csv(DEBUG_CSV, index=False, encoding="utf-8-sig")
        log(f"[CSV] Debug   -> {DEBUG_CSV}  ({len(all_debug)} rows)")

    log(f"\nDealer cache final size: {len(_dealer_cache)}")


if __name__ == "__main__":
    main()
