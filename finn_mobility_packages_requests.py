import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import date

SEARCH_URL = "https://www.finn.no/mobility/search/car"
AD_URL     = "https://www.finn.no/mobility/item/{finnkode}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nb-NO,nb;q=0.9,en-US;q=0.8,en;q=0.7",
}

MAX_PAGES_PER_BUCKET = 50
OUTPUT_CSV = "finn_mobility_packages_summary.csv"
DEBUG_CSV  = "finn_mobility_debug.csv"

# Badge CSS classes confirmed from HTML inspection of known Basis/Pluss/Premium ads
PREMIUM_BADGE_CLS = "bg-[--w-color-badge-warning-background]"   # amber/yellow
PLUSS_BADGE_CLS   = "bg-[--w-color-badge-negative-background]"  # red

# Shared session: reuses TCP/TLS connections across all requests
_session = requests.Session()
_session.headers.update(HEADERS)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def get(url: str) -> BeautifulSoup:
    r = _session.get(url, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def build_search_url(page: int, price_from: int | None, price_to: int | None) -> str:
    params = ["dealer_segment=2", "dealer_segment=1"]
    if price_from is not None:
        params.append(f"price_from={price_from}")
    if price_to is not None:
        params.append(f"price_to={price_to}")
    if page > 1:
        params.append(f"page={page}")
    return f"{SEARCH_URL}?{'&'.join(params)}"


# ── Pass 1: collect finnkodes from search results ─────────────────────────────

def extract_finnkode(href: str | None) -> str | None:
    if not href:
        return None
    m = re.search(r"finnkode=(\d+)", href)
    if m:
        return m.group(1)
    m = re.search(r"/mobility/item/(\d+)", href)
    return m.group(1) if m else None


def get_finnkodes_for_bucket(
    price_from: int | None,
    price_to: int | None,
) -> list[str]:
    seen: set[str] = set()
    finnkodes: list[str] = []

    for page in range(1, MAX_PAGES_PER_BUCKET + 1):
        url = build_search_url(page, price_from, price_to)
        print(f"  [SEARCH] {url}")
        try:
            soup = get(url)
        except Exception as e:
            print(f"  [ERR] {e}")
            break

        links = soup.find_all("a", href=lambda h: h and "/mobility/" in h)
        new = 0
        for link in links:
            fk = extract_finnkode(link.get("href"))
            if fk and fk not in seen:
                seen.add(fk)
                finnkodes.append(fk)
                new += 1

        print(f"  [SEARCH] page={page} new_ids={new} total={len(finnkodes)}")

        if new == 0:
            break
        if new < 5 and page > 3:
            break

    return finnkodes


# ── Pass 2: classify each ad by visiting its page ────────────────────────────

def classify_ad(finnkode: str) -> tuple[str, dict]:
    url = AD_URL.format(finnkode=finnkode)
    try:
        t0 = time.time()
        r = _session.get(url, timeout=30)
        elapsed = time.time() - t0
        kb = len(r.content) / 1024
        print(f"  [AD] {finnkode}  status={r.status_code}  {elapsed:.2f}s  {kb:.0f}KB")
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  [ERR] {finnkode}: {e}")
        return "error", {"finnkode": finnkode, "package": "error", "error": str(e)}

    all_classes: set[str] = set()
    for el in soup.find_all(True):
        for cls in el.get("class") or []:
            all_classes.add(cls)

    if PREMIUM_BADGE_CLS in all_classes:
        package = "premium"
    elif PLUSS_BADGE_CLS in all_classes:
        package = "pluss"
    else:
        package = "basis"

    debug = {
        "finnkode":          finnkode,
        "package":           package,
        "has_premium_badge": PREMIUM_BADGE_CLS in all_classes,
        "has_pluss_badge":   PLUSS_BADGE_CLS in all_classes,
    }
    return package, debug


# ── Price buckets ─────────────────────────────────────────────────────────────

def price_buckets(step: int = 10_000, upper: int = 1_000_000):
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today_str = date.today().isoformat()
    buckets   = price_buckets()

    # ── Pass 1: collect all finnkodes per bucket ──────────────────────────────
    print(f"=== PASS 1: collecting finnkodes ({len(buckets)} buckets) ===\n")
    bucket_finnkodes: dict[str, list[str]] = {}

    for idx, (p_from, p_to) in enumerate(buckets, 1):
        label = bucket_label(p_from, p_to)
        print(f"\n[BUCKET {idx}/{len(buckets)}] {label}")
        fks = get_finnkodes_for_bucket(p_from, p_to)
        bucket_finnkodes[label] = fks
        print(f"  → {len(fks)} ads found")

    all_finnkodes = list({fk for fks in bucket_finnkodes.values() for fk in fks})
    print(f"\nPass 1 done. {len(all_finnkodes)} unique finnkodes across all buckets.")

    # ── Pass 2: classify each unique ad ──────────────────────────────────────
    print(f"\n=== PASS 2: classifying {len(all_finnkodes)} ads ===\n")
    classification: dict[str, str] = {}
    debug_rows: list[dict]         = []

    for i, fk in enumerate(all_finnkodes, 1):
        package, debug = classify_ad(fk)
        classification[fk] = package
        debug_rows.append(debug)

        if i % 50 == 0 or i == len(all_finnkodes):
            dist = pd.Series(classification.values()).value_counts().to_dict()
            print(f"  [{i}/{len(all_finnkodes)}] {dist}")

        time.sleep(0.1)

    # ── Aggregate per bucket ──────────────────────────────────────────────────
    summary_rows = []
    for label, fks in bucket_finnkodes.items():
        counts = {"premium": 0, "pluss": 0, "basis": 0, "error": 0}
        for fk in fks:
            pkg = classification.get(fk, "error")
            counts[pkg] = counts.get(pkg, 0) + 1
        summary_rows.append({
            "date_collected": today_str,
            "price_bracket":  label,
            "premium_count":  counts["premium"],
            "pluss_count":    counts["pluss"],
            "basis_count":    counts["basis"],
        })

    df = pd.DataFrame(summary_rows)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n[CSV] {len(df)} rows → {OUTPUT_CSV}")
    print(df[df[["premium_count","pluss_count","basis_count"]].sum(axis=1) > 0].to_string())

    if debug_rows:
        pd.DataFrame(debug_rows).to_csv(DEBUG_CSV, index=False, encoding="utf-8-sig")
        print(f"[DEBUG CSV] → {DEBUG_CSV}")


if __name__ == "__main__":
    main()
