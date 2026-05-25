"""
Debug tool: fetch a specific blocket listing and show all classification signals.

Usage:
    python3 debug_blocket_listing.py 23548030
    python3 debug_blocket_listing.py https://www.blocket.se/mobility/item/23548030
"""
import re
import sys
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
}

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 debug_blocket_listing.py <listing_id_or_url>")
        sys.exit(1)

    arg = sys.argv[1]
    m = re.search(r"(\d{6,})", arg)
    if not m:
        print(f"Could not parse listing ID from: {arg}")
        sys.exit(1)
    listing_id = m.group(1)
    url = f"https://www.blocket.se/mobility/item/{listing_id}"

    print(f"\nFetching: {url}")
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    raw = r.text
    soup = BeautifulSoup(raw, "html.parser")

    print(f"\n{'='*60}")
    print(f"LISTING {listing_id}")
    print(f"{'='*60}")

    # --- feature_package signal (GAM/prebid targeting) ---
    print("\n[1] feature_package occurrences:")
    for m2 in re.finditer(r'feature_package["\s:]*\[?"?([A-Z]+)"?\]?', raw):
        # Find context around the match
        start = max(0, m2.start() - 30)
        end = min(len(raw), m2.end() + 30)
        print(f"    -> '{m2.group(0)}'")
        print(f"       context: ...{raw[start:end].strip()}...")

    fp_match = re.search(r'"feature_package":\["([A-Z]+)"\]', raw)
    if fp_match:
        print(f"\n    *** Detected feature_package = {fp_match.group(1)} ***")
    else:
        print("\n    *** feature_package NOT found in page ***")

    # --- Aurora div IDs ---
    print("\n[2] Aurora podlet divs present:")
    for div_id in [
        "aurora-mobility-recommendations-podlet-content",
        "aurora-mobility-inventory-podlet-content",
    ]:
        div = soup.find(id=div_id)
        if div:
            ext = div.get("externalprops") or div.get("externalProps") or ""
            # Extract just the type field
            type_m = re.search(r'"type":"([^"]+)"', ext)
            type_val = type_m.group(1) if type_m else "(no type)"
            print(f"    FOUND  #{div_id}  type={type_val}")
        else:
            print(f"    absent #{div_id}")

    # --- type:inventory anywhere ---
    inv_count = raw.count('"type":"inventory"')
    rec_count = raw.count('"type":"recommendations"')
    print(f"\n[3] Raw string counts:")
    print(f"    \"type\":\"inventory\"       = {inv_count}x")
    print(f"    \"type\":\"recommendations\" = {rec_count}x")

    # --- Classification result ---
    print(f"\n[4] Classification:")
    if '"feature_package":["PREMIUM"]' in raw:
        result = "premium"
    elif '"feature_package":["PLUS"]' in raw:
        result = "pluss"
    else:
        result = "basis"
    print(f"    -> {result.upper()}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
