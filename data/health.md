# Scraper health

Generated 2026-10-09 11:43 UTC by .github/workflows/export_data.yml

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

## Latest run per site

```
+---------+------------------+----------+-------------+------------+---------------+-------------+-------------+--------+
|  site   |    latest_run    | age_days | total_count | prev_total | premium_count | pluss_count | basis_count | status |
+---------+------------------+----------+-------------+------------+---------------+-------------+-------------+--------+
| blocket | 2026-10-04 21:55 |        4 |       12351 |      12273 |          3726 |         323 |        8302 | OK     |
| finn    | 2026-10-04 21:50 |        4 |        5269 |       5224 |          2118 |        1332 |        1819 | OK     |
+---------+------------------+----------+-------------+------------+---------------+-------------+-------------+--------+
```

## Runs whose package mix looks wrong (full history)

Empty table = every run looks sane. A volume check cannot see these:
the total stays plausible while the classification breaks.

- **MIX SHIFT** — a package's share moved >15pp against the mean of the
  previous 4 runs. Runs with Premium or Basis at zero are left out of that
  baseline, so one broken run cannot flag the healthy run after it
- **<PACKAGE> ZERO** — a package that appeared in any of the previous 4
  runs is now zero (blocket 2026-05-17 and 05-24: Premium read as zero,
  every dealer as Basis)

Shares, not counts, so the 2026-07-20 sample change (5% -> 10%) does not flag.

```
+---------+------------------+---------------+-------------+-------------+-------------+-------------+-------------------+---------------------------+
|  site   |       run        | premium_count | pluss_count | basis_count | total_count | premium_pct | premium_pct_prev4 |         problems          |
+---------+------------------+---------------+-------------+-------------+-------------+-------------+-------------------+---------------------------+
| blocket | 2026-05-17 21:53 |             0 |           0 |        5940 |        5940 |         0.0 |              32.2 | MIX SHIFT; PREMIUM ZERO;  |
| blocket | 2026-05-24 21:53 |             0 |           0 |        5986 |        5986 |         0.0 |              32.2 | MIX SHIFT; PREMIUM ZERO;  |
+---------+------------------+---------------+-------------+-------------+-------------+-------------+-------------------+---------------------------+
```
