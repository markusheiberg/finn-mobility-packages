import json
import re
import sys
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import date

BASE_URL = "https://www.finn.no/mobility/search/car"
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


def log(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def build_url(page: int, price_from: int | None, price_to: int | None) -> str:
    params = ["dealer_segment=1", "dealer_segment=2"]
    if price_from is not None:
        params.append(f"price_from={price_from}")
    if price_to is not None:
        params.append(f"price_to={price_to}")
    if page > 1:
        params.append(f"page={page}")
    return f"{BASE_URL}?{'&'.join(params)}"


def get_page(page: int, price_from: int | None, price_to: int | None) -> tuple[BeautifulSoup, str]:
    url = build_url(page, price_from, price_to)
    log(f"[GET] {url}")
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), url


# ---------------------------------------------------------------------------
# Primary path: extract listings from __NEXT_DATA__ JSON
# ---------------------------------------------------------------------------

def extract_next_data(soup: BeautifulSoup) -> dict | None:
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag:
        return None
    try:
        return json.loads(tag.string)
    except (json.JSONDecodeError, TypeError):
        return None


def dig(obj, *keys):
    """Safely traverse nested dicts/lists."""
    for k in keys:
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(k)
        elif isinstance(obj, list) and isinstance(k, int):
            obj = obj[k] if k < len(obj) else None
        else:
            return None
    return obj


def find_docs(data: dict) -> list[dict]:
    """
    Try known paths for the listing array inside __NEXT_DATA__.
    finn.no tends to nest under props -> pageProps -> initialProps / data / search.
    """
    candidates = [
        dig(data, "props", "pageProps", "initialProps", "searchResult", "docs"),
        dig(data, "props", "pageProps", "data", "searchResult", "docs"),
        dig(data, "props", "pageProps", "searchResult", "docs"),
        dig(data, "props", "pageProps", "data", "docs"),
        dig(data, "props", "pageProps", "results"),
        dig(data, "props", "pageProps", "ads"),
    ]
    for c in candidates:
        if isinstance(c, list) and c:
            return c

    # deep search: walk the whole tree looking for a list that contains dicts
    # with a "finnkode" or "id" key that looks like an ad
    return _deep_find_docs(data)


def _deep_find_docs(obj, depth=0) -> list[dict]:
    if depth > 8:
        return []
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        keys = set(obj[0].keys())
        if keys & {"finnkode", "ad_id", "adId", "id"}:
            return obj
    if isinstance(obj, dict):
        for v in obj.values():
            result = _deep_find_docs(v, depth + 1)
            if result:
                return result
    if isinstance(obj, list):
        for item in obj:
            result = _deep_find_docs(item, depth + 1)
            if result:
                return result
    return []


FEATURED_KEYWORDS = {"topp", "premium", "featured", "highlight", "fremhevet", "sponsored"}


def classify_doc(doc: dict) -> tuple[str, str, dict]:
    """
    Classify a listing doc from __NEXT_DATA__ JSON.
    Returns (finnkode, package, debug_dict).
    """
    finnkode = str(
        doc.get("finnkode")
        or doc.get("ad_id")
        or doc.get("adId")
        or doc.get("id")
        or ""
    )

    # --- featured / premium signal ---
    flags = doc.get("flags", []) or []
    labels = doc.get("labels", []) or []
    ad_type = str(doc.get("ad_type") or doc.get("adType") or "").lower()
    highlights = doc.get("highlights", []) or []

    flag_text = " ".join(str(f).lower() for f in flags + labels + highlights)
    is_featured = (
        bool(FEATURED_KEYWORDS & set(flag_text.split()))
        or any(kw in flag_text for kw in FEATURED_KEYWORDS)
        or any(kw in ad_type for kw in FEATURED_KEYWORDS)
    )

    # --- dealer logo signal ---
    dealer = doc.get("dealer") or doc.get("company") or doc.get("organisation") or {}
    logo_url = (
        dealer.get("logo")
        or dealer.get("logo_url")
        or dealer.get("logoUrl")
        or dealer.get("image")
        or doc.get("dealer_logo")
        or doc.get("dealerLogo")
        or ""
    )
    has_logo = bool(logo_url)

    # --- image count signal ---
    images = (
        doc.get("images")
        or doc.get("image_urls")
        or doc.get("imageUrls")
        or doc.get("media")
        or []
    )
    if isinstance(images, dict):
        images = list(images.values())
    img_count = len(images) if isinstance(images, list) else 0

    # --- classify ---
    if is_featured:
        package = "premium"
    elif has_logo or img_count >= 2:
        package = "pluss"
    else:
        package = "basis"

    debug = {
        "finnkode": finnkode,
        "package": package,
        "is_featured": is_featured,
        "has_logo": has_logo,
        "logo_url": str(logo_url)[:80],
        "img_count": img_count,
        "flags": str(flags)[:80],
        "ad_type": ad_type[:40],
    }
    return finnkode, package, debug


