"""Scrape the Chinna Katha short stories from ssssahitya.org into chinna_katha.json.

The /snippets/chinna-katha listing is paginated like the discourse collections,
but story links have no year segment (/snippets/chinna-katha/<slug>) and story
pages carry no Date/Event/Location metadata — they are timeless parables, so
`date` is left empty. Page parsing (h1 title + div.ck-content body) is reused
from scrape_summer_showers.parse_discourse.

Usage:
    source ../backend/venv/bin/activate
    python scrape_chinna_katha.py
"""

import json
import logging
import os
import re
import sys
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scrape_vahinis import BASE_URL, fetch, ordered_unique
from scrape_summer_showers import parse_discourse, MAX_PAGES

LIST_URL = f"{BASE_URL}/snippets/chinna-katha"
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chinna_katha.json")
STORY_HREF = re.compile(r"^/snippets/chinna-katha/(?!null$)[^/]+$")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("scrape_chinna_katha")


def collect_all_hrefs():
    seen, seen_set = [], set()
    for page in range(1, MAX_PAGES + 1):
        soup = BeautifulSoup(fetch(f"{LIST_URL}?page={page}"), "html.parser")
        hrefs = ordered_unique(
            a["href"] for a in soup.find_all("a", href=True) if STORY_HREF.match(a["href"])
        )
        new = [h for h in hrefs if h not in seen_set]
        log.info("page %d: %d links, %d new", page, len(hrefs), len(new))
        if not new:
            break
        seen.extend(new)
        seen_set.update(new)
    return seen


def main():
    hrefs = collect_all_hrefs()
    log.info("total unique stories: %d", len(hrefs))
    if not hrefs:
        sys.exit("no story links found — site layout may have changed")

    records, empty = [], 0
    for idx, href in enumerate(hrefs):
        url = urljoin(BASE_URL, href)
        title, content, _meta = parse_discourse(fetch(url))
        for _ in range(2):
            if content and title != "Loading...":
                break
            log.warning("got loading shell for %s; refetching", url)
            title, content, _meta = parse_discourse(fetch(url))
        if not content:
            empty += 1
            log.error("EMPTY content: %s", url)
        records.append({
            "title": title,
            "content": content,
            "link": url,
            "collection_name": "Chinna Katha",
            "book_slug": "chinna-katha",
            "chapter_index": idx,
            "location": "",
            "occasion": "",
            "date": "",
        })
        if (idx + 1) % 25 == 0 or idx + 1 == len(hrefs):
            log.info("[%d/%d] %s", idx + 1, len(hrefs), title)

    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT_PATH)

    words = sum(len(r["content"].split()) for r in records)
    print(f"\nChinna Katha: {len(records)} stories, {words} words")
    print(f"wrote {len(records)} records -> {OUT_PATH}")
    if empty:
        sys.exit(f"{empty} stories had empty content — inspect before using the output")


if __name__ == "__main__":
    main()
