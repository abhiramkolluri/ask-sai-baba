"""Ingest scraped Vahini chapters (webcrawler/vahinis.json) into Weaviate.

Inserts each chapter as an Article and chunks it into Passage objects using the
same chunking as the discourse corpus (chunk_articles.chunk_content). Weaviate
auto-vectorizes on insert via the collections' text2vec-openai vectorizer.

Idempotent and resumable: chapters whose `link` already exists in Article are
skipped; a skipped article with no passages (a previous run died mid-way) gets
its passages backfilled. Every inserted article UUID is appended to
vahini_ingest_log.json for rollback.

Usage:
    source venv/bin/activate
    python ingest_vahinis.py
"""

import os as _os, sys as _sys
# This script lives in a subdirectory but imports the backend's top-level
# modules (search, weaviate_client, …), so put the backend root on sys.path
# before those imports. Keeps the script runnable from anywhere.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))


import argparse
import json
import os
import sys

from dotenv import load_dotenv
from weaviate.classes.query import Filter

load_dotenv()

from weaviate_client import get_client  # noqa: E402
from chunk_articles import chunk_content, build_passage_props, INSERT_BATCH_SIZE, ENC  # noqa: E402
from metadata_norm import derive_article_metadata  # noqa: E402
from search.retrieval import get_embedding  # noqa: E402

# text-embedding-3-large rejects inputs over 8192 tokens. Weaviate vectorizes
# title+content server-side; long chapters exceed that, so for those we embed a
# truncated copy client-side and pass the vector explicitly (which skips the
# module vectorizer). Only the Article vector is affected — the live pipeline
# searches Passage chunks, which are always well under the limit.
EMBED_MAX_TOKENS = 8000

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON = os.path.join(HERE, "..", "webcrawler", "vahinis.json")


def log_path_for(json_path):
    """Per-source rollback log; keeps the original vahini log name."""
    stem = os.path.splitext(os.path.basename(json_path))[0]
    if stem == "vahinis":
        return os.path.join(HERE, "vahini_ingest_log.json")
    return os.path.join(HERE, f"{stem}_ingest_log.json")


def load_records(json_path, expected):
    with open(json_path) as f:
        records = json.load(f)
    assert len(records) == expected, f"expected {expected} records, got {len(records)}"
    for r in records:
        assert r["title"] and r["content"] and r["link"], f"incomplete record: {r.get('link')}"
    return records


def append_log(log_path, entry):
    log = []
    if os.path.exists(log_path):
        with open(log_path) as f:
            log = json.load(f)
    log.append(entry)
    with open(log_path, "w") as f:
        json.dump(log, f, indent=1)


def article_props(rec):
    """Map a scraped record onto the Article schema (drop scraper-only keys).

    Includes the normalized book/volume/chapter_index/year metadata used by
    structured search, so freshly ingested articles never need
    backfill_metadata.py. None values are dropped (Weaviate rejects them).
    """
    meta = derive_article_metadata(
        rec["collection_name"], rec.get("date", ""),
        crawler_chapter_index=rec.get("chapter_index"),
    )
    props = {
        "title": rec["title"],
        "content": rec["content"],
        "location": rec.get("location", ""),
        "occasion": rec.get("occasion", ""),
        "link": rec["link"],
        "collection_name": rec["collection_name"],
        "date": rec.get("date", ""),
    }
    props.update({k: v for k, v in meta.items() if v is not None})
    return props


def passage_source_props(props):
    """Article props plus the date_authored key build_passage_props reads —
    without this, passages lose the date and /search results carry no date."""
    return {**props, "date_authored": props.get("date", "")}


def passage_count(passages_col, article_uuid):
    agg = passages_col.aggregate.over_all(
        filters=Filter.by_property("article_id").equal(str(article_uuid)),
        total_count=True,
    )
    return agg.total_count or 0


