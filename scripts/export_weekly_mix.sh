#!/usr/bin/env bash
# Export weekly package mix (finn + blocket) from BigQuery to CSV.
# Single definition of the query — used by export_data.yml and runnable in Cloud Shell.
set -euo pipefail

OUT="${1:-data/weekly_package_mix.csv}"
PROJECT_ID="vend-scrapers-v2"

mkdir -p "$(dirname "$OUT")"
# Suppress first-run init banner; under WIF, bq also prints a WARNING line to
# STDOUT that would land ahead of the CSV header — strip it.
touch ~/.bigqueryrc

bq query --project_id="$PROJECT_ID" --use_legacy_sql=false --format=csv --max_rows=10000 '
SELECT
  DATE_TRUNC(DATE(run_timestamp), WEEK(MONDAY)) AS week,
  site,
  SUM(premium_count) AS premium,
  SUM(pluss_count)   AS pluss,
  SUM(basis_count)   AS basis,
  SUM(total_count)   AS total,
  ROUND(SAFE_DIVIDE(SUM(premium_count), SUM(total_count)) * 100, 1) AS premium_pct,
  ROUND(SAFE_DIVIDE(SUM(pluss_count),   SUM(total_count)) * 100, 1) AS pluss_pct,
  ROUND(SAFE_DIVIDE(SUM(basis_count),   SUM(total_count)) * 100, 1) AS basis_pct
FROM `vend-scrapers-v2.market_scraper.mobility_packages`
GROUP BY week, site
ORDER BY week DESC, site
' | grep -v '^WARNING:' > "$OUT"

echo "Wrote $OUT ($(wc -l < "$OUT") lines)"
head -3 "$OUT"
