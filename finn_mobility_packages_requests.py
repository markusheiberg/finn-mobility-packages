import re
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
OUTPUT_CSV = "/home/user/finn-mobility-packages/finn_mobility_packages_summary.csv"
DEBUG_CSV = "/home/user/finn-mobility-packages/finn_mobility_debug.csv"


def build_url(page: int, price_from: int | None, price_to: int | None) -> str:
    params = ["dealer_segment=1", "dealer_segment=2"]
    if price_from is not None:
        params.append(f"price_from={price_from}")
    if price_to is not None:
        params.append(f"price_to={price_to}")
    if page > 1:
        params.append(f"page={page}")
    return f"{BASE_URL}?{'&'.join(params)}"


def get_soup(page: int, price_from: int | None, price_to: int | None):
    url = build_url(page, price_from, price_to)
    print(f"\n[GET] {url}")
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), r.url


def extract_finnkode_from_href(href: str | None) -> str | None:
    if not href:
        return None
    m = re.search(r"finnkode=(\d+)", href)
    if m:
        return m.group(1)
    # also handle path-style: /mobility/car/ad.html?finnkode=...
    m = re.search(r"/(\d{8,})", href)
    return m.group(1) if m else None


def classify_card(card) -> tuple[str | None, str, dict]:
    """
    Returns (finnkode, package_name, debug_info).

    package_name is one of: 'premium', 'pluss', 'basis'

    Classification signals:
      - premium: card has a featured/topp/premium badge or highlight marker
      - pluss:   card has a visible dealer logo OR >= 2 car images
      - basis:   everything else
    """
    # --- finnkode ---
    link = card.select_one('a[href*="/mobility/"], a[href*="finnkode="]')
    href = link.get("href") if link else None
    finnkode = extract_finnkode_from_href(href)

    card_text = card.get_text(" ", strip=True).lower()
    card_classes = " ".join(card.get("class") or []).lower()
    card_testid = card.get("data-testid", "").lower()

    # --- signal 1: featured/premium badge ---
    FEATURED_KEYWORDS = ["topp", "featured", "premium", "highlight", "sponsored", "fremhevet"]
    is_featured = False

    # check badge elements
    badge_selectors = [
        '[data-testid*="badge"]',
        '[data-testid*="featured"]',
        '[data-testid*="premium"]',
        '[class*="badge"]',
        '[class*="featured"]',
        '[class*="premium"]',
        '[class*="highlight"]',
        '[class*="sponsored"]',
        "mark",
        "strong",
    ]
    for sel in badge_selectors:
        el = card.select_one(sel)
        if el:
            el_text = el.get_text(" ", strip=True).lower()
            if any(kw in el_text for kw in FEATURED_KEYWORDS):
                is_featured = True
                break

    # also check card-level class/testid
    if not is_featured:
        if any(kw in card_classes for kw in FEATURED_KEYWORDS):
            is_featured = True
        elif any(kw in card_testid for kw in FEATURED_KEYWORDS):
            is_featured = True

    # --- signal 2: dealer logo ---
    LOGO_SELECTORS = [
        '[data-testid*="dealer-logo"]',
        '[data-testid*="logo"]',
        '[class*="dealer-logo"]',
        '[class*="dealerLogo"]',
        '[class*="brand-logo"]',
        '[class*="brandLogo"]',
    ]
    has_logo = False
    for sel in LOGO_SELECTORS:
        if card.select_one(sel):
            has_logo = True
            break

    if not has_logo:
        for img in card.select("img"):
            src = (img.get("src") or "").lower()
            alt = (img.get("alt") or "").lower()
            cls = " ".join(img.get("class") or []).lower()
            if any(kw in src + alt + cls for kw in ["logo", "dealer", "brand", "merkevare"]):
                has_logo = True
                break

    # --- signal 3: image count (car images only) ---
    LOGO_SKIP_KEYWORDS = ["logo", "dealer", "brand", "merkevare", "icon", "avatar"]
    seen_src: set[str] = set()
    car_img_count = 0

    for img in card.select("img"):
        src = (img.get("src") or "").strip()
        alt = (img.get("alt") or "").lower()
        cls = " ".join(img.get("class") or []).lower()

        if not src or src in seen_src:
            continue

        # skip obvious non-car images
        if any(kw in alt + cls for kw in LOGO_SKIP_KEYWORDS):
            continue

        # skip tiny icons (width/height attributes < 40)
        try:
            w = int(img.get("width") or 0)
            h = int(img.get("height") or 0)
            if (w and w < 40) or (h and h < 40):
                continue
        except (ValueError, TypeError):
            pass

        seen_src.add(src)
        car_img_count += 1

    # --- classify ---
    if is_featured:
        package = "premium"
    elif has_logo or car_img_count >= 2:
        package = "pluss"
    else:
        package = "basis"

    debug = {
        "finnkode": finnkode,
        "package": package,
        "is_featured": is_featured,
        "has_logo": has_logo,
        "img_count": car_img_count,
        "card_classes": card_classes[:120],
        "card_testid": card_testid[:80],
    }

    return finnkode, package, debug


