"""Backfill normalized metadata (book/volume/chapter_index/year) onto every
Article and Passage already in Weaviate.

Sources, in order of precedence:
  - chapter_index: the scraped webcrawler/*.json corpora (matched by link;
    0-based), falling back to the 'Disc. N' number inside SSS collection_names.
  - year: parsed from the article's `date` text, falling back to a trailing
    year in the collection_name ('Summer Showers 1976').
  - book/volume: normalized from collection_name (see metadata_norm).

Only skip_vectorization properties are written, so no re-embedding occurs; the
first real article update verifies the vector is unchanged before the bulk run.
Idempotent: objects whose stored props already match are skipped, so re-runs
are cheap and interrupted runs resume where they left off.

Usage:
    source venv/bin/activate
    python backfill_metadata.py --dry-run          # report, write nothing
    python backfill_metadata.py                    # articles then passages
    python backfill_metadata.py --only articles
    python backfill_metadata.py --only passages
"""

import os as _os, sys as _sys
# This script lives in a subdirectory but imports the backend's top-level
# modules (search, weaviate_client, …), so put the backend root on sys.path
# before those imports. Keeps the script runnable from anywhere.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))


import argparse
import glob
import json
import os

from dotenv import load_dotenv

load_dotenv()

from weaviate_client import get_client  # noqa: E402
from metadata_norm import derive_article_metadata  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER_GLOB = os.path.join(HERE, "..", "webcrawler", "*.json")

META_KEYS = ("book", "volume", "chapter_index", "year")


def load_crawler_chapter_map():
    """link -> 0-based chapter_index across every scraped corpus. Corpora that
    fail to parse (e.g. the stale output.json) are skipped with a warning."""
    chapter_by_link = {}
    for path in sorted(glob.glob(CRAWLER_GLOB)):
        try:
            with open(path) as f:
                records = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  skipping {os.path.basename(path)}: {e}")
            continue
        for rec in records:
            link = (rec.get("link") or "").strip()
            idx = rec.get("chapter_index")
            if link and idx is not None and str(idx).isdigit():
                chapter_by_link[link] = int(idx)
    return chapter_by_link


def desired_props(props, chapter_by_link):
    link = (props.get("link") or "").strip()
    return derive_article_metadata(
        props.get("collection_name", ""),
        props.get("date", ""),
        crawler_chapter_index=chapter_by_link.get(link),
    )


def changed_subset(current, desired):
    """The desired key/values that differ from what is stored (None values are
    never written — Weaviate has no stored value to reset)."""
    return {
        k: v for k, v in desired.items()
        if v is not None and current.get(k) != v
    }


def verify_vector_unchanged(collection, uuid, update_props):
    """Update one object and assert its vector did not change (metadata props
    are skip_vectorization, so it must not). Aborts the run if it did."""
    before = collection.query.fetch_object_by_id(uuid, include_vector=True)
    collection.data.update(uuid=uuid, properties=update_props)
    after = collection.query.fetch_object_by_id(uuid, include_vector=True)
    if before.vector != after.vector:
        raise RuntimeError(
            f"Vector changed after metadata update on {uuid} — aborting. "
            "Check that the new properties are skip_vectorization."
        )
    print("  vector-immutability check passed on first updated article")


def backfill_articles(client, chapter_by_link, dry_run):
    articles = client.collections.get("Article")
    updated = skipped = 0
    counts = {k: 0 for k in META_KEYS}
    verified = False
    meta_by_article = {}  # uuid(str) -> derived metadata, for the passage pass

    for obj in articles.iterator():
        props = obj.properties or {}
        desired = desired_props(props, chapter_by_link)
        meta_by_article[str(obj.uuid)] = desired
        delta = changed_subset(props, desired)
        if not delta:
            skipped += 1
            continue
        for k in delta:
            counts[k] += 1
        if not dry_run:
            if not verified:
                verify_vector_unchanged(articles, obj.uuid, delta)
                verified = True
            else:
                articles.data.update(uuid=obj.uuid, properties=delta)
        updated += 1
        if updated % 200 == 0:
            print(f"  articles updated: {updated}")

    print(f"Articles: {updated} updated, {skipped} already current"
          + (" (dry run — nothing written)" if dry_run else ""))
    for k in META_KEYS:
        print(f"  set {k:14} on {counts[k]} articles")
    return meta_by_article


def backfill_passages(client, meta_by_article, dry_run):
    passages = client.collections.get("Passage")
    updated = skipped = orphaned = 0

    for obj in passages.iterator():
        props = obj.properties or {}
        desired = meta_by_article.get(props.get("article_id", ""))
        if desired is None:
            orphaned += 1
            continue
        delta = changed_subset(props, desired)
        if not delta:
            skipped += 1
            continue
        if not dry_run:
            passages.data.update(uuid=obj.uuid, properties=delta)
        updated += 1
        if updated % 500 == 0:
            print(f"  passages updated: {updated}")

    print(f"Passages: {updated} updated, {skipped} already current, "
          f"{orphaned} orphaned (article_id not found)"
          + (" (dry run — nothing written)" if dry_run else ""))


def main():
    ap = argparse.ArgumentParser(description="Backfill book/volume/chapter_index/year metadata")
    ap.add_argument("--dry-run", action="store_true", help="report changes without writing")
    ap.add_argument("--only", choices=["articles", "passages"], help="limit to one collection")
    args = ap.parse_args()

    print("Loading crawler chapter map...")
    chapter_by_link = load_crawler_chapter_map()
    print(f"  {len(chapter_by_link)} links with chapter_index")

    client = get_client()
    try:
        # Ensure the new properties exist before writing to them.
        from weaviate_client import init_schema
        init_schema()

        # The passage pass joins on the article derivation, so articles are
        # always derived; --only passages just skips writing them.
        meta_by_article = backfill_articles(
            client, chapter_by_link, dry_run=args.dry_run or args.only == "passages"
        )
        if args.only != "articles":
            backfill_passages(client, meta_by_article, dry_run=args.dry_run)
    finally:
        client.close()


if __name__ == "__main__":
    main()
