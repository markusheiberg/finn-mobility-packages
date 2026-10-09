# shellcheck shell=bash
# History of SAMPLE_FRACTION, the share of each price bucket's listings that
# both scrapers classify. Sourced by the exports; not run directly.
#
# The BigQuery table has no column for it, and the package COUNTS scale with it
# (they doubled on 2026-07-20), so the exports derive a sample_fraction column
# from this history instead. Shares are comparable across a change; counts are not.
#
# When SAMPLE_FRACTION changes in the scrapers, append "<UTC deploy time>|<value>".
# check_sample_fraction makes the export FAIL until you do, so a change can never
# go unrecorded the way the 2026-07-20 one did.
SAMPLE_FRACTION_HISTORY=(
  "2026-04-01 00:00:00|0.05"   # first run (backfill row 2026-05-02) through 2026-07-19
  "2026-07-20 15:53:27|0.10"   # commit 91ddf01; first run at 10% was 2026-07-26
)

# SQL CASE expression mapping a TIMESTAMP column to the fraction in force.
sample_fraction_sql() {
  local col="$1" sql="CASE" i ts val
  for (( i=${#SAMPLE_FRACTION_HISTORY[@]}-1; i>=0; i-- )); do
    ts="${SAMPLE_FRACTION_HISTORY[$i]%%|*}"; val="${SAMPLE_FRACTION_HISTORY[$i]##*|}"
    sql+=" WHEN ${col} >= TIMESTAMP '${ts}' THEN ${val}"
  done
  echo "${sql} END"
}

# Fail if a scraper's SAMPLE_FRACTION is not the latest recorded value.
check_sample_fraction() {
  local root="${1:-.}" latest f val
  latest="${SAMPLE_FRACTION_HISTORY[-1]##*|}"
  for f in "$root"/finn_mobility_packages_requests.py "$root"/blocket_mobility_packages_requests.py; do
    val="$(sed -nE 's/^SAMPLE_FRACTION[[:space:]]*=[[:space:]]*([0-9.]+).*/\1/p' "$f")"
    if [[ -z "$val" ]] || ! awk -v a="$val" -v b="$latest" 'BEGIN{exit !(a+0==b+0)}'; then
      echo "::error::$(basename "$f") has SAMPLE_FRACTION=${val:-?} but scripts/sample_fraction.sh records ${latest}. Append the change there." >&2
      return 1
    fi
  done
  echo "SAMPLE_FRACTION ${latest} matches both scrapers."
}