# ---------------------------------------------------------------------------
# Fallback path: parse HTML cards
# ---------------------------------------------------------------------------

def classify_html_card(card) -> tuple[str | None, str, dict]:
    link = card.select_one('a[href*="/mobility/"], a[href*="finnkode="]')
    href = link.get("href") if link else None
    finnkode = _extract_finnkode(href)

    card_classes = " ".join(card.get("class") or []).lower()
    card_testid = card.get("data-testid", "").lower()

    is_featured = False
    for sel in ['[class*="badge"]', '[class*="featured"]', '[class*="premium"]',
                '[data-testid*="badge"]', '[data-testid*="featured"]', "mark"]:
        el = card.select_one(sel)
        if el and any(kw in el.get_text(" ", strip=True).lower() for kw in FEATURED_KEYWORDS):
            is_featured = True
            break
    if not is_featured:
        if any(kw in card_classes + card_testid for kw in FEATURED_KEYWORDS):
            is_featured = True

    has_logo = False
    for sel in ['[data-testid*="logo"]', '[class*="dealer-logo"]', '[class*="dealerLogo"]',
                '[class*="brand-logo"]', '[class*="brandLogo"]']:
        if card.select_one(sel):
            has_logo = True
            break
    if not has_logo:
        for img in card.select("img"):
            combined = (img.get("src") or "") + (img.get("alt") or "") + " ".join(img.get("class") or [])
            if any(kw in combined.lower() for kw in ["logo", "dealer", "brand"]):
                has_logo = True
                break

    SKIP = {"logo", "dealer", "brand", "icon", "avatar"}
    seen: set[str] = set()
    img_count = 0
    for img in card.select("img"):
        src = (img.get("src") or "").strip()
        if not src or src in seen:
            continue
        combined = (img.get("alt") or "") + " ".join(img.get("class") or [])
        if any(kw in combined.lower() for kw in SKIP):
            continue
        try:
            w, h = int(img.get("width") or 0), int(img.get("height") or 0)
            if (w and w < 40) or (h and h < 40):
                continue
        except (ValueError, TypeError):
            pass
        seen.add(src)
        img_count += 1

    if is_featured:
        package = "premium"
    elif has_logo or img_count >= 2:
        package = "pluss"
    else:
        package = "basis"

    debug = {
        "finnkode": finnkode,
        "package": package,
        "is_featured": is_featured,
        "has_logo": has_logo,
        "img_count": img_count,
        "card_classes": card_classes[:120],
        "card_testid": card_testid[:80],
    }
    return finnkode, package, debug


def get_html_cards(soup: BeautifulSoup) -> list:
    AD_PATTERNS = [
        'a[href*="/mobility/car/ad"]',
        'a[href*="/mobility/"]',
        'a[href*="finnkode="]',
    ]
    for container_sel in ["article", '[data-testid*="ad-list-item"]',
                           '[data-testid*="search-result"]', 'li[class*="result"]']:
        candidates = soup.select(container_sel)
        if not candidates:
            continue
        for pattern in AD_PATTERNS:
            cards = [c for c in candidates if c.select_one(pattern)]
            if cards:
                return cards
    return []


def _extract_finnkode(href: str | None) -> str | None:
    if not href:
        return None
    m = re.search(r"finnkode=(\d+)", href)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{7,})", href)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

