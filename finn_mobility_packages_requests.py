import base64
import json
import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import date

SEARCH_PAGE_URL = "https://www.finn.no/mobility/search/car"
SEARCH_API_URL = "https://www.finn.no/mobility/search/api"
SEARCH_KEY = "SEARCH_ID_CAR_USED"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nb-NO,nb;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "application/json, text/html, */*",
}

MAX_PAGES_PER_BUCKET = 200
OUTPUT_CSV = "finn_mobility_packages_summary.csv"
DEBUG_CSV = "finn_mobility_debug.csv"


def log(*args):
    print(*args, flush=True)


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------

def _common_params(price_from, price_to, page):
    params = [("dealer_segment", "1"), ("dealer_segment", "2")]
    if price_from is not None:
        params.append(("price_from", str(price_from)))
    if price_to is not None:
        params.append(("price_to", str(price_to)))
    if page > 1:
        params.append(("page", str(page)))
    return params


def html_url(page, price_from, price_to):
    params = _common_params(price_from, price_to, page)
    qs = "&".join(f"{k}={v}" for k, v in params)
    return f"{SEARCH_PAGE_URL}?{qs}"


def api_url(page, price_from, price_to):
    params = [("searchKey", SEARCH_KEY)] + _common_params(price_from, price_to, page)
    qs = "&".join(f"{k}={v}" for k, v in params)
    return f"{SEARCH_API_URL}?{qs}"


# ---------------------------------------------------------------------------
# Primary path: JSON API
# ---------------------------------------------------------------------------

