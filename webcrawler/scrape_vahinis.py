"""Scrape all Vahini books from ssssahitya.org into vahinis.json.

Standalone, re-runnable. Walks /vahinis -> each book page -> each chapter page
(all server-rendered HTML) and writes one JSON list of chapter records whose
keys mirror the Weaviate Article schema, so a later ingest step can insert them
directly and chunk with backend/chunk_articles.py (paragraphs are joined with
blank lines to match its split_paragraphs()).

Usage:
    source ../backend/venv/bin/activate
    python scrape_vahinis.py            # writes vahinis.json next to this file
"""

import json
import logging
import os
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.ssssahitya.org"
INDEX_URL = f"{BASE_URL}/vahinis"
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vahinis.json")

REQUEST_DELAY_S = 0.5
RETRIES = 3
TIMEOUT_S = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("scrape_vahinis")


def fetch(url: str) -> str:
    """GET with politeness delay and retry/backoff on 5xx/timeouts."""
    last_err = None
    for attempt in range(1, RETRIES + 1):
        time.sleep(REQUEST_DELAY_S)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_S)
            if resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code} from {url}")
            resp.raise_for_status()
            return resp.text
        except (requests.RequestException, requests.HTTPError) as e:
            last_err = e
            wait = 2 ** attempt
            log.warning("attempt %d/%d failed for %s: %s (retrying in %ds)",
                        attempt, RETRIES, url, e, wait)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}: {last_err}")


def ordered_unique(items):
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def book_links(index_html: str):
    """(slug, href) pairs for every book on the /vahinis index, in page order."""
    soup = BeautifulSoup(index_html, "html.parser")
    hrefs = [
        a["href"] for a in soup.find_all("a", href=True)
        # exactly /vahinis/<slug> — chapter pages are one level deeper
        if a["href"].startswith("/vahinis/") and a["href"].count("/") == 2
    ]
    return [(h.rsplit("/", 1)[1], h) for h in ordered_unique(hrefs)]


def chapter_hrefs(book_html: str, book_href: str):
    """Chapter hrefs on a book page, in page (reading) order."""
    soup = BeautifulSoup(book_html, "html.parser")
    prefix = book_href + "/"
    return ordered_unique(
        a["href"] for a in soup.find_all("a", href=True)
        # the site has at least one literal ".../null" href (broken CMS entry)
        if a["href"].startswith(prefix) and not a["href"].endswith("/null")
    )


def page_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    return h1.get_text(" ", strip=True) if h1 else ""


def chapter_content(chapter_html: str) -> tuple:
    """(title, content) from a chapter page.

    The chapter title is the first <h2> in <main> (the <h1> is the book title).
    The body is the <p> paragraphs inside the div.ck-content container, which
    holds the article text and excludes breadcrumbs/nav.
    """
    soup = BeautifulSoup(chapter_html, "html.parser")
    main = soup.find("main") or soup
    h2 = main.find("h2")
    title = h2.get_text(" ", strip=True) if h2 else page_title(main)
    body = main.find("div", class_="ck-content") or main
    paras = [p.get_text(" ", strip=True) for p in body.find_all("p")]
    paras = [p for p in paras if p]
    return title, "\n\n".join(paras)


def main():
    records = []
    log.info("fetching book index %s", INDEX_URL)
    books = book_links(fetch(INDEX_URL))
    log.info("found %d books", len(books))
    if not books:
        sys.exit("no books found on the index page — site layout may have changed")

    summary = []
    for slug, book_href in books:
        book_html = fetch(urljoin(BASE_URL, book_href))
        book_title = page_title(BeautifulSoup(book_html, "html.parser")) or slug
        chapters = chapter_hrefs(book_html, book_href)
        log.info("%s (%s): %d chapters", book_title, slug, len(chapters))

        empty = 0
        for idx, href in enumerate(chapters):
            url = urljoin(BASE_URL, href)
            title, content = chapter_content(fetch(url))
            # the server occasionally returns a "Loading..." shell; refetch
            for _ in range(2):
                if content and title != "Loading...":
                    break
                log.warning("got loading shell for %s; refetching", url)
                title, content = chapter_content(fetch(url))
            if not content:
                empty += 1
                log.error("EMPTY content: %s", url)
            records.append({
                "title": title,
                "content": content,
                "link": url,
                "collection_name": book_title,
                "book_slug": slug,
                "chapter_index": idx,
                "location": "",
                "occasion": "",
                "date": "",
            })
        words = sum(len(r["content"].split()) for r in records if r["book_slug"] == slug)
        summary.append((book_title, len(chapters), words, empty))

    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT_PATH)

    print(f"\n{'Book':45} {'chapters':>8} {'words':>9} {'empty':>6}")
    for title, n, words, empty in summary:
        print(f"{title:45} {n:>8} {words:>9} {empty:>6}")
    total_ch = sum(s[1] for s in summary)
    total_w = sum(s[2] for s in summary)
    total_e = sum(s[3] for s in summary)
    print(f"{'TOTAL':45} {total_ch:>8} {total_w:>9} {total_e:>6}")
    print(f"\nwrote {len(records)} records -> {OUT_PATH}")
    if total_e:
        sys.exit(f"{total_e} chapters had empty content — inspect before using the output")


if __name__ == "__main__":
    main()
