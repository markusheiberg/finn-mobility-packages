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

MAX_PAGES_PER_BUCKET = 50   # FINN caps search results at ~50 pages per filter
OUTPUT_CSV = "finn_mobility_packages_summary.csv"
DEBUG_CSV = "finn_mobility_debug.csv"

# ── Package tiers ─────────────────────────────────────────────────────────────
# All dealer cards occupy the same grid cell. Differences are visual signals:
#
#   top   – "Topp"-badge / highlighted border  +  dealer logo  +  ≥3 images
#   pluss – dealer logo visible  OR  ≥2 car images
#   basis – single image, no logo
#
# NOTE: Run once with DEBUG_CSV and inspect card_classes / has_logo / img_count
# to verify these rules against live HTML before trusting the summary counts.

# Broad ad-link selector covering all finn.no mobility sub-categories
AD_LINK_SEL = 'a[href*="/mobility/"][href*="finnkode="]'

LOGO_ALT_RE  = re.compile(r"\b(logo|dealer|forhandler|brand|merke|logotype)\b", re.I)
LOGO_CLS_RE  = re.compile(r"logo|dealer|brand|avatar|partner", re.I)
LOGO_SRC_RE  = re.compile(r"logo|brand|icon|dealer", re.I)

FEATURED_CLS_RE    = re.compile(r"topp|top[-_]?ad|featured|highlight|premium|gold|sponsored", re.I)
FEATURED_TESTID_RE = re.compile(r"topp|featured|highlight|premium|top[-_]?ad", re.I)


# ── URL builder ───────────────────────────────────────────────────────────────

def build_url(page: int, price_from: int | None, price_to: int | None) -> str:
    params = ["dealer_segment=1"]
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


# ── Card classification helpers ───────────────────────────────────────────────

def extract_finnkode(href: str | None) -> str | None:
    if not href:
        return None
    m = re.search(r"finnkode=(\d+)", href)
    return m.group(1) if m else None


def _is_logo_img(img) -> bool:
    alt = (img.get("alt") or "").strip()
    cls = " ".join(img.get("class") or [])
    src = (img.get("src") or "").lower()
    return (
        LOGO_ALT_RE.search(alt) is not None
        or LOGO_CLS_RE.search(cls) is not None
        or LOGO_SRC_RE.search(src) is not None
    )


def _has_dealer_logo(card) -> bool:
    # 1. Dedicated logo container via data-testid
    for el in card.find_all(attrs={"data-testid": True}):
        if re.search(r"logo|dealer[-_]?logo|brand[-_]?logo", el["data-testid"], re.I):
            return True
    # 2. Class-based logo container
    for el in card.find_all(["div", "span", "figure", "a"]):
        cls = " ".join(el.get("class") or [])
        if LOGO_CLS_RE.search(cls) and el.find("img"):
            return True
    # 3. Direct logo img
    for img in card.find_all("img"):
        if _is_logo_img(img):
            return True
    return False


def _count_car_images(card) -> int:
    seen: set[str] = set()
    count = 0
    for img in card.find_all("img"):
        if _is_logo_img(img):
            continue
        src = (img.get("src") or img.get("data-src") or "").strip()
        if not src:
            continue
        try:
            if int(img.get("width", 999)) < 40 or int(img.get("height", 999)) < 40:
                continue
        except (ValueError, TypeError):
            pass
        if src not in seen:
            seen.add(src)
            count += 1
    return count


def _is_featured(card) -> bool:
    card_cls    = " ".join(card.get("class") or [])
    card_testid = card.get("data-testid", "")
    if FEATURED_CLS_RE.search(card_cls) or FEATURED_TESTID_RE.search(card_testid):
        return True
    for el in card.find_all(["span", "div", "p", "strong", "label", "li"]):
        cls    = " ".join(el.get("class") or [])
        testid = el.get("data-testid", "")
        text   = (el.get_text() or "").strip()
        if FEATURED_CLS_RE.search(cls) or FEATURED_TESTID_RE.search(testid):
            return True
        if re.fullmatch(r"topp", text, re.I):
            return True
    return False


