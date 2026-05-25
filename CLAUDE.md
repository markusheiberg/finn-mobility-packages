# finn-mobility-packages

Scrapers that classify car dealer listings by package (Basis / Pluss / Premium) on finn.no (Norway) and blocket.se (Sweden). Runs as a scheduled Cloud Run Job on GCP (Sundays at 23:50 Oslo time). Results are written to BigQuery.

## What it does

- **finn.no**: Scrapes `https://www.finn.no/mobility/search/car?dealer_segment=1&dealer_segment=2` across 101 price buckets (10k NOK steps from 0–1M NOK, then open-ended)
- **blocket.se**: Scrapes `https://www.blocket.se/mobility/search/car?dealer_segment=2` across 41 price buckets (25k SEK steps from 0–1M SEK, then open-ended)

For each bucket it samples 5% of listings from randomly selected pages, classifies each dealer, and writes CSVs + one summary row to BigQuery.

## Package classification rules

### finn.no

| Package | Signal |
|---------|--------|
| **Basis** | No dealer logo on the ad page (just bold text company name) |
| **Pluss** | Dealer logo hosted on `dealerhub.cdn-vend.com` — no inventory podlet |
| **Premium** | Dealer logo **+** `"type":"inventory"` in the static HTML (Aurora recommendations podlet with own inventory at the bottom) |

Logo detection: `img[src*="dealerhub.cdn-vend.com"]` or `img[src*="mobility-company-profile"]` (excluding NBF badge).

Premium detection: `"type":"inventory"` appears in the raw HTML inside the Aurora podlet's `externalProps` attribute.

### blocket.se

Detection is based on the GAM advertising targeting JSON embedded in the static HTML. Blocket sets `"feature_package"` in the page's ad-targeting state for every listing, making it the most reliable signal.

| Package | Signal in static HTML | Section shown |
|---------|----------------------|---------------|
| **Premium** | `"feature_package":["PREMIUM"]` in raw HTML, **or** `"type":"inventory"` (secondary) | "Flera annonser från oss" (own inventory) |
| **Pluss** | `"feature_package":["PLUS"]` in raw HTML | No recommendations section |
| **Basis** | Neither Premium nor Pluss signal | "Mer som det här" (other dealers) |

The `aurora-mobility-recommendations-podlet-content` div is present for **both** Pluss and Basis listings, so it cannot be used to distinguish them. The `feature_package` key appears in the prebid/GAM targeting JSON (e.g. `{"key":"feature_package","value":["PLUS"]}`).

Logos are not a reliable signal on blocket.se.

## Scripts

| Script | Site | Filter |
|--------|------|--------|
| `finn_mobility_packages_requests.py` | finn.no | `dealer_segment=1&dealer_segment=2` |
| `blocket_mobility_packages_requests.py` | blocket.se | `dealer_segment=2` |
| `run_all.py` | both | Entry point for Cloud Run Job |

## Running

```bash
# Full scrape — both sites (used by Cloud Run Job)
python3 run_all.py

# Individual scripts
python3 finn_mobility_packages_requests.py
python3 blocket_mobility_packages_requests.py

# Quick test on first ~N listings (no price filter)
python3 finn_mobility_packages_requests.py --test 30
python3 blocket_mobility_packages_requests.py --test 30
```

## Output

Each run writes to three places:

### CSVs
| File | Contents |
|------|----------|
| `finn_mobility_packages_summary.csv` | Latest run — one row per price bucket (easy inspection) |
| `blocket_mobility_packages_summary.csv` | Latest run — one row per price bucket (easy inspection) |
| `runs/finn_mobility_packages_summary_<timestamp>.csv` | Timestamped archive of every run |
| `runs/blocket_mobility_packages_summary_<timestamp>.csv` | Timestamped archive of every run |
| `runs/finn_mobility_debug_<timestamp>.csv` | Per-listing detail: finnkode, org_name, package |
| `runs/blocket_mobility_debug_<timestamp>.csv` | Per-listing detail: blocketkod, org_name, package |

The `runs/` folder is gitignored and lives only on GCP.

### BigQuery
One row appended per run to `vend-scrapers-v2.market_scraper.mobility_packages`:

