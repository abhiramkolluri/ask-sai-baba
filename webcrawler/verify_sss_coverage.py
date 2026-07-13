"""Verify that every discourse listed on ssssahitya.org's Sathya Sai Speaks
collection exists in the Weaviate Article collection (read-only diff).

Walks /collections/sathya-sai-speaks?page=1..N (same pagination pattern as the
scrapers), collects the /discourses/<year>/<slug> hrefs, and diffs them against
the `link` values stored in Weaviate. Reports both directions:
  - on the site but MISSING from Weaviate
  - tagged book="Sathya Sai Speaks" in Weaviate but not on the site listing

Usage:
    source ../backend/venv/bin/activate
    python verify_sss_coverage.py
"""

import json
import logging
import sys
from urllib.parse import urljoin

sys.path.insert(0, "../backend")

from dotenv import load_dotenv

load_dotenv("../backend/.env")

from scrape_vahinis import BASE_URL, fetch, ordered_unique  # noqa: E402
from scrape_summer_showers import listing_hrefs  # noqa: E402

LIST_URL = f"{BASE_URL}/collections/sathya-sai-speaks"
MAX_PAGES = 200  # ~1,499 discourses; stop early when a page adds nothing new

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("verify_sss")


def collect_site_hrefs():
    seen, seen_set = [], set()
    for page in range(1, MAX_PAGES + 1):
        hrefs = listing_hrefs(fetch(f"{LIST_URL}?page={page}"))
        new = [h for h in hrefs if h not in seen_set]
        if page % 10 == 0 or not new:
            log.info("page %d: %d links, %d new (total %d)", page, len(hrefs), len(new), len(seen))
        if not new:
            break
        seen.extend(new)
        seen_set.update(new)
    return seen


def main():
    site_hrefs = collect_site_hrefs()
    site_links = {urljoin(BASE_URL, h) for h in site_hrefs}
    print(f"\nSite listing: {len(site_links)} discourses")

    from weaviate_client import get_client
    client = get_client()
    articles = client.collections.get("Article")
    db_links = {}
    db_sss_links = set()
    for obj in articles.iterator(return_properties=["link", "book", "collection_name"]):
        props = obj.properties or {}
        link = (props.get("link") or "").strip()
        if link:
            db_links[link] = props.get("book") or props.get("collection_name") or ""
        if (props.get("book") or "") == "Sathya Sai Speaks" and link:
            db_sss_links.add(link)
    client.close()
    print(f"Weaviate: {len(db_links)} articles with links, {len(db_sss_links)} tagged Sathya Sai Speaks")

    missing = sorted(l for l in site_links if l not in db_links)
    extra = sorted(l for l in db_sss_links if l not in site_links)

    print(f"\nOn site but MISSING from Weaviate: {len(missing)}")
    for l in missing[:20]:
        print("  ", l)
    if len(missing) > 20:
        print(f"   ... and {len(missing) - 20} more")

    print(f"\nIn Weaviate as SSS but not on the site listing: {len(extra)}")
    for l in extra[:20]:
        print("  ", l)

    with open("sss_missing_links.json", "w") as f:
        json.dump(missing, f, indent=1)
    print(f"\nFull missing list written to sss_missing_links.json")

    # Per-year breakdown of what's missing, to see the shape of the gap.
    import re
    from collections import Counter
    years = Counter()
    for l in missing:
        m = re.search(r"/discourses/(\d{4})/", l)
        if m:
            years[m.group(1)] += 1
    if years:
        print("\nMissing by year:")
        for y, c in sorted(years.items()):
            print(f"  {y}: {c}")


if __name__ == "__main__":
    main()
