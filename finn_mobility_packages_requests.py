import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import date

BASE_URL = "https://www.finn.no/mobility/search.html"
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

# Package tiers (all cards are same grid size; differences are visual signals):
#   top   – featured badge / highlighted border  +  dealer logo  +  ≥3 images
#   pluss – dealer logo visible  OR  ≥2 car images in card (but not top)
#   basis – single image, no logo, standard card

# ── Selector helpers ──────────────────────────────────────────────────────────

AD_LINK_SEL = (
    'a[href*="/mobility/car/ad.html"],'
    'a[href*="/mobility/bus/ad.html"],'
    'a[href*="/mobility/van/ad.html"],'
    'a[href*="/mobility/mc/ad.html"],'
    'a[href*="/mobility/truck/ad.html"],'
    'a[href*="/mobility/caravan/ad.html"],'
    'a[href*="/mobility/motorhome/ad.html"]'
)

# Patterns that suggest an image is a dealer logo rather than a car photo
LOGO_ALT_PATTERNS = re.compile(
    r"\b(logo|dealer|forhandler|brand|merke|merkevare|logotype)\b", re.I
)
LOGO_CLASS_PATTERNS = re.compile(
    r"logo|dealer|brand|avatar|icon|partner", re.I
)

# Classes on the card itself that FINN uses for featured / top placement
FEATURED_CLASS_PATTERNS = re.compile(
    r"topp|top[-_]?ad|featured|highlight|premium|sponsored|gold", re.I
)
FEATURED_TESTID_PATTERNS = re.compile(
    r"topp|featured|highlight|premium|top[-_]?ad", re.I
)


# ── URL builder ───────────────────────────────────────────────────────────────

def build_url(page: int, price_from: int | None, price_to: int | None) -> str:
    params = ["sales_form=1"]  # dealer listings only
    if price_from is not None:
        params.append(f"price_from={price_from}")
    if price_to is not None:
        params.append(f"price_to={price_to}")
    params.append(f"page={page}")
    return f"{BASE_URL}?{'&'.join(params)}"


def get_soup(page: int, price_from: int | None, price_to: int | None):
    url = build_url(page, price_from, price_to)
    print(f"\n[GET] {url}")
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), r.url


# ── Card helpers ──────────────────────────────────────────────────────────────

def extract_finnkode(href: str | None) -> str | None:
    if not href:
        return None
    m = re.search(r"finnkode=(\d+)", href)
    return m.group(1) if m else None


def _is_logo_img(img) -> bool:
    """Return True if this img element looks like a dealer/brand logo."""
    alt = (img.get("alt") or "").strip()
    cls = " ".join(img.get("class") or [])
    src = (img.get("src") or "").lower()

    if LOGO_ALT_PATTERNS.search(alt):
        return True
    if LOGO_CLASS_PATTERNS.search(cls):
        return True
    # Small inlined SVG / icon sprites are not car photos
    if "logo" in src or "brand" in src or "icon" in src:
        return True
    return False


def _has_dealer_logo(card) -> bool:
    """
    Return True when a dealer logo is visibly embedded in the card.

    Signals checked:
      1. An <img> whose alt/class/src suggests it is a logo.
      2. A dedicated logo container element (data-testid="dealer-logo",
         class containing "logo" / "dealer-logo", etc.).
    """
    # data-testid containers
    for el in card.find_all(attrs={"data-testid": True}):
        testid = el.get("data-testid", "")
        if re.search(r"logo|dealer[-_]?logo|brand[-_]?logo", testid, re.I):
            return True

    # class-based containers (div/span/figure)
    for el in card.find_all(["div", "span", "figure", "a"]):
        cls = " ".join(el.get("class") or [])
        if LOGO_CLASS_PATTERNS.search(cls):
            # Confirm it actually contains an image or is itself a logo element
            if el.find("img") or el.name in ("figure", "span"):
                return True

    # direct img inspection
    for img in card.find_all("img"):
        if _is_logo_img(img):
            return True

    return False


def _count_car_images(card) -> int:
    """Count images that are actual car/listing photos (not logos or icons)."""
    seen_src: set[str] = set()
    count = 0
    for img in card.find_all("img"):
        if _is_logo_img(img):
            continue
        src = (img.get("src") or img.get("data-src") or "").strip()
        if not src:
            continue
        # Skip tiny icons (w/h hints in the src or explicit attributes)
        width = img.get("width")
        height = img.get("height")
        if width and int(width) < 40:
            continue
        if height and int(height) < 40:
            continue
        if src not in seen_src:
            seen_src.add(src)
            count += 1
    return count


