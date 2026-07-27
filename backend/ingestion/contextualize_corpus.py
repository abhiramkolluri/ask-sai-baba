"""Contextual Retrieval migration: build Passage_v2 with situated passages.

*** THIS SCRIPT IS NOT MEANT TO BE RUN YET. It writes 27,349 objects to the live
*** Weaviate cluster and spends real money. It is checked in for review first.
*** Run it only with an explicit go-ahead, and only after a green eval baseline.

WHY
Passages are currently embedded standalone. Sai Baba's discourses lean on stories,
pronouns and Sanskrit terms whose referent sits paragraphs away, so a chunk
reading "He then asked the boy to return the following morning" carries almost no
retrievable signal about its actual subject. Anthropic's Contextual Retrieval
prepends a short model-generated context to each chunk before indexing; they
report 49% fewer retrieval failures, and 67% when combined with reranking.

THE CRITICAL DESIGN CONSTRAINT
The live `Passage` collection vectorizes ONLY `content` (everything else is
skip_vectorization). The obvious implementation — prepend the context onto
`content` — would be a correctness bug, not just a style choice: `content` is the
text `ranking._locate_verbatim` quotes from, so generated context would become
eligible to appear inside a "verbatim" quote attributed to Swami. In a
quotes-only product that is the worst possible failure.

So Passage_v2 splits them:
  - `contextualized_content` : context + passage. VECTORIZED. Used for retrieval.
  - `content`                : the original passage, untouched. NOT vectorized.
                               Used for display and for verbatim quote extraction.

Retrieval must also query BM25 against `contextualized_content` — Anthropic's
result depends on contextualizing BOTH halves of the hybrid, not just the vector
side. See the CUTOVER notes at the bottom.

COST
Context is generated per passage but the parent article is prompt-cached, so each
article is paid for once at write rates and then read at ~0.1x. For 2,409
articles / 27,349 passages on Claude Haiku 4.5 this lands around $30-35.

USAGE (once approved)
    venv/bin/python contextualize_corpus.py --dry-run      # 3 articles, prints, writes nothing
    venv/bin/python contextualize_corpus.py --limit 25     # small live batch
    venv/bin/python contextualize_corpus.py                # full run (hours)

Resumable: already-migrated passages are skipped by article_id, so an interrupted
run can simply be restarted.
"""

import os as _os, sys as _sys
# This script lives in a subdirectory but imports the backend's top-level
# modules (search, weaviate_client, …), so put the backend root on sys.path
# before those imports. Keeps the script runnable from anywhere.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_HERE)


def _here(name):
    """A committed fixture that lives beside this script."""
    return _os.path.join(_HERE, name)


def _artifact(name):
    """A generated run output. Kept out of the source tree in artifacts/."""
    d = _os.path.join(_ROOT, "artifacts")
    _os.makedirs(d, exist_ok=True)
    return _os.path.join(d, name)


import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

import weaviate.classes.config as wcd  # noqa: E402
from weaviate.classes.query import Filter  # noqa: E402
from weaviate_client import get_client  # noqa: E402

SOURCE_COLLECTION = "Passage"
TARGET_COLLECTION = "Passage_v2"
CONTEXT_MODEL = "claude-haiku-4-5"
PROGRESS_FILE = _artifact("contextualize_progress.json")

# Keep the generated context short: it exists to make the passage findable, not
# to summarize it. Long context dilutes the passage's own terms in the embedding.
CONTEXT_MAX_TOKENS = 120

SYSTEM = (
    "You situate an excerpt within the discourse it came from, so a search engine "
    "can find it. Sathya Sai Baba's discourses use stories, pronouns, and Sanskrit "
    "terms whose referent may appear far earlier in the text.\n\n"
    "Write ONE or TWO sentences that state: which discourse and collection this is "
    "from, and what this specific excerpt is about — naming the people, places, "
    "scripture, festival, or concept it refers to, especially where the excerpt "
    "itself only uses a pronoun or an unexplained term.\n\n"
    "Write plain declarative context. Do NOT quote the excerpt, do not add "
    "commentary or interpretation, and do not begin with 'This excerpt' or "
    "'This passage'."
)