def classify_card(card) -> tuple[str | None, str, dict]:
    link     = card.select_one(AD_LINK_SEL)
    finnkode = extract_finnkode(link.get("href") if link else None)
    has_logo = _has_dealer_logo(card)
    img_count = _count_car_images(card)
    featured  = _is_featured(card)

    if featured:
        package = "top"
    elif has_logo or img_count >= 2:
        package = "pluss"
    else:
        package = "basis"

    return finnkode, package, {
        "finnkode":    finnkode,
        "package":     package,
        "img_count":   img_count,
        "has_logo":    has_logo,
        "is_featured": featured,
        "card_classes": " ".join(card.get("class") or []),
        "card_testid":  card.get("data-testid", ""),
    }


def get_listing_cards(soup: BeautifulSoup) -> list:
    # Try progressively broader selectors
    for sel in [
        "article",
        '[data-testid*="ad-list-item"]',
        '[data-testid*="search-result"]',
        '[data-testid*="listing"]',
        'div[class*="search-result"]',
    ]:
        candidates = soup.select(sel)
        cards = [c for c in candidates if c.select_one(AD_LINK_SEL)]
        if cards:
            return cards
    return []


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
            print(f"[STOP] bucket=({price_from},{price_to}) page={page}: no cards found")
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
            f"[PAGE]  bucket=({price_from},{price_to}) page={page} "
            f"new={new_rows} "
            f"(top={counts['top']}, pluss={counts['pluss']}, basis={counts['basis']})"
        )

        if new_rows == 0:
            print(f"[STOP] no new rows, moving to next bucket")
            break
        if new_rows < 5 and page > 3:
            print(f"[STOP] low activity ({new_rows} new), moving to next bucket")
            break

        time.sleep(0.3)

    return counts


# ── Price buckets ─────────────────────────────────────────────────────────────

def price_buckets(step: int = 10_000, upper: int = 1_000_000):
    """
    Non-overlapping 10 000 NOK buckets up to 1 000 000, then one open-ended bucket.

    Bucket boundaries (examples):
      0 – 10 000        → price_from=0,       price_to=10000
      10 001 – 20 000   → price_from=10001,   price_to=20000
      990 001 – 1000 000→ price_from=990001,  price_to=1000000
      1 000 001 +       → price_from=1000001  (no upper bound)
    """
    intervals = []
    start = 0
    while start < upper:
        end = start + step
        p_from = 0 if start == 0 else start + 1
        intervals.append((p_from, end))
        start = end
    intervals.append((upper + 1, None))   # open-ended top bucket
    return intervals


def bucket_label(p_from: int | None, p_to: int | None) -> str:
    return f"{p_from}-plus" if p_to is None else f"{p_from}-{p_to}"


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    today_str = date.today().isoformat()
    buckets = price_buckets()
    summary_rows: list[dict] = []
    debug_rows:   list[dict] = []

    print(f"Starting scrape: {len(buckets)} price buckets, step=25 000 NOK")

    for idx, (p_from, p_to) in enumerate(buckets, start=1):
        label = bucket_label(p_from, p_to)
        print(f"\n{'='*60}")
        print(f"BUCKET {idx}/{len(buckets)}: {label}")
        print(f"{'='*60}")

        counts = scrape_bucket(p_from, p_to, debug_rows)

        summary_rows.append({
            "date_collected": today_str,
            "price_bracket":  label,
            "top_count":      counts["top"],
            "pluss_count":    counts["pluss"],
            "basis_count":    counts["basis"],
        })

        time.sleep(0.5)

    # ── Summary CSV ───────────────────────────────────────────────────────────
    df = pd.DataFrame(summary_rows)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n[CSV] Wrote {len(df)} rows → {OUTPUT_CSV}")
    print(df.to_string())

    # ── Debug CSV (for tuning classification on first run) ────────────────────
    if debug_rows:
        df_debug = pd.DataFrame(debug_rows)
        df_debug.to_csv(DEBUG_CSV, index=False, encoding="utf-8-sig")
        print(f"\n[DEBUG CSV] Wrote {len(df_debug)} rows → {DEBUG_CSV}")
        print("\nPackage distribution:")
        print(df_debug["package"].value_counts().to_string())
        print("\nSample with has_logo=True:")
        print(df_debug[df_debug["has_logo"]].head(5).to_string())
        print("\nSample card_classes (first 10 unique):")
        print(df_debug["card_classes"].drop_duplicates().head(10).to_string())


if __name__ == "__main__":
    main()
