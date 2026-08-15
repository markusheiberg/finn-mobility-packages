#!/usr/bin/env bash
# Generate data/health.md — the pull-side health check.
# Covers the blind spot push alerts can't: a job that never ran prints nothing.
set -euo pipefail

OUT="${1:-data/health.md}"
PROJECT_ID="vend-scrapers-v2"

mkdir -p "$(dirname "$OUT")"
touch ~/.bigqueryrc

CSV=$(bq query --project_id="$PROJECT_ID" --use_legacy_sql=false --format=csv --max_rows=100 '
WITH ranked AS (
  SELECT
    site, run_timestamp, total_count,
    ROW_NUMBER() OVER (PARTITION BY site ORDER BY run_timestamp DESC) AS rn
  FROM `vend-scrapers-v2.market_scraper.mobility_packages`
)
SELECT
  site,
  FORMAT_TIMESTAMP("%F %H:%M", MAX(IF(rn = 1, run_timestamp, NULL))) AS latest_run,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(IF(rn = 1, run_timestamp, NULL)), DAY) AS days_old,
  MAX(IF(rn = 1, total_count, NULL)) AS latest_total,
  MAX(IF(rn = 2, total_count, NULL)) AS prev_total
FROM ranked
WHERE rn <= 2
GROUP BY site
ORDER BY site
' | grep -v '^WARNING:')

printf '%s\n' "$CSV" | python3 scripts/render_health.py "$OUT"

echo "--- $OUT ---"
cat "$OUT"
