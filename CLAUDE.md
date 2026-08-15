# finn-mobility-packages

Scrapers that classify car dealer listings by package (Basis / Pluss / Premium) on finn.no (Norway) and blocket.se (Sweden). Runs as a scheduled Cloud Run Job on GCP (Sundays at 23:50 Oslo time). Results are written to BigQuery.

## ⚠️ Constraint that shapes everything: no direct GCP access

> **Claude Code cannot reach GCP.** The sandbox has no `gcloud`, no `bq`, and its network policy blocks GCP endpoints (and finn.no/blocket.se). Any plan that assumes running `gcloud`/`bq` locally will fail. GitHub is the only channel, in both directions:

```
Claude Code  ──push──>  GitHub  ──Actions (WIF)──>  GCP
     ^                    |                          |
     └────read repo───────┴──<─ data/ + job logs ──<─┘
```

| Need | How Claude does it |
|---|---|
| Ship code | push to `main` → `deploy.yml` builds and deploys |
| See the data | read `data/mobility_packages.csv` + `data/health.md` (committed weekly by `export_data.yml`); `git pull` for the latest |
| Logs / ad-hoc runs / live-site probes | trigger `ops.yml` via the GitHub API (`actions_run_trigger`, workflow_dispatch), then read the job log (`get_job_logs` with `return_content: true`) |

**Session start: read `data/health.md` first** — it leads with per-site freshness and flags stale/zero/volume-drop states.

**Rule:** if a new capability needs GCP, add a *named* action to `ops.yml` — never a free-form command input (that would make repo write access equivalent to full control of the GCP project), and never hand the user a manual Cloud Shell command when a workflow can do it.

### ops.yml menu

| action | does | writes? |
|---|---|---|
| `logs` | recent job output (freshness 8d — the job is weekly) | no |
| `warns` | only `[WARN]`/`[ERR]` lines, last 30d; prints `CLEAN` if none | no |
| `run-scraper` | executes the job now, then prints signal lines | **yes** (one extra BQ row per site) |
| `probe-finn` | runs `--test 20` against live finn.no from the runner | no |
| `probe-blocket` | runs `--test 20` against live blocket.se from the runner | no |
| `check-deploy` | deployed image (SHA-tagged) + recent execution history | no |

The probes are how you validate a detection change *before* deploying — GitHub runners can reach the scraped sites; the sandbox cannot.

## What it does

- **finn.no**: Scrapes `https://www.finn.no/mobility/search/car?dealer_segment=1&dealer_segment=2` across 101 price buckets (10k NOK steps from 0–1M NOK, then open-ended)
- **blocket.se**: Scrapes `https://www.blocket.se/mobility/search/car?dealer_segment=2` across 41 price buckets (25k SEK steps from 0–1M SEK, then open-ended)

For each bucket it samples 10% of listings from randomly selected pages, classifies each dealer, and writes CSVs + one summary row to BigQuery.

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

Detection mirrors finn.no: dealer logo + `externalprops` type on the Aurora podlet div.

| Package | `aurora-mobility-recommendations-podlet-content` `type` | Logo at `i.blocketcdn.se/pictures/stores/` | Section shown |
|---------|--------------------------------------------------------|--------------------------------------------|---------------|
| **Premium** | `inventory` | ✓ | "Flera annonser från oss" (own inventory) |
| **Pluss** | `recommendations` | ✓ | "Mer som det här" (other dealers) |
| **Basis** | `recommendations` | ✗ | "Mer som det här" (other dealers) |

All three packages use the **same div** (`aurora-mobility-recommendations-podlet-content`). The `type` field inside `externalprops` attribute distinguishes Premium from Pluss/Basis. The dealer logo (`i.blocketcdn.se/pictures/stores/`) distinguishes Pluss from Basis.

The `feature_package` GAM targeting key is **not present** in the static HTML.

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

### Repo data snapshots (`data/`)

Committed weekly by `export_data.yml` (Mondays 06:00 UTC, after the Sunday scrape) so results are readable without GCP access:

| File | Contents |
|------|----------|
| `data/mobility_packages.csv` | Full run history from BigQuery, sorted ASC so weekly diffs append |
| `data/weekly_package_mix.csv` | Weekly mix per site — counts + share (%) per package, Excel-ready (query lives in `scripts/export_weekly_mix.sh`) |
| `data/health.md` | Per-site freshness + status (OK / STALE / ZERO / VOLUME DROP) |

Only aggregate counts are committed — per-listing debug data never lands in git.

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
2. Target = `ceil(total × 0.10)` (10%)
3. Randomly select pages from the **first 50 pages** (`MAX_PAGES`) of each bucket — both sites cap pagination, and newest listings come first, which is what we want
4. Trim collected listings to exactly `target` via random subsample if needed

Guards: a `[WARN]` is logged when the target exceeds what's reachable within the page cap, and when a bucket collects fewer listings than its target. Failed ad-page fetches are excluded from counts (not classified as basis) on both sites, so throttling can't skew the package distribution.

**Open question — blocket speed:** Blocket is slower than finn because 25k SEK steps = 41 wide buckets with many listings each (vs finn's 101 narrow buckets). Two options if speed becomes an issue:
- Option A: Reduce step to 10k SEK → ~101 buckets, fewer listings per bucket
- Option B: Reduce `SAMPLE_FRACTION` from 0.10 to a lower value → smaller sample per bucket

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
- `deploy.yml` path-ignores `data/**` and `**.md` so the weekly snapshot commit doesn't trigger a no-op Docker build; the export commit also carries `[skip ci]` as belt-and-braces
- `ops.yml`/`export_data.yml` additionally rely on `roles/logging.viewer`, `roles/run.admin`, `roles/bigquery.jobUser` and `roles/bigquery.dataViewer` on `github-deployer` — already granted (in production use by vend-scraper-v2, same project and service account)
- `bq` under WIF prints a `WARNING: --scopes` line on **stdout**, ahead of the CSV header — the export pipes through `grep -v '^WARNING:'`; keep that if you touch the queries
- GitHub disables scheduled workflows after 60 days of repo inactivity; the weekly snapshot commit counts as activity, so `export_data.yml` is self-sustaining once running — but if it ever stops, check this first

**For new repos in the same GCP project:** workflow YAML is copy-paste, just swap image URL and job names. No Cloud Shell IAM commands needed — the Workload Identity Provider attribute condition is set to `assertion.repository_owner=='markusheiberg'`, so all repos under that GitHub account are trusted automatically.

## Common GCP commands (human, Cloud Shell only)

> These are reference commands for a human in Cloud Shell. Claude Code cannot run them — use the `ops.yml` actions instead (see the constraint section at the top).

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
