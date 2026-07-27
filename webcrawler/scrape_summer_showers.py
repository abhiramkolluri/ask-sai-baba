"""Scrape a paginated ssssahitya.org discourse collection into JSON.

Collection listings (/collections/<slug>?page=1..N) link to shared
/discourses/<year>/<slug> pages. Discourse pages carry the title in <h1>, the
body in div.ck-content (same as vahini chapters), and short metadata <p> lines
("Date : ...", "Event : ...", "Location: ...") that map to the Article
schema's date/occasion/location.

Reuses fetch/ordered_unique politeness + retry helpers from scrape_vahinis.

Usage:
    source ../backend/venv/bin/activate
    python scrape_summer_showers.py                                        # Summer Showers
    python scrape_summer_showers.py --slug monsoon-showers --name "Monsoon Showers"
"""

import argparse
import json
import logging
import os
import re
import sys
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scrape_vahinis import BASE_URL, fetch, ordered_unique

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_PAGES = 50  # hard stop; pagination walk ends when a page adds nothing new

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("scrape_summer_showers")

# exclude the site's broken literal ".../null" CMS entries
DISCOURSE_HREF = re.compile(r"^/discourses/(\d{4})/(?!null$)[^/]+$")
META_PREFIXES = {"date": "Date", "occasion": "Event", "location": "Location"}


def listing_hrefs(page_html: str):
    soup = BeautifulSoup(page_html, "html.parser")
    return ordered_unique(
        a["href"] for a in soup.find_all("a", href=True)
        if DISCOURSE_HREF.match(a["href"])
    )


def collect_all_hrefs(list_url):
    """Walk ?page=1..N until a page contributes no new discourse links."""
    seen = []
    seen_set = set()
    for page in range(1, MAX_PAGES + 1):
        hrefs = listing_hrefs(fetch(f"{list_url}?page={page}"))
        new = [h for h in hrefs if h not in seen_set]
        log.info("page %d: %d links, %d new", page, len(hrefs), len(new))
        if not new:
            break
        seen.extend(new)
        seen_set.update(new)
    return seen


def parse_discourse(page_html: str):
    """(title, content, meta) from a /discourses/<year>/<slug> page."""
    soup = BeautifulSoup(page_html, "html.parser")
    main = soup.find("main") or soup
    h1 = main.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""

    body = main.find("div", class_="ck-content")
    paras = [p.get_text(" ", strip=True) for p in body.find_all("p")] if body else []
    content = "\n\n".join(p for p in paras if p)

    meta = {"date": "", "occasion": "", "location": ""}
    for p in main.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) > 120:
            continue
        for key, prefix in META_PREFIXES.items():
            m = re.match(rf"^{prefix}\s*:\s*(.+)$", text)
            if m and not meta[key]:
                meta[key] = m.group(1).strip()
    return title, content, meta


def main():
    ap = argparse.ArgumentParser(description="Scrape a ssssahitya.org discourse collection")
    ap.add_argument("--slug", default="summer-showers", help="collection URL slug")
    ap.add_argument("--name", default="Summer Showers", help="collection display name prefix")
    args = ap.parse_args()
    list_url = f"{BASE_URL}/collections/{args.slug}"
    out_path = os.path.join(HERE, args.slug.replace("-", "_") + ".json")

    hrefs = collect_all_hrefs(list_url)
    log.info("total unique discourses: %d", len(hrefs))
    if not hrefs:
        sys.exit("no discourse links found — site layout may have changed")

    records = []
    per_year = {}
    empty = 0
    for idx, href in enumerate(hrefs):
        url = urljoin(BASE_URL, href)
        year = DISCOURSE_HREF.match(href).group(1)
        title, content, meta = parse_discourse(fetch(url))
        # transient "Loading..." shells, as seen on vahini chapter pages
        for _ in range(2):
            if content and title != "Loading...":
                break
            log.warning("got loading shell for %s; refetching", url)
            title, content, meta = parse_discourse(fetch(url))
        if not content:
            empty += 1
            log.error("EMPTY content: %s", url)
        if not meta["date"]:
            # some collections (e.g. Ati Rudra Maha Yajna) have no Date line in
            # the CMS ("No Day"); fall back to the year from the URL
            meta["date"] = year
        y = per_year.setdefault(year, {"n": 0, "words": 0})
        records.append({
            "title": title,
            "content": content,
            "link": url,
            "collection_name": f"{args.name} {year}",
            "book_slug": f"{args.slug}-{year}",
            "chapter_index": y["n"],
            "location": meta["location"],
            "occasion": meta["occasion"],
            "date": meta["date"],
        })
        y["n"] += 1
        y["words"] += len(content.split())
        if (idx + 1) % 25 == 0 or idx + 1 == len(hrefs):
            log.info("[%d/%d] %s", idx + 1, len(hrefs), title)

    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out_path)

    print(f"\n{'Year':22} {'discourses':>10} {'words':>9}")
    for year in sorted(per_year):
        y = per_year[year]
        print(f"{args.name} {year:7} {y['n']:>10} {y['words']:>9}")
    print(f"{'TOTAL':22} {len(records):>10} {sum(y['words'] for y in per_year.values()):>9}")
    print(f"\nwrote {len(records)} records -> {out_path}")
    if empty:
        sys.exit(f"{empty} discourses had empty content — inspect before using the output")


if __name__ == "__main__":
    main()
