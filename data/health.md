# Scraper health

Generated 2026-10-05 13:33 UTC by .github/workflows/export_data.yml

Weekly scrape: Sundays 23:50 Oslo, one row per site per run.

status per site:
- **OK** — fresh row, sane volume
- **STALE** — latest row older than 8 days: the job may not be running
  at all for this site. No run also means no [WARN] in the logs, so
  this table is the only thing that catches it.
- **ZERO** — a row was written but nothing was classified (scrape ran,
  parser found no listings — likely a site markup change)
- **VOLUME DROP** — total more than 50% below the previous run (a
  breaking parser usually halves the count rather than zeroing it)

```
+---------+------------------+----------+-------------+------------+---------------+-------------+-------------+--------+
|  site   |    latest_run    | age_days | total_count | prev_total | premium_count | pluss_count | basis_count | status |
+---------+------------------+----------+-------------+------------+---------------+-------------+-------------+--------+
| blocket | 2026-10-04 21:55 |        0 |       12351 |      12273 |          3726 |         323 |        8302 | OK     |
| finn    | 2026-10-04 21:50 |        0 |        5269 |       5224 |          2118 |        1332 |        1819 | OK     |
+---------+------------------+----------+-------------+------------+---------------+-------------+-------------+--------+
```
