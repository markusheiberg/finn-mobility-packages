# Private cars in Sweden: Tradera vs Blocket

Census of 2026-10-25 23:01 UTC, by `private_cars_se_compare.py` in the weekly Cloud Run job. History: `history.csv` (one block per week).

| | Tradera | Blocket |
|---|---:|---:|
| Private car listings (site count) | 3 177 | 26 516 |
| Listings read | 3 177 | 26 293 |
| Median price, kr | 54 000 | 70 000 |
| Price, 25th–75th percentile, kr | 25 000–110 000 | 35 000–140 000 |
| Median days since published | 21 | 25 (ID estimate) |
| Median days since published or renewed | – | 17 |

## Days since published, % of listings

| days | Tradera | Blocket (ID estimate) | Blocket (published or renewed) |
|---|---:|---:|---:|
| 0–1 | 3.9 | 3.0 | 5.1 |
| 1–7 | 19.8 | 13.9 | 21.8 |
| 7–14 | 14.5 | 14.1 | 16.1 |
| 14–30 | 24.0 | 25.7 | 28.8 |
| 30–60 | 37.5 | 43.3 | 28.1 |
| 60–90 | 0.1 | 0.0 | 0.0 |
| 90–180 | 0.0 | 0.0 | 0.0 |
| 180–365 | 0.0 | 0.0 | 0.0 |
| 365+ | 0.0 | 0.0 | 0.0 |

## Price, % of listings

| price, kr | Tradera | Blocket |
|---|---:|---:|
| 0k–25k | 24.3 | 14.7 |
| 25k–50k | 23.2 | 21.5 |
| 50k–75k | 12.6 | 14.9 |
| 75k–100k | 12.2 | 12.3 |
| 100k–150k | 11.3 | 13.8 |
| 150k–200k | 6.1 | 8.9 |
| 200k–300k | 5.8 | 8.1 |
| 300k–500k | 3.3 | 4.2 |
| 500k+ | 1.1 | 1.6 |

## Same car on both sites

| | cars |
|---|---:|
| Matched (brand, model, year, mileage, fuel, gearbox) | 421 |
| Expected by chance (same match, model year shifted one) | 68 |
| **Estimated on both** | **353** |
| …share of Tradera private cars | 11.2% |
| …share of Blocket private cars | 1.4% |
| Matches with price within 5% (by chance) | 266 (9) |

A lower bound: a car whose seller typed a different mileage or model on each site is not matched.

## How to read this

- **Tradera** dates are exact: `startDate` of a 60-day classified. A car relisted after 60 days restarts its clock.
- **Blocket (ID estimate)** is the closest Blocket gets to a publish date: ad IDs are issued in order, so an ad was created no later than the earliest card time among ads with a higher ID. It can only overstate freshness slightly, never invent age.
- **Blocket (published or renewed)** is the card's own time. Renewals and paid bumps reset it, so it understates age; the gap between the two Blocket columns is how much renewing goes on.
- Paid placements ("Betald placering") are left out of Blocket; they repeat across pages.
- Blocket was read through 17 price bands so every listing is reachable past its 50-page search limit.
