"""
Run this once to compare the raw HTML signals across three known ad pages:
  python inspect_ads.py

It prints a structured side-by-side diff of class names, data-testid values,
image counts, and any text that could be a package badge.
"""
import re
import requests
from bs4 import BeautifulSoup
from collections import Counter

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nb-NO,nb;q=0.9,en-US;q=0.8,en;q=0.7",
}

KNOWN = [
    ("basis",   "https://www.finn.no/mobility/item/461739442"),
    ("pluss",   "https://www.finn.no/mobility/item/461868207"),
    ("premium", "https://www.finn.no/mobility/item/462000695"),
]

# Short text strings that might be package labels
LABEL_RE = re.compile(
    r"\b(basis|pluss|premium|profilkort|pakke|package|featured|highlight)\b", re.I
)


def fetch(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def all_classes(soup: BeautifulSoup) -> Counter:
    c: Counter = Counter()
    for el in soup.find_all(True):
        for cls in el.get("class") or []:
            c[cls] += 1
    return c


def all_testids(soup: BeautifulSoup) -> list[str]:
    return [
        el.get("data-testid")
        for el in soup.find_all(attrs={"data-testid": True})
    ]


def label_texts(soup: BeautifulSoup) -> list[str]:
    hits = []
    for el in soup.find_all(["span", "div", "p", "h1", "h2", "h3", "strong", "li", "label"]):
        text = (el.get_text(strip=True) or "")
        if LABEL_RE.search(text) and len(text) < 80:
            hits.append(text)
    return list(dict.fromkeys(hits))  # deduplicate, preserve order


def img_srcs(soup: BeautifulSoup) -> list[str]:
    srcs = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if src:
            srcs.append(src[:80])
    return srcs


def section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


results = {}
for tier, url in KNOWN:
    print(f"[FETCH] {tier}: {url}")
    soup = fetch(url)
    results[tier] = {
        "soup":     soup,
        "classes":  all_classes(soup),
        "testids":  all_testids(soup),
        "labels":   label_texts(soup),
        "img_srcs": img_srcs(soup),
    }

tiers = [t for t, _ in KNOWN]

# ── 1. Label texts containing package keywords ────────────────────────────────
section("TEXT LABELS matching basis|pluss|premium|profilkort (per tier)")
for tier in tiers:
    print(f"\n  [{tier.upper()}]")
    for t in results[tier]["labels"]:
        print(f"    {t!r}")

# ── 2. data-testid values ─────────────────────────────────────────────────────
section("data-testid values (unique per tier, not shared by all)")
all_testid_sets = {tier: set(results[tier]["testids"]) for tier in tiers}
for tier in tiers:
    others = set().union(*(all_testid_sets[t] for t in tiers if t != tier))
    unique = all_testid_sets[tier] - others
    shared = all_testid_sets[tier] & set.intersection(*[all_testid_sets[t] for t in tiers])
    print(f"\n  [{tier.upper()}] unique to this tier:")
    for v in sorted(unique):
        print(f"    {v!r}")

section("data-testid values present in ALL three tiers")
shared_testids = set.intersection(*[set(results[t]["testids"]) for t in tiers])
for v in sorted(shared_testids):
    print(f"  {v!r}")

# ── 3. CSS class diff ─────────────────────────────────────────────────────────
section("CSS classes unique to each tier (not present in the other two)")
for tier in tiers:
    own   = set(results[tier]["classes"].keys())
    other = set().union(*(set(results[t]["classes"].keys()) for t in tiers if t != tier))
    unique = own - other
    print(f"\n  [{tier.upper()}] ({len(unique)} unique classes):")
    for cls in sorted(unique):
        print(f"    .{cls}  (×{results[tier]['classes'][cls]})")

# ── 4. Image count ────────────────────────────────────────────────────────────
section("Image count per tier")
for tier in tiers:
    print(f"  [{tier.upper()}]  {len(results[tier]['img_srcs'])} images")

# ── 5. Raw image src sample ───────────────────────────────────────────────────
section("Image src samples (first 8 per tier)")
for tier in tiers:
    print(f"\n  [{tier.upper()}]")
    for src in results[tier]["img_srcs"][:8]:
        print(f"    {src}")