def get_listing_cards(soup: BeautifulSoup) -> list:
    AD_LINK_PATTERNS = [
        'a[href*="/mobility/car/ad"]',
        'a[href*="/mobility/"]',
        'a[href*="finnkode="]',
    ]

    candidates = soup.select("article")
    if not candidates:
        candidates = soup.select('[data-testid*="search-result"]')
    if not candidates:
        candidates = soup.select('[data-testid*="ad-list-item"]')
    if not candidates:
        candidates = soup.select('div[class*="search-result"]')
    if not candidates:
        candidates = soup.select('li[class*="result"]')

    cards = []
    for pattern in AD_LINK_PATTERNS:
        for c in candidates:
            if c.select_one(pattern) and c not in cards:
                cards.append(c)
        if cards:
            break

    # fallback: any container that has a finnkode link
    if not cards:
        for c in candidates:
            if extract_finnkode_from_href(
                (c.select_one("a") or {}).get("href")
            ):
                cards.append(c)

    return cards


def scrape_bucket(
    price_from: int | None, price_to: int | None
) -> tuple[dict, list[dict]]:
    counts = {"premium": 0, "pluss": 0, "basis": 0}
    seen: set[str] = set()
    debug_rows: list[dict] = []

    for page in range(1, MAX_PAGES_PER_BUCKET + 1):
        try:
            soup, final_url = get_soup(page, price_from, price_to)
        except Exception as e:
            print(f"[ERR] bucket=({price_from},{price_to}) page={page} fetch -> {e}")
            break

        cards = get_listing_cards(soup)
        if not cards:
            print(f"[STOP] bucket=({price_from},{price_to}) page={page} no cards found")
            break

        new_rows = 0

        for card in cards:
            finnkode, package, debug = classify_card(card)
            if not finnkode or finnkode in seen:
                continue
            seen.add(finnkode)

            counts[package] += 1
            new_rows += 1

            debug_rows.append(
                {
                    "price_bracket": bucket_label(price_from, price_to),
                    "page": page,
                    **debug,
                }
            )

        print(
            f"[PAGE] bucket=({price_from},{price_to}) page={page} "
            f"new={new_rows} total="
            f"(P={counts['premium']}, Pl={counts['pluss']}, B={counts['basis']})"
        )

        if new_rows == 0:
            print(f"[STOP] bucket=({price_from},{price_to}) page={page} no new rows")
            break

        if new_rows < 5 and page > 3:
            print(
                f"[STOP] bucket=({price_from},{price_to}) page={page} low activity"
            )
            break

        time.sleep(0.3)

    return counts, debug_rows


def price_buckets(step: int = 10_000, upper: int = 1_000_000):
    """
    10k NOK steps: 0-10000, 10001-20000, ..., 990001-1000000, then 1000001+ open-ended.
    """
    intervals = []
    start = 0
    while start < upper:
        end = start + step
        p_from = 0 if start == 0 else start + 1
        p_to = end
        intervals.append((p_from, p_to))
        start = end
    intervals.append((upper + 1, None))
    return intervals


def bucket_label(p_from: int | None, p_to: int | None) -> str:
    if p_to is None:
        return f"{p_from}-plus"
    return f"{p_from}-{p_to}"


def main():
    today_str = date.today().isoformat()
    buckets = price_buckets(step=10_000, upper=1_000_000)
    summary_rows = []
    all_debug_rows = []

    print(f"Starting scrape: {len(buckets)} price buckets (step=10 000 NOK, cap=1 000 000 NOK)")

    for idx, (p_from, p_to) in enumerate(buckets, start=1):
        label = bucket_label(p_from, p_to)
        print(f"\n=== BUCKET {idx}/{len(buckets)}: {label} ===")

        counts, debug_rows = scrape_bucket(p_from, p_to)
        all_debug_rows.extend(debug_rows)

        summary_rows.append(
            {
                "date_collected": today_str,
                "price_bracket": label,
                "premium_count": counts["premium"],
                "pluss_count": counts["pluss"],
                "basis_count": counts["basis"],
                "total_count": sum(counts.values()),
            }
        )

        time.sleep(0.5)

    df_summary = pd.DataFrame(summary_rows)
    df_summary.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n[CSV] Summary: {len(df_summary)} rows -> {OUTPUT_CSV}")
    print(df_summary)

    if all_debug_rows:
        df_debug = pd.DataFrame(all_debug_rows)
        df_debug.to_csv(DEBUG_CSV, index=False, encoding="utf-8-sig")
        print(f"[CSV] Debug:   {len(df_debug)} rows -> {DEBUG_CSV}")


if __name__ == "__main__":
    main()
