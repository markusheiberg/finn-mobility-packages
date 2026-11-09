# Private cars in Sweden: Tradera vs Blocket

Census of 2026-11-08 23:01 UTC, by `private_cars_se_compare.py` in the weekly Cloud Run job. History: `history.csv` (one block per week).

| | Tradera | Blocket |
|---|---:|---:|
| Private car listings (site count) | 3 220 | 26 479 |
| Listings read | 3 220 | 26 326 |
| Median price, kr | 56 000 | 70 000 |
| Price, 25th–75th percentile, kr | 25 000–115 000 | 35 000–139 900 |
| Median days since published | 21 | 26 (ID estimate) |
| Median days since published or renewed | – | 16 |

## Days since published, % of listings

| days | Tradera | Blocket (ID estimate) | Blocket (published or renewed) |
|---|---:|---:|---:|
| 0–1 | 3.6 | 3.5 | 5.4 |
| 1–7 | 17.7 | 13.6 | 21.6 |
| 7–14 | 14.7 | 13.8 | 16.5 |
| 14–30 | 27.8 | 24.2 | 26.8 |
| 30–60 | 36.0 | 38.8 | 29.7 |
| 60–90 | 0.1 | 0.0 | 0.0 |
| 90–180 | 0.1 | 2.4 | 0.0 |
| 180–365 | 0.0 | 3.8 | 0.0 |
| 365+ | 0.0 | 0.0 | 0.0 |

## Price, % of listings

| price, kr | Tradera | Blocket |
|---|---:|---:|
| 0k–25k | 23.7 | 14.8 |
| 25k–50k | 22.6 | 21.7 |
| 50k–75k | 12.4 | 14.7 |
| 75k–100k | 12.7 | 12.6 |
| 100k–150k | 12.1 | 13.6 |
| 150k–200k | 6.2 | 8.9 |
| 200k–300k | 5.8 | 8.2 |
| 300k–500k | 3.3 | 4.1 |
| 500k+ | 1.1 | 1.4 |

## Same car on both sites

| | cars |
|---|---:|
| Matched (brand, model, year, mileage, fuel, gearbox) | 412 |
| Expected by chance (same match, model year shifted one) | 67 |
| **Estimated on both** | **345** |
| …share of Tradera private cars | 10.8% |
| …share of Blocket private cars | 1.3% |
| Matches with price within 5% (by chance) | 270 (8) |

A lower bound: a car whose seller typed a different mileage or model on each site is not matched.

## How to read this

- **Tradera** dates are exact: `startDate` of a 60-day classified. A car relisted after 60 days restarts its clock.
- **Blocket (ID estimate)** is the closest Blocket gets to a publish date: ad IDs are issued in order, so an ad was created no later than the earliest card time among ads with a higher ID. It can only overstate freshness slightly, never invent age.
- **Blocket (published or renewed)** is the card's own time. Renewals and paid bumps reset it, so it understates age; the gap between the two Blocket columns is how much renewing goes on.
- Paid placements ("Betald placering") are left out of Blocket; they repeat across pages.
- Blocket was read through 17 price bands so every listing is reachable past its 50-page search limit.
