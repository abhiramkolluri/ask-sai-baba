"""Merge-enrich ingest of the ssssahitya.org Sathya Sai Speaks scrape
(webcrawler/sss_ssssahitya.json) into Weaviate.

The corpus already holds 518 SSS discourses from saispeaks.sathyasai.org —
kept intact because user highlights and /blog links reference them. Each
scraped record is title-matched (stopword-stripped slug, metadata_norm.slug_key)
against those existing articles:

  - MATCHED   -> metadata-only enrichment of the existing article: fill date /
                 year / occasion / location ONLY where currently empty (the old
                 corpus has no dates), plus date_authored/year on its passages.
                 No content or vector changes.
  - UNMATCHED -> fresh Article + chunked Passage ingest (same helpers as
                 ingest_vahinis), collection_name "Sathya Sai Speaks", year
                 from the parsed date, no volume/chapter (site has none).
  - AMBIGUOUS -> a slug key shared by several site records or several existing
                 articles is never auto-merged; logged to sss_ambiguous.json
                 for review and SKIPPED entirely.

Idempotent: inserts are skipped when the link already exists; enrichment
re-runs are no-ops once fields are filled. Inserted UUIDs are appended to
sss_ingest_log.json for rollback.

Usage:
    source venv/bin/activate
    python ingest_sss.py --dry-run
    python ingest_sss.py
"""

import os as _os, sys as _sys
# This script lives in a subdirectory but imports the backend's top-level
# modules (search, weaviate_client, …), so put the backend root on sys.path
# before those imports. Keeps the script runnable from anywhere.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))


import argparse
import json
import os
import time
from collections import Counter

from dotenv import load_dotenv

load_dotenv()

