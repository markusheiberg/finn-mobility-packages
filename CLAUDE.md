# finn-mobility-packages

Scrapers that classify car dealer listings by package (Basis / Pluss / Premium) on finn.no (Norway) and blocket.se (Sweden).

## What it does

Scrapes `https://www.finn.no/mobility/search/car?dealer_segment=1&dealer_segment=2` across 101 price buckets (10k NOK steps from 0–1M NOK, then open-ended). For each bucket it samples 5% of listings from randomly selected pages, classifies each dealer, and writes two CSVs.

### Package classification rules

| Package | Signal |
|---------|--------|
| **Basis** | No dealer logo on the ad page (just bold text company name) |
| **Pluss** | Dealer logo hosted on `dealerhub.cdn-vend.com` — no inventory podlet |
| **Premium** | Dealer logo **+** `"type":"inventory"` in the static HTML (Aurora recommendations podlet with own inventory at the bottom) |

Logo detection: `img[src*="dealerhub.cdn-vend.com"]` or `img[src*="mobility-company-profile"]` (excluding NBF badge).

Premium detection: `"type":"inventory"` appears in the raw HTML of Premium ad pages inside the Aurora podlet's `externalprops` attribute. It is absent on Pluss pages. All other Premium signals ("Se annonsen på selgerens side", "Flere annonser fra oss", `rel="sponsored"`) are JS-rendered and not in the static HTML.

## Scripts

| Script | Site | Filter |
|--------|------|--------|
| `finn_mobility_packages_requests.py` | finn.no | `dealer_segment=1&dealer_segment=2` |
| `blocket_mobility_packages_requests.py` | blocket.se | `dealer_segment=2` |

## Running

```bash
# Full scrape (~101 buckets, ~5% sample per bucket)
python3 finn_mobility_packages_requests.py
python3 blocket_mobility_packages_requests.py

# Quick test on first ~N listings (no price filter)
python3 finn_mobility_packages_requests.py --test 30
python3 blocket_mobility_packages_requests.py --test 30
```

## Output

### finn.no
| File | Contents |
|------|----------|
| `finn_mobility_packages_summary.csv` | One row per price bucket: date, bracket, premium/pluss/basis counts, total |
| `finn_mobility_debug.csv` | One row per listing: price bracket, finnkode, org_name, package |

### blocket.se
| File | Contents |
|------|----------|
| `blocket_mobility_packages_summary.csv` | One row per price bucket: date, bracket, premium/pluss/basis counts, total |
| `blocket_mobility_debug.csv` | One row per listing: price bracket, blocketkod, org_name, package |

## Sampling strategy

1. Fetch page 1 of each bucket → parse total listing count ("X treff")
2. Target = `ceil(total × 0.05)` (5%)
3. Randomly select pages across the full page range (not just page 1) to avoid recency bias (finn.no sorts newest-first)
4. Trim collected listings to exactly `target` via random subsample if a page yields more than needed

## Key implementation details

- **Global dealer cache** (`org_name → package`): each dealer is classified only once across all price buckets, saving many HTTP requests
- **Two-phase scrape**: Phase 1 collects `(finnkode, org_name)` from search pages; Phase 2 fetches individual ad pages for classification
- **Org name extraction**: parsed from `span.truncate` containing `∙` separator (e.g. "Oslo ∙ Dealer Name")
- **Finnkode extraction**: from `<a id="461145507">` attribute, falling back to `/mobility/item/(\d+)` regex on href
- finn.no is **not** a Next.js app — no `__NEXT_DATA__`. Uses Schibsted "podlet" micro-frontends; most dynamic content is JS-rendered

## Dependencies

```
requests
beautifulsoup4
pandas
```