def _is_featured(card) -> bool:
    """
    Return True when the card carries visual signals for the "Topp" package:
      - a CSS class on the card itself (highlighted border, gold background…)
      - a data-testid attribute matching featured/top patterns
      - a child element with text "Topp" (FINN's explicit badge)
      - a <span>/<div> with class matching premium/featured patterns
    """
    card_cls = " ".join(card.get("class") or [])
    if FEATURED_CLASS_PATTERNS.search(card_cls):
        return True

    card_testid = card.get("data-testid", "")
    if FEATURED_TESTID_PATTERNS.search(card_testid):
        return True

    # Child elements
    for el in card.find_all(["span", "div", "p", "strong", "label"]):
        cls = " ".join(el.get("class") or [])
        testid = el.get("data-testid", "")
        text = (el.get_text() or "").strip()

        if FEATURED_CLASS_PATTERNS.search(cls):
            return True
        if FEATURED_TESTID_PATTERNS.search(testid):
            return True
        # Explicit badge text "Topp" (case-insensitive, standalone word)
        if re.fullmatch(r"topp", text, re.I):
            return True

    return False


def classify_card(card) -> tuple[str | None, str, dict]:
    """
    Returns (finnkode, package, debug_info).

    package is one of: "top", "pluss", "basis"
    """
    link = card.select_one(AD_LINK_SEL)
    href = link.get("href") if link else None
    finnkode = extract_finnkode(href)

    has_logo = _has_dealer_logo(card)
    img_count = _count_car_images(card)
    featured = _is_featured(card)

    if featured:
        package = "top"
    elif has_logo or img_count >= 2:
        package = "pluss"
    else:
        package = "basis"

    debug = {
        "finnkode": finnkode,
        "package": package,
        "img_count": img_count,
        "has_logo": has_logo,
        "is_featured": featured,
        "card_classes": " ".join(card.get("class") or []),
    }
    return finnkode, package, debug


def get_listing_cards(soup: BeautifulSoup) -> list:
    candidates = soup.select("article")
    if not candidates:
        candidates = soup.select('[data-testid*="search-result"]')
    if not candidates:
        candidates = soup.select('[data-testid*="listing"]')
    if not candidates:
        candidates = soup.select('div[class*="search-result"]')

    cards = []
    for c in candidates:
        if c.select_one(AD_LINK_SEL):
            cards.append(c)
    return cards


# ── Bucket scraper ────────────────────────────────────────────────────────────

def scrape_bucket(
    price_from: int | None,
    price_to: int | None,
    debug_rows: list,
) -> dict:
    counts = {"top": 0, "pluss": 0, "basis": 0}
    seen: set[str] = set()

    for page in range(1, MAX_PAGES_PER_BUCKET + 1):
        try:
            soup, _ = get_soup(page, price_from, price_to)
        except Exception as e:
            print(f"[ERR] bucket=({price_from},{price_to}) page={page} -> {e}")
            break

        cards = get_listing_cards(soup)
        if not cards:
            print(f"[STOP] bucket=({price_from},{price_to}) page={page} no cards")
            break

        new_rows = 0
        for card in cards:
            finnkode, package, debug = classify_card(card)
            if not finnkode or finnkode in seen:
                continue
            seen.add(finnkode)
            counts[package] += 1
            new_rows += 1
            debug_rows.append(debug)

        print(
            f"[SUMMARY] bucket=({price_from},{price_to}) page={page} "
            f"new={new_rows} "
            f"(top={counts['top']}, pluss={counts['pluss']}, basis={counts['basis']})"
        )

        if new_rows == 0:
            print(f"[STOP] no new rows")
            break
        if new_rows < 5 and page > 3:
            print(f"[STOP] low activity, breaking.")
            break

        time.sleep(0.2)

    return counts


# ── Price buckets ─────────────────────────────────────────────────────────────

def price_buckets(step: int = 100_000, upper: int = 2_000_000):
    """
    Non-overlapping NOK price buckets suited to used-car price ranges on FINN.
    E.g. 0-100000, 100001-200000, ..., 1900001-2000000, 2000001+
    """
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


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    today_str = date.today().isoformat()
    buckets = price_buckets()
    summary_rows = []
    debug_rows: list[dict] = []

    for idx, (p_from, p_to) in enumerate(buckets, start=1):
        label = bucket_label(p_from, p_to)
        print(f"\n=== BUCKET {idx}/{len(buckets)}: {label} ===")

        counts = scrape_bucket(p_from, p_to, debug_rows)

        summary_rows.append(
            {
                "date_collected": today_str,
                "price_bracket": label,
                "top_count": counts["top"],
                "pluss_count": counts["pluss"],
                "basis_count": counts["basis"],
            }
        )

        time.sleep(0.5)

    df = pd.DataFrame(summary_rows)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n[CSV] wrote {len(df)} rows to {OUTPUT_CSV}")
    print(df.to_string())

    # Debug dump – helps tune classification rules on first run
    if debug_rows:
        df_debug = pd.DataFrame(debug_rows)
        df_debug.to_csv(DEBUG_CSV, index=False, encoding="utf-8-sig")
        print(f"\n[DEBUG CSV] wrote {len(df_debug)} rows to {DEBUG_CSV}")
        print("\nPackage distribution across all listings:")
        print(df_debug["package"].value_counts().to_string())
        print("\nSample rows where has_logo=True:")
        print(df_debug[df_debug["has_logo"]].head(5).to_string())


if __name__ == "__main__":
    main()