def has_next_page(soup: BeautifulSoup, data: dict | None) -> bool:
    """Return True if there is a next page of results."""
    if data:
        metadata = (
            dig(data, "props", "pageProps", "initialProps", "searchResult", "metadata")
            or dig(data, "props", "pageProps", "data", "searchResult", "metadata")
            or dig(data, "props", "pageProps", "searchResult", "metadata")
            or {}
        )
        if isinstance(metadata, dict):
            current = metadata.get("current_page") or metadata.get("page") or 0
            last = metadata.get("last_page") or metadata.get("totalPages") or 0
            if current and last:
                return int(current) < int(last)

    # HTML fallback: look for a "next" pagination link
    next_link = soup.select_one(
        'a[aria-label*="next" i], a[rel="next"], [data-testid*="next-page"]'
    )
    return next_link is not None


# ---------------------------------------------------------------------------
# Bucket scraper
# ---------------------------------------------------------------------------

def scrape_bucket(
    price_from: int | None, price_to: int | None
) -> tuple[dict, list[dict]]:
    counts = {"premium": 0, "pluss": 0, "basis": 0}
    seen: set[str] = set()
    debug_rows: list[dict] = []
    label = bucket_label(price_from, price_to)

    for page in range(1, MAX_PAGES_PER_BUCKET + 1):
        try:
            soup, url = get_page(page, price_from, price_to)
        except Exception as e:
            log(f"[ERR] {label} page={page} -> {e}")
            break

        data = extract_next_data(soup)

        rows_this_page: list[tuple[str, str, dict]] = []

        if data:
            docs = find_docs(data)
            if docs:
                for doc in docs:
                    fk, pkg, dbg = classify_doc(doc)
                    rows_this_page.append((fk, pkg, {**dbg, "source": "json"}))
            else:
                log(f"[WARN] {label} page={page} __NEXT_DATA__ found but no docs; falling back to HTML")

        if not rows_this_page:
            cards = get_html_cards(soup)
            if not cards:
                log(f"[STOP] {label} page={page} no cards found (HTML or JSON)")
                break
            for card in cards:
                fk, pkg, dbg = classify_html_card(card)
                rows_this_page.append((fk, pkg, {**dbg, "source": "html"}))

        new_rows = 0
        for fk, pkg, dbg in rows_this_page:
            if not fk or fk in seen:
                continue
            seen.add(fk)
            counts[pkg] += 1
            new_rows += 1
            debug_rows.append({"price_bracket": label, "page": page, **dbg})

        log(
            f"[PAGE] {label} page={page} new={new_rows} "
            f"total=(Premium={counts['premium']}, Pluss={counts['pluss']}, Basis={counts['basis']})"
        )

        if new_rows == 0:
            log(f"[STOP] {label} page={page} no new listings")
            break

        if new_rows < 5 and page > 3:
            log(f"[STOP] {label} page={page} low activity")
            break

        time.sleep(0.3)

    return counts, debug_rows


# ---------------------------------------------------------------------------
# Price buckets
# ---------------------------------------------------------------------------

def price_buckets(step: int = 10_000, upper: int = 1_000_000) -> list[tuple]:
    intervals = []
    start = 0
    while start < upper:
        end = start + step
        p_from = 0 if start == 0 else start + 1
        intervals.append((p_from, end))
        start = end
    intervals.append((upper + 1, None))
    return intervals


def bucket_label(p_from: int | None, p_to: int | None) -> str:
    return f"{p_from}-plus" if p_to is None else f"{p_from}-{p_to}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today_str = date.today().isoformat()
    buckets = price_buckets(step=10_000, upper=1_000_000)
    log(f"Starting scrape: {len(buckets)} price buckets (10k NOK steps, cap=1M)")

    summary_rows = []
    all_debug: list[dict] = []

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
        df_dbg = pd.DataFrame(all_debug)
        df_dbg.to_csv(DEBUG_CSV, index=False, encoding="utf-8-sig")
        log(f"[CSV] Debug   -> {DEBUG_CSV}  ({len(df_dbg)} rows)")


if __name__ == "__main__":
    main()
