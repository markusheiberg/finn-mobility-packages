# Private cars in Sweden: Tradera vs Blocket

Census of 2026-10-18 22:01 UTC, by `private_cars_se_compare.py` in the weekly Cloud Run job. History: `history.csv` (one block per week).

| | Tradera | Blocket |
|---|---:|---:|
| Private car listings (site count) | 3 049 | 26 528 |
| Listings read | 3 049 | 26 302 |
| Median price, kr | 52 000 | 72 000 |
| Price, 25th–75th percentile, kr | 24 994–108 000 | 35 000–140 000 |
| Median days since published | 22 | 24 (ID estimate) |
| Median days since published or renewed | – | 16 |

## Days since published, % of listings

| days | Tradera | Blocket (ID estimate) | Blocket (published or renewed) |
|---|---:|---:|---:|
| 0–1 | 4.5 | 3.8 | 5.7 |
| 1–7 | 17.9 | 13.6 | 20.9 |
| 7–14 | 14.7 | 13.7 | 16.5 |
| 14–30 | 26.0 | 25.7 | 28.5 |
| 30–60 | 36.8 | 43.3 | 28.5 |
| 60–90 | 0.1 | 0.0 | 0.0 |
| 90–180 | 0.0 | 0.0 | 0.0 |
| 180–365 | 0.0 | 0.0 | 0.0 |
| 365+ | 0.0 | 0.0 | 0.0 |

## Price, % of listings

| price, kr | Tradera | Blocket |
|---|---:|---:|
| 0k–25k | 25.1 | 14.6 |
| 25k–50k | 23.1 | 21.4 |
| 50k–75k | 12.7 | 14.7 |
| 75k–100k | 12.2 | 12.3 |
| 100k–150k | 11.1 | 13.8 |
| 150k–200k | 6.0 | 9.1 |
| 200k–300k | 5.4 | 8.1 |
| 300k–500k | 3.2 | 4.2 |
| 500k+ | 1.1 | 1.7 |

## Same car on both sites

| | cars |
|---|---:|
| Matched (brand, model, year, mileage, fuel, gearbox) | 392 |
| Expected by chance (same match, model year shifted one) | 61 |
| **Estimated on both** | **331** |
| …share of Tradera private cars | 11.0% |
| …share of Blocket private cars | 1.3% |
| Matches with price within 5% (by chance) | 231 (5) |

A lower bound: a car whose seller typed a different mileage or model on each site is not matched.

## How to read this

- **Tradera** dates are exact: `startDate` of a 60-day classified. A car relisted after 60 days restarts its clock.
- **Blocket (ID estimate)** is the closest Blocket gets to a publish date: ad IDs are issued in order, so an ad was created no later than the earliest card time among ads with a higher ID. It can only overstate freshness slightly, never invent age.
- **Blocket (published or renewed)** is the card's own time. Renewals and paid bumps reset it, so it understates age; the gap between the two Blocket columns is how much renewing goes on.
- Paid placements ("Betald placering") are left out of Blocket; they repeat across pages.
- Blocket was read through 17 price bands so every listing is reachable past its 50-page search limit.
