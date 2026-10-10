# Private cars in Sweden: Tradera vs Blocket

Census of 2026-10-10 18:44 UTC, by `private_cars_se_compare.py` in the weekly Cloud Run job. History: `history.csv` (one block per week).

| | Tradera | Blocket |
|---|---:|---:|
| Private car listings (site count) | 3 011 | 26 601 |
| Listings read | 3 011 | 26 441 |
| Median price, kr | 50 000 | 72 900 |
| Price, 25th–75th percentile, kr | 24 000–105 000 | 35 000–140 000 |
| Median days since published | 22 | 25 (ID estimate) |
| Median days since published or renewed | – | 15 |

## Days since published, % of listings

| days | Tradera | Blocket (ID estimate) | Blocket (published or renewed) |
|---|---:|---:|---:|
| 0–1 | 3.5 | 2.9 | 5.0 |
| 1–7 | 18.2 | 14.9 | 22.8 |
| 7–14 | 14.3 | 14.9 | 17.8 |
| 14–30 | 27.2 | 23.3 | 25.6 |
| 30–60 | 36.6 | 29.5 | 28.8 |
| 60–90 | 0.1 | 9.9 | 0.1 |
| 90–180 | 0.0 | 4.5 | 0.0 |
| 180–365 | 0.0 | 0.0 | 0.0 |
| 365+ | 0.0 | 0.0 | 0.0 |

## Price, % of listings

| price, kr | Tradera | Blocket |
|---|---:|---:|
| 0k–25k | 25.8 | 14.4 |
| 25k–50k | 23.1 | 21.4 |
| 50k–75k | 12.5 | 14.8 |
| 75k–100k | 12.3 | 12.4 |
| 100k–150k | 11.0 | 13.9 |
| 150k–200k | 6.1 | 8.9 |
| 200k–300k | 5.0 | 8.2 |
| 300k–500k | 3.2 | 4.3 |
| 500k+ | 1.0 | 1.7 |

## How to read this

- **Tradera** dates are exact: `startDate` of a 60-day classified. A car relisted after 60 days restarts its clock.
- **Blocket (ID estimate)** is the closest Blocket gets to a publish date: ad IDs are issued in order, so an ad was created no later than the earliest card time among ads with a higher ID. It can only overstate freshness slightly, never invent age.
- **Blocket (published or renewed)** is the card's own time. Renewals and paid bumps reset it, so it understates age; the gap between the two Blocket columns is how much renewing goes on.
- Paid placements ("Betald placering") are left out of Blocket; they repeat across pages.
- Blocket was read through 17 price bands so every listing is reachable past its 50-page search limit.
