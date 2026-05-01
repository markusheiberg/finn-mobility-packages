# finn-mobility-packages

## Environment
- Runs in **GCP Cloud Shell** (`~/finn-mobility-packages`)
- User: `markusheiberg`
- GitHub repo: `markusheiberg/finn-mobility-packages`
- Python virtual env: `vend-scrapers-v2`
- Active dev branch: `claude/finn-packages-scraper-HQALh`

## Running scripts
```bash
# Always run from ~/finn-mobility-packages (already the CWD in Cloud Shell)
pip install requests beautifulsoup4 pandas --quiet
python finn_mobility_packages_requests.py
```

## Project goal
Scrape finn.no dealer car listings and classify each ad by package tier:
- **Basis** – standard listing
- **Pluss** – dealer logo + extra features
- **Premium** – full profilkort, dealer inventory shown

## Key files
- `finn_mobility_packages_requests.py` – main scraper
- `inspect_ads.py` – one-time diagnostic to diff HTML across package tiers
- `finn_mobility_packages_summary.csv` – output: counts per price bucket per package
- `finn_mobility_debug.csv` – output: per-listing signal dump for tuning

## Scraper design
- URL: `https://www.finn.no/mobility/search/car?dealer_segment=2&dealer_segment=1`
- Pagination handled via 10 000 NOK price buckets (0–1 000 000, then 1 000 001+)
- Price bucket boundaries use `price_from = prev_to + 1` to avoid duplicates
  e.g. bucket 375 001–400 000 → `price_from=375001&price_to=400000`

## Git workflow
- Develop on `claude/finn-packages-scraper-HQALh`
- Push: `git push -u origin claude/finn-packages-scraper-HQALh`
- Never push directly to `main`
