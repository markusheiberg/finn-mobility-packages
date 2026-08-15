#!/usr/bin/env bash
# Export weekly package mix (finn + blocket) from BigQuery to CSV.
# Single definition of the query — used by export_data.yml and runnable in Cloud Shell.
#
# One row per (week, site), taken from the LATEST run in that week — not a SUM,
# because a week can hold more than one run (a manual ops.yml run-scraper adds a
# midweek row, and the early history has re-runs) and summing would double-count.
set -euo pipefail

OUT="${1:-data/weekly_package_mix.csv}"
PROJECT_ID="vend-scrapers-v2"

mkdir -p "$(dirname "$OUT")"
# Suppress first-run init banner; under WIF, bq also prints a WARNING line to
# STDOUT that would land ahead of the CSV header — strip it.
touch ~/.bigqueryrc

bq query --project_id="$PROJECT_ID" --use_legacy_sql=false --format=csv --max_rows=10000 '
WITH ranked AS (
  SELECT
    DATE_TRUNC(DATE(run_timestamp), WEEK(MONDAY)) AS week,
    site,
    premium_count, pluss_count, basis_count, total_count,
    ROW_NUMBER() OVER (
      PARTITION BY DATE_TRUNC(DATE(run_timestamp), WEEK(MONDAY)), site
      ORDER BY run_timestamp DESC
    ) AS rn
  FROM `vend-scrapers-v2.market_scraper.mobility_packages`
)
SELECT
  week,
  site,
  premium_count AS premium,
  pluss_count   AS pluss,
  basis_count   AS basis,
  total_count   AS total,
  ROUND(SAFE_DIVIDE(premium_count, total_count) * 100, 1) AS premium_pct,
  ROUND(SAFE_DIVIDE(pluss_count,   total_count) * 100, 1) AS pluss_pct,
  ROUND(SAFE_DIVIDE(basis_count,   total_count) * 100, 1) AS basis_pct
FROM ranked
WHERE rn = 1
ORDER BY week ASC, site
' | grep -v '^WARNING:' > "$OUT"

echo "Wrote $OUT ($(wc -l < "$OUT") lines)"
head -3 "$OUT"