from weaviate_client import get_client  # noqa: E402
from weaviate.classes.query import Filter  # noqa: E402
from metadata_norm import slug_key, extract_year  # noqa: E402
from ingest_vahinis import (  # noqa: E402
    article_props,
    passage_count,
    oversized_article_vector,
    insert_passages,
    append_log,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SCRAPE_PATH = os.path.join(HERE, "..", "webcrawler", "sss_ssssahitya.json")
LOG_PATH = os.path.join(HERE, "sss_ingest_log.json")
AMBIGUOUS_PATH = os.path.join(HERE, "sss_ambiguous.json")

SSS_BOOK = "Sathya Sai Speaks"

# Some site pages have no transcript, just this banner (sometimes with a short
# note). Skip them unless a substantive excerpt accompanies the banner.
_PLACEHOLDER_MARK = "TRANSCRIPT OF THIS DIVINE DISCOURSE IS UNAVAILABLE"


def is_placeholder(content):
    if _PLACEHOLDER_MARK not in (content or ""):
        return False
    import re
    body = re.sub(r"SAI RAM[^\n]*UNAVAILABLE AT THIS POINT\.?", "", content).strip()
    return len(body) < 400


def build_existing_sss_map(articles_col):
    """slug_key -> [article info] for every existing SSS article, plus the
    set of links already in the whole Article collection (insert idempotency)."""
    by_key = {}
    all_links = set()
    for obj in articles_col.iterator(
        return_properties=["title", "book", "link", "date", "occasion", "location", "year"]
    ):
        props = obj.properties or {}
        link = (props.get("link") or "").strip()
        if link:
            all_links.add(link)
        if (props.get("book") or "") != SSS_BOOK:
            continue
        key = slug_key(props.get("title") or "")
        if key:
            by_key.setdefault(key, []).append({"uuid": obj.uuid, "props": props})
    return by_key, all_links


def insert_passages_with_retry(passages_col, article_uuid, props, content, attempts=3):
    """insert_passages, retried on transient cluster errors ('no healthy
    upstream'). A failed attempt may have flushed some batches, so the
    article's passages are wiped before each retry — retrying can't duplicate."""
    for attempt in range(attempts):
        try:
            return insert_passages(passages_col, article_uuid, props, content)
        except Exception as e:
            if attempt == attempts - 1:
                raise
            wait = 20 * (attempt + 1)
            print(f"  passage insert failed ({e}); cleaning up and retrying in {wait}s")
            time.sleep(wait)
            passages_col.data.delete_many(
                where=Filter.by_property("article_id").equal(str(article_uuid))
            )


def enrichment_delta(existing_props, rec):
    """Fields to fill on the existing article — empty/absent fields only,
    never overwriting anything already present."""
    delta = {}
    for src_key, dst_key in (("date", "date"), ("occasion", "occasion"), ("location", "location")):
        new_val = (rec.get(src_key) or "").strip()
        if new_val and not (existing_props.get(dst_key) or "").strip():
            delta[dst_key] = new_val
    if rec.get("date") and existing_props.get("year") is None:
        year = extract_year(rec["date"])
        if year:
            delta["year"] = year
    return delta


def enrich_passages(passages_col, article_uuid, delta, dry_run):
    """Propagate the enriched date/year onto the article's passages."""
    p_delta = {}
    if "date" in delta:
        p_delta["date_authored"] = delta["date"]
    if "year" in delta:
        p_delta["year"] = delta["year"]
    if not p_delta:
        return 0
    n = 0
    response = passages_col.query.fetch_objects(
        filters=Filter.by_property("article_id").equal(str(article_uuid)),
        limit=200,
        return_properties=["article_id"],
    )
    for obj in response.objects:
        if not dry_run:
            passages_col.data.update(uuid=obj.uuid, properties=p_delta)
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description="Merge-enrich ingest of scraped SSS discourses")
    ap.add_argument("--dry-run", action="store_true", help="report the plan without writing")
    ap.add_argument("--limit", type=int, default=None, help="process at most N records (testing)")
    args = ap.parse_args()

    with open(SCRAPE_PATH) as f:
        records = json.load(f)
    print(f"Scraped records: {len(records)}")

    # Site-side ambiguity: several site records collapsing to one slug key.
    site_key_counts = Counter(slug_key(r["title"]) for r in records)

    client = get_client()
    try:
        articles_col = client.collections.get("Article")
        passages_col = client.collections.get("Passage")
        by_key, all_links = build_existing_sss_map(articles_col)

        enriched = enriched_passages = inserted = skipped_existing = 0
        unchanged_matches = skipped_placeholder = 0
        ambiguous = []
        inserted_passages = 0
        year_counts = Counter()
        vector_checked = False

        for i, rec in enumerate(records, 1):
            if args.limit and i > args.limit:
                break
            link = rec["link"]
            if link in all_links:
                skipped_existing += 1
                continue
            if is_placeholder(rec.get("content")):
                skipped_placeholder += 1
                continue

            key = slug_key(rec["title"])
            matches = by_key.get(key, [])

            if matches and (len(matches) > 1 or site_key_counts[key] > 1):
                ambiguous.append({
                    "link": link,
                    "title": rec["title"],
                    "existing": [str(m["uuid"]) for m in matches],
                    "site_records_with_key": site_key_counts[key],
                })
                continue

            if matches:
                m = matches[0]
                delta = enrichment_delta(m["props"], rec)
                if not delta:
                    unchanged_matches += 1
                    continue
                if not args.dry_run:
                    if not vector_checked:
                        before = articles_col.query.fetch_object_by_id(m["uuid"], include_vector=True)
                        articles_col.data.update(uuid=m["uuid"], properties=delta)
                        after = articles_col.query.fetch_object_by_id(m["uuid"], include_vector=True)
                        if before.vector != after.vector:
                            raise RuntimeError("vector changed on metadata enrichment — aborting")
                        print("  vector-immutability check passed")
                        vector_checked = True
                    else:
                        articles_col.data.update(uuid=m["uuid"], properties=delta)
                enriched += 1
                enriched_passages += enrich_passages(passages_col, m["uuid"], delta, args.dry_run)
            else:
                year = extract_year(rec.get("date", ""))
                year_counts[year or 0] += 1
                # Built even in dry-run so schema-mapping errors surface before
                # anything touches the live cluster.
                props = article_props(rec)
                if not args.dry_run:
                    vec = oversized_article_vector(props)
                    uuid = (articles_col.data.insert(props, vector=vec)
                            if vec is not None else articles_col.data.insert(props))
                    append_log(LOG_PATH, {"uuid": str(uuid), "link": link, "title": props["title"]})
                    inserted_passages += insert_passages_with_retry(passages_col, uuid, props, props["content"])
                inserted += 1

            if i % 100 == 0:
                print(f"[{i}/{len(records)}] enriched={enriched} inserted={inserted}")

        with open(AMBIGUOUS_PATH, "w") as f:
            json.dump(ambiguous, f, indent=1)

        mode = "DRY RUN — nothing written" if args.dry_run else "applied"
        print(f"\n=== Merge-enrich summary ({mode}) ===")
        print(f"Enriched existing articles:      {enriched} (+{enriched_passages} passages)")
        print(f"Matched but nothing to fill:     {unchanged_matches}")
        print(f"Inserted new articles:           {inserted}"
              + (f" (+{inserted_passages} passages)" if not args.dry_run else ""))
        print(f"Skipped (link already ingested): {skipped_existing}")
        print(f"Skipped (no-transcript page):    {skipped_placeholder}")
        print(f"Ambiguous (review, skipped):     {len(ambiguous)} -> {os.path.basename(AMBIGUOUS_PATH)}")
        if year_counts:
            no_year = year_counts.pop(0, 0)
            years = sorted(year_counts)
            print(f"New inserts span {years[0] if years else '-'}–{years[-1] if years else '-'}; "
                  f"{no_year} without a parseable year")
    finally:
        client.close()


if __name__ == "__main__":
    main()