def _anthropic_client():
    """Constructed lazily so --help and a review read work without a key."""
    try:
        import anthropic
    except ImportError:
        sys.exit("anthropic SDK not installed. Add `anthropic` to requirements.txt first.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set.")
    return anthropic.Anthropic(max_retries=3, timeout=30.0)


def ensure_target(client):
    """Create Passage_v2 if absent. Mirrors Passage, with the split content fields."""
    if client.collections.exists(TARGET_COLLECTION):
        return
    client.collections.create(
        name=TARGET_COLLECTION,
        vectorizer_config=wcd.Configure.Vectorizer.text2vec_openai(
            model="text-embedding-3-large"
        ),
        properties=[
            # The ONLY vectorized property: context + passage.
            wcd.Property(name="contextualized_content", data_type=wcd.DataType.TEXT),
            # The original passage, verbatim. Never vectorized, never modified —
            # this is what quotes are extracted from.
            wcd.Property(name="content", data_type=wcd.DataType.TEXT, skip_vectorization=True),
            # The generated context on its own, kept for auditing what the model wrote.
            wcd.Property(name="context", data_type=wcd.DataType.TEXT, skip_vectorization=True),
            wcd.Property(name="article_id", data_type=wcd.DataType.TEXT, skip_vectorization=True),
            wcd.Property(name="chunk_index", data_type=wcd.DataType.INT, skip_vectorization=True),
            wcd.Property(name="title", data_type=wcd.DataType.TEXT, skip_vectorization=True),
            wcd.Property(name="link", data_type=wcd.DataType.TEXT, skip_vectorization=True),
            wcd.Property(name="location", data_type=wcd.DataType.TEXT, skip_vectorization=True),
            wcd.Property(name="occasion", data_type=wcd.DataType.TEXT, skip_vectorization=True),
            wcd.Property(name="collection_name", data_type=wcd.DataType.TEXT, skip_vectorization=True),
            wcd.Property(name="date_authored", data_type=wcd.DataType.TEXT, skip_vectorization=True),
        ],
    )
    print(f"Created collection '{TARGET_COLLECTION}'")


def load_passages_by_article(client):
    """Every source passage, grouped by article and ordered by chunk_index."""
    col = client.collections.get(SOURCE_COLLECTION)
    by_article = defaultdict(list)
    for obj in col.iterator():
        p = obj.properties
        by_article[p.get("article_id", "")].append({
            "uuid": str(obj.uuid),
            "content": p.get("content", "") or "",
            "chunk_index": p.get("chunk_index", 0) or 0,
            "title": p.get("title", "") or "",
            "link": p.get("link", "") or "",
            "location": p.get("location", "") or "",
            "occasion": p.get("occasion", "") or "",
            "collection_name": p.get("collection_name", "") or "",
            "date_authored": p.get("date_authored", "") or "",
        })
    for chunks in by_article.values():
        chunks.sort(key=lambda c: c["chunk_index"])
    return by_article