def oversized_article_vector(props):
    """Client-side vector for articles too long for server-side vectorization,
    else None. Mimics the module input (title + content), truncated to fit."""
    text = props["title"] + " " + props["content"]
    toks = ENC.encode(text)
    if len(toks) <= EMBED_MAX_TOKENS:
        return None
    vec = get_embedding(ENC.decode(toks[:EMBED_MAX_TOKENS]))
    if not vec:
        raise RuntimeError(f"client-side embedding failed for {props['link']}")
    return vec


def insert_passages(passages_col, article_uuid, props, content):
    passages, _units = chunk_content(content)
    pending = []
    def flush(batch):
        if batch:
            resp = passages_col.data.insert_many(batch)
            if resp.has_errors:
                raise RuntimeError(f"passage insert errors for {props['link']}: {list(resp.errors.values())[:2]}")

    src = passage_source_props(props)
    for idx, p in enumerate(passages):
        pending.append(build_passage_props(article_uuid, src, p["content"], idx))
        if len(pending) >= INSERT_BATCH_SIZE:
            flush(pending)
            pending = []
    flush(pending)
    return len(passages)


def main():
    ap = argparse.ArgumentParser(description="Ingest scraped articles into Weaviate")
    ap.add_argument("json_path", nargs="?", default=DEFAULT_JSON,
                    help="scraped JSON (default: webcrawler/vahinis.json)")
    ap.add_argument("--expect", type=int, default=271,
                    help="expected record count sanity check (default 271)")
    args = ap.parse_args()
    log_path = log_path_for(args.json_path)

    records = load_records(args.json_path, args.expect)
    client = get_client()
    articles_col = client.collections.get("Article")
    passages_col = client.collections.get("Passage")

    # Existing links -> uuid, for idempotent skip/resume.
    existing = {}
    for obj in articles_col.iterator(return_properties=["link"]):
        link = (obj.properties.get("link") or "").strip()
        if link:
            existing[link] = obj.uuid

    inserted_articles = 0
    backfilled = 0
    skipped = 0
    total_passages = 0
    per_book = {}

    for i, rec in enumerate(records, 1):
        props = article_props(rec)
        link = props["link"]
        book = props["collection_name"]

        if link in existing:
            uuid = existing[link]
            if passage_count(passages_col, uuid) == 0:
                n = insert_passages(passages_col, uuid, props, props["content"])
                total_passages += n
                backfilled += 1
                print(f"[{i}/{len(records)}] backfilled {n} passages: {props['title']}")
            else:
                skipped += 1
            continue

        vec = oversized_article_vector(props)
        if vec is not None:
            uuid = articles_col.data.insert(props, vector=vec)
        else:
            uuid = articles_col.data.insert(props)
        append_log(log_path, {"uuid": str(uuid), "link": link, "title": props["title"]})
        n = insert_passages(passages_col, uuid, props, props["content"])
        inserted_articles += 1
        total_passages += n
        per_book[book] = per_book.get(book, {"articles": 0, "passages": 0})
        per_book[book]["articles"] += 1
        per_book[book]["passages"] += n
        if i % 25 == 0 or i == len(records):
            print(f"[{i}/{len(records)}] {book}: {props['title']} ({n} passages)")

    art_total = articles_col.aggregate.over_all(total_count=True).total_count
    pas_total = passages_col.aggregate.over_all(total_count=True).total_count

    print("\n=== Ingest summary ===")
    print(f"Articles inserted:   {inserted_articles}")
    print(f"Passages inserted:   {total_passages}")
    print(f"Backfilled articles: {backfilled}")
    print(f"Skipped (existing):  {skipped}")
    for book, c in sorted(per_book.items()):
        print(f"  {book:35} {c['articles']:3} articles {c['passages']:5} passages")
    print(f"Article total now:   {art_total}")
    print(f"Passage total now:   {pas_total}")

    client.close()


if __name__ == "__main__":
    main()
