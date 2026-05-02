import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict
from typing import Optional
import time
import json
import logging
import re

logger = logging.getLogger(__name__)

BASE_URL = "https://www.finn.no"
SEARCH_URL = f"{BASE_URL}/mobility/search.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "no,en;q=0.9",
}


@dataclass
class FinnListing:
    finn_code: str
    title: str
    price: Optional[str]
    year: Optional[str]
    mileage: Optional[str]
    location: Optional[str]
    url: str
    image_url: Optional[str]


def _parse_listing(article) -> Optional[FinnListing]:
    try:
        link_tag = article.find("a", href=True)
        if not link_tag:
            return None

        href = link_tag["href"]
        url = href if href.startswith("http") else BASE_URL + href
        finn_code = url.rstrip("/").split("/")[-1]

        title_tag = article.find(["h2", "h3"])
        title = title_tag.get_text(strip=True) if title_tag else ""

        price_tag = article.find(class_=lambda c: c and "price" in c.lower())
        price = price_tag.get_text(strip=True) if price_tag else None

        details_text = article.get_text(" ", strip=True)

        year = None
        mileage = None
        year_match = re.search(r"\b(19|20)\d{2}\b", details_text)
        if year_match:
            year = year_match.group()
        km_match = re.search(r"([\d\s]+)\s*km\b", details_text, re.IGNORECASE)
        if km_match:
            mileage = km_match.group().strip()

        location_tag = article.find(class_=lambda c: c and "location" in c.lower())
        location = location_tag.get_text(strip=True) if location_tag else None

        img_tag = article.find("img")
        image_url = img_tag.get("src") if img_tag else None

        return FinnListing(
            finn_code=finn_code,
            title=title,
            price=price,
            year=year,
            mileage=mileage,
            location=location,
            url=url,
            image_url=image_url,
        )
    except Exception as exc:
        logger.warning("Failed to parse listing: %s", exc)
        return None


def scrape_page(
    session: requests.Session, params: dict, page: int = 1
) -> tuple[list[FinnListing], bool]:
    p = dict(params)
    if page > 1:
        p["page"] = page

    resp = session.get(SEARCH_URL, params=p, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = soup.find_all("article")
    listings = [lst for a in articles if (lst := _parse_listing(a)) is not None]

    has_next = bool(
        soup.find("a", attrs={"aria-label": lambda v: v and "neste" in v.lower()})
    )

    return listings, has_next


def scrape(
    query: str = "",
    max_pages: int = 5,
    delay: float = 1.0,
    extra_params: dict | None = None,
) -> list[FinnListing]:
    session = requests.Session()
    params: dict = {}
    if query:
        params["q"] = query
    if extra_params:
        params.update(extra_params)

    all_listings: list[FinnListing] = []

    for page in range(1, max_pages + 1):
        logger.info("Scraping page %d", page)
        try:
            listings, has_next = scrape_page(session, params, page)
        except requests.RequestException as exc:
            logger.error("Request failed on page %d: %s", page, exc)
            break

        all_listings.extend(listings)
        logger.info("Page %d: %d listings (total %d)", page, len(listings), len(all_listings))

        if not has_next:
            break
        if page < max_pages:
            time.sleep(delay)

    return all_listings


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    results = scrape(query="toyota", max_pages=2)
    print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
    print(f"\nTotal listings scraped: {len(results)}")
