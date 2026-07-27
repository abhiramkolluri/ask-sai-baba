"""Scrape the Sathya Sai Speaks discourses listed on ssssahitya.org that are
missing from Weaviate (the URL list produced by verify_sss_coverage.py) into
sss_ssssahitya.json.

The discourse pages share the exact format scrape_summer_showers already
parses (h1 title, div.ck-content body, "Date/Event/Location:" metadata lines).
There is no volume/chapter structure on this site — SSS is organized by year —
so chapter_index is left empty and ingest groups by the parsed date.

Resumable: records already in the output file are skipped; output is written
incrementally every FLUSH_EVERY records. Pages that fail to fetch/parse are
logged to sss_scrape_failures.json and skipped rather than aborting the run.

Usage:
    source ../backend/venv/bin/activate
    python scrape_sss.py --limit 5   # smoke test
    python scrape_sss.py             # full run (~1,500 pages, polite delays)
"""

import argparse
import json
import logging
import os
import re

from scrape_vahinis import fetch
from scrape_summer_showers import parse_discourse

HERE = os.path.dirname(os.path.abspath(__file__))
LINKS_PATH = os.path.join(HERE, "sss_missing_links.json")
OUT_PATH = os.path.join(HERE, "sss_ssssahitya.json")
FAILURES_PATH = os.path.join(HERE, "sss_scrape_failures.json")
FLUSH_EVERY = 50

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("scrape_sss")


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def flush(records, failures):
    with open(OUT_PATH, "w") as f:
        json.dump(records, f, indent=1)
    with open(FAILURES_PATH, "w") as f:
        json.dump(failures, f, indent=1)


def main():
    ap = argparse.ArgumentParser(description="Scrape missing SSS discourses from ssssahitya.org")
    ap.add_argument("--limit", type=int, default=None, help="stop after N new records (smoke test)")
    args = ap.parse_args()

    links = load_json(LINKS_PATH, None)
    if not links:
        raise SystemExit(f"missing or empty {LINKS_PATH}; run verify_sss_coverage.py first")

    records = load_json(OUT_PATH, [])
    failures = load_json(FAILURES_PATH, [])
    done = {r["link"] for r in records}
    failed_before = {f["link"] for f in failures}

    todo = [l for l in links if l not in done]
    log.info("%d links total, %d already scraped, %d to go", len(links), len(done), len(todo))

    new_count = 0
    for i, link in enumerate(todo, 1):
        if args.limit and new_count >= args.limit:
            break
        try:
            title, content, meta = parse_discourse(fetch(link))
            if not title or not content:
                raise ValueError("empty title or content")
            records.append({
                "title": title,
                "content": content,
                "link": link,
                "collection_name": "Sathya Sai Speaks",
                "book_slug": "sathya-sai-speaks",
                "chapter_index": "",  # site has no in-book order; ingest sorts by date
                "location": meta.get("location", ""),
                "occasion": meta.get("occasion", ""),
                "date": meta.get("date", ""),
            })
            new_count += 1
            if link in failed_before:
                failures = [f for f in failures if f["link"] != link]
        except Exception as e:
            log.warning("FAILED %s: %s", link, e)
            if link not in failed_before:
                failures.append({"link": link, "error": str(e)})
        if new_count and new_count % FLUSH_EVERY == 0:
            flush(records, failures)
            log.info("[%d/%d] flushed %d records (%d failures)", i, len(todo), len(records), len(failures))

    flush(records, failures)

    years = {}
    for r in records:
        m = re.search(r"\b(19|20)\d{2}\b", r.get("date", ""))
        y = m.group(0) if m else "(no date)"
        years[y] = years.get(y, 0) + 1
    print(f"\n=== Scrape summary ===")
    print(f"Records in {os.path.basename(OUT_PATH)}: {len(records)} ({new_count} new this run)")
    print(f"Failures: {len(failures)} (see {os.path.basename(FAILURES_PATH)})")
    no_date = years.get("(no date)", 0)
    print(f"Records without a parseable date: {no_date}")
    print(f"Year span: {min((y for y in years if y != '(no date)'), default='-')}"
          f" - {max((y for y in years if y != '(no date)'), default='-')}")


if __name__ == "__main__":
    main()