def fetch_api(page, price_from, price_to):
    """
    Try the JSON search API. Returns list of result dicts, or None on failure.
    Each dict has keys: finnkode, is_featured, has_logo, img_count, source.
    """
    url = api_url(page, price_from, price_to)
    log(f"[API] {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    # finn.no API may return docs at different paths
    docs = (
        data.get("docs")
        or data.get("results")
        or data.get("ads")
        or []
    )
    if not docs:
        return None

    rows = []
    for doc in docs:
        rows.append(_classify_api_doc(doc))
    return rows


def _classify_api_doc(doc):
    ad = doc.get("searchEntry") or doc
    finnkode = str(
        ad.get("id") or ad.get("ad_id") or ad.get("adId") or ad.get("finnkode") or ""
    )

    # Premium: labels list contains a "promotion" label
    labels = ad.get("labels") or doc.get("labels") or []
    label_texts = " ".join(
        (lbl.get("text") if isinstance(lbl, dict) else str(lbl)).lower()
        for lbl in labels
    )
    is_featured = "betalt plassering" in label_texts or "promotion" in label_texts

    # Pluss: organisation has a logo url (field varies)
    logo = (
        ad.get("logo_url") or ad.get("logoUrl")
        or ad.get("dealer_logo") or ad.get("dealerLogo")
        or (ad.get("dealer") or {}).get("logo")
        or (ad.get("organisation") or {}).get("logo")
        or ""
    )
    has_logo = bool(logo)

    # Image count
    images = ad.get("images") or ad.get("image_urls") or []
    img_count = len(images) if isinstance(images, list) else (1 if ad.get("image") else 0)

    package = _package(is_featured, has_logo, img_count)

    return {
        "finnkode": finnkode,
        "package": package,
        "is_featured": is_featured,
        "has_logo": has_logo,
        "img_count": img_count,
        "logo_url": str(logo)[:80],
        "source": "api",
    }


# ---------------------------------------------------------------------------
# Fallback path: HTML parsing via React Query dehydrated state + article tags
# ---------------------------------------------------------------------------

def fetch_html(page, price_from, price_to):
    """
    Fetch the HTML page, extract listing data via:
    1. Dehydrated React Query state (base64 blobs)
    2. <article> tag parsing
    Returns list of result dicts, or None if nothing found.
    """
    url = html_url(page, price_from, price_to)
    log(f"[HTML] {url}")
    r = requests.get(url, headers={**HEADERS, "Accept": "text/html"}, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Try dehydrated React Query state first (richest data)
    rows = _extract_dehydrated(soup)
    if rows:
        return rows

    # Fall back to article tag parsing
    return _extract_articles(soup) or None


def _extract_dehydrated(soup):
    """
    Decode base64 script blobs and look for React Query dehydrated state
    containing search results with labels.
    """
    rows = []
    for script in soup.find_all("script", src=False):
        txt = (script.string or "").strip()
        if not txt.startswith("ey"):
            continue
        try:
            decoded = base64.b64decode(txt + "==").decode("utf-8")
            data = json.loads(decoded)
        except Exception:
            continue

        # Look for queries array (React Query dehydrated state)
        queries = data.get("queries")
        if not isinstance(queries, list):
            continue

        for q in queries:
            results = _dig(q, "state", "data", "results")
            if not isinstance(results, list) or not results:
                continue
            for item in results:
                row = _classify_dehydrated_item(item)
                if row and row["finnkode"]:
                    rows.append(row)

    return rows if rows else None


def _classify_dehydrated_item(item):
    ad = item.get("searchEntry") or item
    finnkode = str(
        ad.get("id") or ad.get("ad_id") or ad.get("finnkode") or ""
    )

    labels = ad.get("labels") or item.get("labels") or []
    label_texts = " ".join(
        (lbl.get("text") if isinstance(lbl, dict) else str(lbl)).lower()
        for lbl in labels
    )
    is_featured = "betalt plassering" in label_texts

    logo = (
        ad.get("logo_url") or ad.get("logoUrl") or ad.get("dealer_logo") or ""
    )
    has_logo = bool(logo)

    images = ad.get("images") or []
    img_count = len(images) if isinstance(images, list) else (1 if ad.get("image") else 0)

    package = _package(is_featured, has_logo, img_count)

    return {
        "finnkode": finnkode,
        "package": package,
        "is_featured": is_featured,
        "has_logo": has_logo,
        "img_count": img_count,
        "logo_url": str(logo)[:80],
        "source": "dehydrated",
    }


def _extract_articles(soup):
    """Parse <article> tags directly from HTML."""
    rows = []
    for article in soup.find_all("article"):
        row = _classify_article(article)
        if row and row["finnkode"]:
            rows.append(row)
    return rows if rows else None


def _classify_article(article):
    # Finnkode: <a id="461145507" href="/mobility/item/461145507">
    link = article.select_one('a[href*="/mobility/item/"]')
    finnkode = None
    if link:
        finnkode = link.get("id") or None
        if not finnkode:
            m = re.search(r"/mobility/item/(\d+)", link.get("href", ""))
            finnkode = m.group(1) if m else None

    if not finnkode:
        return None

    card_text = article.get_text(" ", strip=True).lower()

    # Premium: "Betalt plassering" text anywhere in the card
    is_featured = "betalt plassering" in card_text

    # Pluss: dealer logo img in the bottom-right logo slot
    # <span class="self-end justify-self-end pl-16"> contains an <img>
    logo_slot = article.select_one(
        'span[class*="self-end"][class*="justify-self-end"], '
        'span[class*="justify-self-end"]'
    )
    has_logo = bool(logo_slot and logo_slot.find("img"))

    # Car image count (the main listing photo)
    car_imgs = article.select('div[class*="aspect"] img')
    img_count = len(car_imgs)

    package = _package(is_featured, has_logo, img_count)

    return {
        "finnkode": finnkode,
        "package": package,
        "is_featured": is_featured,
        "has_logo": has_logo,
        "img_count": img_count,
        "logo_url": "",
        "source": "html",
    }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _package(is_featured, has_logo, img_count):
    if is_featured:
        return "premium"
    if has_logo:
        return "pluss"
    return "basis"


def _dig(obj, *keys):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


# ---------------------------------------------------------------------------
# Bucket scraper
# ---------------------------------------------------------------------------

def scrape_bucket(price_from, price_to):
    counts = {"premium": 0, "pluss": 0, "basis": 0}
    seen = set()
    debug_rows = []
    label = bucket_label(price_from, price_to)

    for page in range(1, MAX_PAGES_PER_BUCKET + 1):
        # Try JSON API first, fall back to HTML
        rows = fetch_api(page, price_from, price_to)
        if not rows:
            try:
                rows = fetch_html(page, price_from, price_to)
            except Exception as e:
                log(f"[ERR] {label} page={page} -> {e}")
                break

        if not rows:
            log(f"[STOP] {label} page={page} no listings found")
            break

        new = 0
        for row in rows:
            fk = row.get("finnkode")
            if not fk or fk in seen:
                continue
            seen.add(fk)
            counts[row["package"]] += 1
            new += 1
            debug_rows.append({"price_bracket": label, "page": page, **row})

        log(
            f"[PAGE] {label} page={page} src={rows[0]['source']} new={new} "
            f"total=(Premium={counts['premium']}, Pluss={counts['pluss']}, Basis={counts['basis']})"
        )

        if new == 0:
            log(f"[STOP] {label} page={page} no new listings")
            break
        if new < 5 and page > 3:
            log(f"[STOP] {label} page={page} low activity")
            break

        time.sleep(0.3)

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

    summary_rows = []
    all_debug = []

    for idx, (p_from, p_to) in enumerate(buckets, start=1):
        label = bucket_label(p_from, p_to)
        log(f"\n=== BUCKET {idx}/{len(buckets)}: {label} ===")

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


if __name__ == "__main__":
    main()