def contextualize_article(anthropic_client, chunks):
    """Generate context for every chunk of one article.

    The full article is sent once as a cached prefix and re-read per chunk, so an
    11-chunk article costs one cache write plus 11 cheap reads instead of 11 full
    re-reads of the same text. That caching is what makes this affordable.
    """
    article_text = "\n\n".join(c["content"] for c in chunks)
    meta = chunks[0]
    header = (
        f"Discourse: {meta['title']}\n"
        f"Collection: {meta['collection_name']}\n"
        f"Occasion: {meta['occasion'] or 'n/a'}\n"
        f"Location: {meta['location'] or 'n/a'}\n\n"
        f"Full discourse text:\n{article_text}"
    )

    out = []
    for c in chunks:
        try:
            resp = anthropic_client.messages.create(
                model=CONTEXT_MODEL,
                max_tokens=CONTEXT_MAX_TOKENS,
                system=[{
                    "type": "text",
                    "text": SYSTEM,
                }, {
                    "type": "text",
                    "text": header,
                    # The article is the same for every chunk — cache it so only
                    # the per-chunk tail is billed at full rate.
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{
                    "role": "user",
                    "content": f"Situate this excerpt:\n\n{c['content']}",
                }],
            )
            ctx = "".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception as e:
            logging.error(f"context generation failed for {meta['title']!r} chunk {c['chunk_index']}: {e}")
            # Fall back to deterministic metadata context rather than dropping the
            # passage — a passage with weak context still beats a missing one.
            ctx = f"From the discourse '{meta['title']}' in {meta['collection_name']}."
        out.append((c, ctx))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Process 3 articles, print the generated context, write nothing.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Only process this many articles (0 = all).")
    args = ap.parse_args()

    client = get_client()
    print(f"Loading passages from '{SOURCE_COLLECTION}'...")
    by_article = load_passages_by_article(client)
    total_passages = sum(len(v) for v in by_article.values())
    print(f"  {len(by_article)} articles / {total_passages} passages")

    if args.dry_run:
        anthropic_client = _anthropic_client()
        for aid, chunks in list(by_article.items())[:3]:
            print(f"\n--- {chunks[0]['title']} ({len(chunks)} chunks) ---")
            for c, ctx in contextualize_article(anthropic_client, chunks[:2]):
                print(f"  [chunk {c['chunk_index']}] context: {ctx}")
                print(f"      passage starts: {c['content'][:90]}...")
        client.close()
        print("\nDRY RUN — nothing written.")
        return

    ensure_target(client)
    target = client.collections.get(TARGET_COLLECTION)

    done = set()
    if os.path.exists(PROGRESS_FILE):
        done = set(json.load(open(PROGRESS_FILE)))
        print(f"  resuming: {len(done)} articles already migrated")

    anthropic_client = _anthropic_client()
    todo = [(a, c) for a, c in by_article.items() if a not in done]
    if args.limit:
        todo = todo[:args.limit]

    started = time.time()
    for i, (aid, chunks) in enumerate(todo, 1):
        pairs = contextualize_article(anthropic_client, chunks)
        with target.batch.dynamic() as batch:
            for c, ctx in pairs:
                batch.add_object(properties={
                    "contextualized_content": f"{ctx}\n\n{c['content']}",
                    "content": c["content"],   # untouched — quotes come from here
                    "context": ctx,
                    "article_id": aid,
                    "chunk_index": c["chunk_index"],
                    "title": c["title"],
                    "link": c["link"],
                    "location": c["location"],
                    "occasion": c["occasion"],
                    "collection_name": c["collection_name"],
                    "date_authored": c["date_authored"],
                })
        if target.batch.failed_objects:
            logging.error(f"{len(target.batch.failed_objects)} objects failed for article {aid}")
        done.add(aid)
        json.dump(sorted(done), open(PROGRESS_FILE, "w"))
        if i % 25 == 0 or i == len(todo):
            rate = i / max(time.time() - started, 1e-9)
            print(f"  {i}/{len(todo)} articles  ({rate*60:.1f}/min)")

    client.close()
    print(f"\nDone. {len(done)} articles migrated into {TARGET_COLLECTION}.")
    print(
        "\nCUTOVER (do NOT do this automatically):\n"
        "  1. search/retrieval.py::search_passages — change query_properties to\n"
        "     [\"contextualized_content\", \"title\"] so BM25 sees the context too.\n"
        "     Contextualizing only the vector half leaves most of the gain on the table.\n"
        "  2. search/config.py — PASSAGE_COLLECTION = \"Passage_v2\".\n"
        "  3. Run eval_ragas.py against the golden set and compare to the baseline.\n"
        "  4. Roll back by reverting both lines; the original Passage collection is\n"
        "     never modified by this script.\n"
        "  Note: aggregate_to_discourses reads `content`, which is the ORIGINAL\n"
        "  passage text, so quotes stay verbatim after cutover."
    )


if __name__ == "__main__":
    main()