| Column | Type | Description |
|--------|------|-------------|
| `run_timestamp` | TIMESTAMP | UTC timestamp of the run |
| `site` | STRING | `finn` or `blocket` |
| `premium_count` | INTEGER | Total premium dealers across all price buckets |
| `pluss_count` | INTEGER | Total pluss dealers across all price buckets |
| `basis_count` | INTEGER | Total basis dealers across all price buckets |
| `total_count` | INTEGER | Sum of all packages |

## Sampling strategy

1. Fetch page 1 of each bucket → parse total listing count
2. Target = `ceil(total × 0.05)` (5%)
3. Randomly select pages across the full page range to avoid recency bias
4. Trim collected listings to exactly `target` via random subsample if needed

**Open question — blocket speed:** Blocket is slower than finn because 25k SEK steps = 41 wide buckets with many listings each (vs finn's 101 narrow buckets). Two options if speed becomes an issue:
- Option A: Reduce step to 10k SEK → ~101 buckets, fewer listings per bucket
- Option B: Reduce `SAMPLE_FRACTION` from 0.05 to 0.02 → 2% sample instead of 5%

## Key implementation details

- **Global dealer cache** (`org_name → package`): each dealer is classified only once across all price buckets
- **Two-phase scrape**: Phase 1 collects `(finnkode/blocketkod, org_name)` from search pages; Phase 2 fetches individual ad pages for classification
- **Org name extraction**: parsed from `span.truncate` containing `∙` separator (e.g. "Oslo ∙ Dealer Name")
- finn.no is **not** a Next.js app — no `__NEXT_DATA__`. Uses Schibsted "podlet" micro-frontends

## GCP configuration

- Project ID: `vend-scrapers-v2`
- Dataset: `market_scraper`
- Table: `mobility_packages`
- Region: `europe-north1`
- Job name: `mobility-packages`
- Scheduler: `mobility-packages-2350-sunday` (europe-west1, Sundays 23:50 Oslo time)
- Artifact Registry: `europe-north1-docker.pkg.dev/vend-scrapers-v2/scrapers/mobility-packages`

## CI/CD — GitHub Actions auto-deploy

Push to `main` → GitHub Actions builds and deploys automatically. No manual `gcloud builds submit` needed.

**How it works:**
- Trigger: push to `main`
- Auth: Workload Identity Federation (no stored GCP keys)
- Steps: `docker build` → `docker push` to Artifact Registry → `gcloud run jobs update`

**GCP setup (already done for this project):**
- Service account: `github-deployer@vend-scrapers-v2.iam.gserviceaccount.com`
- Roles: `roles/artifactregistry.writer` + `roles/run.developer`
- Workload Identity Pool: `github-pool` / Provider: `github-provider`

**Gotchas:**
- Use `docker build` + `docker push` directly — `gcloud builds submit` requires Viewer/Owner role
- Service account needs `roles/artifactregistry.writer` explicitly for image pushes

**For new repos in the same GCP project:** workflow YAML is copy-paste, just swap image URL and job names. No Cloud Shell IAM commands needed — the Workload Identity Provider attribute condition is set to `assertion.repository_owner=='markusheiberg'`, so all repos under that GitHub account are trusted automatically.

## Common GCP commands

### Build and push image
```bash
gcloud builds submit --tag europe-north1-docker.pkg.dev/vend-scrapers-v2/scrapers/mobility-packages:latest
```

### Update Cloud Run Job
```bash
gcloud run jobs update mobility-packages --image europe-north1-docker.pkg.dev/vend-scrapers-v2/scrapers/mobility-packages:latest --region europe-north1
```

### Run job manually
```bash
gcloud run jobs execute mobility-packages --region europe-north1
```

### View logs
```bash
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=mobility-packages" --limit 100 --format "value(textPayload)" --freshness=10m | grep -E "\[BQ\]|\[ERR\]|Done|Starting"
```

### Check BigQuery
```bash
bq query --use_legacy_sql=false 'SELECT * FROM `vend-scrapers-v2.market_scraper.mobility_packages` ORDER BY run_timestamp DESC LIMIT 10'
```

### Check scheduler
```bash
gcloud scheduler jobs list --location europe-west1
```

## Artifact Registry cleanup policy

Both `europe-north1` and `europe-west1` scrapers repositories have a cleanup policy set to keep the 2 most recent image versions. Old images are deleted automatically — no manual cleanup needed.

## Dependencies

```
requests
beautifulsoup4
pandas
google-cloud-bigquery
```
