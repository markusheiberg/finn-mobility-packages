"""Render data/health.md from the freshness/volume CSV on stdin.

Weekly cadence (Sundays 23:50 Oslo): STALE means the latest run is more than
8 days old. Freshness is measured against now on purpose — a missed run is
exactly what this check exists to catch. The volume check compares against the
previous run, not a calendar window, so manual midweek runs cannot produce
false positives.
"""
import csv
import sys
from datetime import datetime, timezone

STALE_DAYS = 8          # weekly cadence + one day of slack
DROP_FRACTION = 0.5     # latest total below 50% of previous run
EXPECTED_SITES = {"finn", "blocket"}

out_path = sys.argv[1]
rows = list(csv.DictReader(sys.stdin))

problems = []
seen = {r["site"] for r in rows}
for missing in sorted(EXPECTED_SITES - seen):
    problems.append(f"**NO DATA**: `{missing}` has no rows in the table at all.")

table = [
    "| site | latest run (UTC) | age (days) | total | prev total | change |",
    "|---|---|---|---|---|---|",
]
for r in rows:
    days = int(r["days_old"])
    total = int(r["latest_total"])
    prev = int(r["prev_total"]) if r["prev_total"] else None
    change = f"{(total - prev) / prev * 100:+.1f}%" if prev else "n/a"
    flags = []
    if days > STALE_DAYS:
        problems.append(
            f"**STALE**: `{r['site']}` latest run is {days} days old "
            f"(weekly cadence expects <= {STALE_DAYS})."
        )
        flags.append("⚠️")
    if prev and total < prev * DROP_FRACTION:
        problems.append(
            f"**VOLUME DROP**: `{r['site']}` total {total} vs previous {prev} — "
            f"a breaking parser usually halves the count rather than zeroing it."
        )
        flags.append("📉")
    site_cell = r["site"] + (" " + "".join(flags) if flags else "")
    table.append(
        f"| {site_cell} | {r['latest_run']} | {days} | {total} "
        f"| {prev if prev is not None else ''} | {change} |"
    )

lines = [
    "# Scraper health",
    "",
    f"Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
    "",
    *table,
    "",
]
if problems:
    lines.append("## Problems")
    lines.extend(f"- {p}" for p in problems)
else:
    lines.append("**Status: CLEAN** — fresh data, no volume anomalies.")

with open(out_path, "w") as f:
    f.write("\n".join(lines) + "\n")
