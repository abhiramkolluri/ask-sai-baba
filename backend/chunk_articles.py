"""
Rebuild the Passage collection from the Article collection.

Standalone, re-runnable. Each Article's `content` is chunked with a
paragraph-aware strategy and inserted into Passage. Weaviate auto-vectorizes
the passage `content` via the collection's configured text2vec-openai
vectorizer (text-embedding-3-large) — we never compute embeddings here.

Usage:
    source venv/bin/activate
    python chunk_articles.py              # rebuild (wipe Passage first)
    python chunk_articles.py --no-rebuild # append without wiping
"""

import re
import argparse
import statistics

import tiktoken
from dotenv import load_dotenv
from weaviate.classes.query import Filter, MetadataQuery

from weaviate_client import get_client

load_dotenv()

# ---- Chunking config -------------------------------------------------------
CHUNK_TARGET_TOKENS = 300   # aim per passage
CHUNK_OVERLAP_TOKENS = 50   # overlap between consecutive passages
CHUNK_MAX_TOKENS = 500      # hard ceiling before a forced split

INSERT_BATCH_SIZE = 100

# tiktoken encoder. text-embedding-3-large uses cl100k_base; fall back
# explicitly so token counts stay deterministic offline.
try:
    ENC = tiktoken.encoding_for_model("text-embedding-3-large")
except Exception:
    ENC = tiktoken.get_encoding("cl100k_base")


def n_tokens(text: str) -> int:
    return len(ENC.encode(text or ""))


def tail_tokens(text: str, k: int) -> str:
    """Return the last ~k tokens of text, decoded back to a string."""
    toks = ENC.encode(text or "")
    if not toks:
        return ""
    return ENC.decode(toks[-k:]).strip()


def split_paragraphs(content: str):
    """Split on blank lines into non-empty, stripped paragraphs."""
    paras = re.split(r"\n\s*\n", content or "")
    return [p.strip() for p in paras if p.strip()]


def split_sentences(text: str):
    """Split into sentences on terminal punctuation, never mid-sentence."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [s.strip() for s in parts if s.strip()]


def make_units(content: str):
    """
    Produce the atomic units that passages are packed from.

    Normally one unit == one paragraph (paragraphs are never split). A
    paragraph that alone exceeds CHUNK_MAX_TOKENS is split on sentence
    boundaries into smaller units, each <= CHUNK_MAX_TOKENS where possible.
    A single sentence longer than the ceiling is emitted intact (we never
    split mid-sentence).
    """
    units = []
    for para in split_paragraphs(content):
        if n_tokens(para) <= CHUNK_MAX_TOKENS:
            units.append(para)
            continue

        cur, cur_tok = [], 0
        for sent in split_sentences(para):
            st = n_tokens(sent)
            if cur and cur_tok + st > CHUNK_MAX_TOKENS:
                units.append(" ".join(cur))
                cur, cur_tok = [], 0
            cur.append(sent)
            cur_tok += st
            # A lone sentence over the ceiling can't be split further.
            if st > CHUNK_MAX_TOKENS and len(cur) == 1:
                units.append(cur[0])
                cur, cur_tok = [], 0
        if cur:
            units.append(" ".join(cur))
    return units


def chunk_content(content: str):
    """
    Greedily pack units into passages targeting CHUNK_TARGET_TOKENS, seeding
    each passage after the first with ~CHUNK_OVERLAP_TOKENS of tail overlap
    from the previous passage's body.

    Returns a list of dicts: {"content", "body", "overlap"} where
        content = (overlap + "\\n\\n" + body) if overlap else body
        body    = the passage's own (non-overlap) text == joined units
    Concatenating every passage's `body` in order reconstructs the discourse.
    """
    units = make_units(content)
    passages = []
    cur_units, cur_tok = [], 0
    prev_body = None

    def emit():
        nonlocal cur_units, cur_tok, prev_body
        body = "\n\n".join(cur_units).strip()
        if not body:  # never emit empty/whitespace-only passages
            cur_units, cur_tok = [], 0
            return
        overlap = tail_tokens(prev_body, CHUNK_OVERLAP_TOKENS) if prev_body else ""
        content_str = (overlap + "\n\n" + body) if overlap else body
        passages.append({"content": content_str, "body": body, "overlap": overlap})
        prev_body = body
        cur_units, cur_tok = [], 0

    for u in units:
        ut = n_tokens(u)
        if cur_units and cur_tok + ut > CHUNK_TARGET_TOKENS:
            emit()
        cur_units.append(u)
        cur_tok += ut
    emit()
    return passages, units


def wipe_passages(passages_col):
    """Delete every object in Passage so a rebuild is idempotent."""
    total = passages_col.aggregate.over_all(total_count=True).total_count
    if not total:
        print("Passage already empty; nothing to wipe.")
        return
    print(f"Wiping {total} existing Passage objects...")
    # delete_many caps deletions per call, so loop until empty.
    for _ in range(1000):
        remaining = passages_col.aggregate.over_all(total_count=True).total_count
        if remaining == 0:
            break
        passages_col.data.delete_many(
            where=Filter.by_property("chunk_index").greater_or_equal(0)
        )
    print("Passage wiped.")


def build_passage_props(article_uuid, props, passage_text, idx):
    return {
        "content": passage_text,
        "article_id": str(article_uuid),
        "chunk_index": idx,
        "title": props.get("title", ""),
        "link": props.get("link", ""),
        "location": props.get("location", ""),
        "occasion": props.get("occasion", ""),
        "collection_name": props.get("collection_name", ""),
        # date_authored may not exist on Article — never assume it's present.
        "date_authored": props.get("date_authored", ""),
    }


def rebuild(rebuild_flag: bool):
    client = get_client()
    articles_col = client.collections.get("Article")
    passages_col = client.collections.get("Passage")

    if rebuild_flag:
        wipe_passages(passages_col)

    pending = []
    articles_processed = 0
    total_passages = 0
    per_article_counts = []

    def flush():
        nonlocal pending
        if pending:
            passages_col.data.insert_many(pending)
            pending = []

    for obj in articles_col.iterator():
        props = obj.properties or {}
        content = props.get("content", "") or ""
        passages, _units = chunk_content(content)

        for idx, p in enumerate(passages):
            pending.append(build_passage_props(obj.uuid, props, p["content"], idx))
            if len(pending) >= INSERT_BATCH_SIZE:
                flush()

        articles_processed += 1
        total_passages += len(passages)
        per_article_counts.append(len(passages))

    flush()

    print("\n=== Rebuild summary ===")
    print(f"Articles processed:     {articles_processed}")
    print(f"Total passages created: {total_passages}")
    if per_article_counts:
        print(f"Passages/article  min:    {min(per_article_counts)}")
        print(f"Passages/article  median: {statistics.median(per_article_counts)}")
        print(f"Passages/article  max:    {max(per_article_counts)}")

    return client, articles_col, passages_col


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def spot_check(articles_col, passages_col, sample_n=3, near_text_topic="devotion and love for God"):
    print("\n=== Spot-check: reconstruction of 3 articles ===")
    checked = 0
    for obj in articles_col.iterator():
        if checked >= sample_n:
            break
        props = obj.properties or {}
        content = props.get("content", "") or ""
        if not content.strip():
            continue

        passages, units = chunk_content(content)
        if not passages:
            continue

        checked += 1
        title = props.get("title", "Untitled")
        print(f"\n--- Article {checked}: {title!r}  ({len(passages)} passages) ---")
        for p in passages:
            preview = _normalize_ws(p["content"])[:90]
            ov = "yes" if p["overlap"] else "no"
            print(f"  [tokens={n_tokens(p['content']):>3} overlap={ov:>3}] {preview}...")

        reconstructed = "\n\n".join(p["body"] for p in passages)
        original_units = "\n\n".join(units)
        ok = _normalize_ws(reconstructed) == _normalize_ws(original_units)
        print(f"  reconstruction (bodies minus overlap) matches source: {ok}")
        if not ok:
            # Show first divergence point for debugging.
            r, o = _normalize_ws(reconstructed), _normalize_ws(original_units)
            i = next((k for k in range(min(len(r), len(o))) if r[k] != o[k]), min(len(r), len(o)))
            print(f"    DIVERGENCE at char {i}: recon={r[i:i+40]!r} orig={o[i:i+40]!r}")

    print("\n=== Spot-check: near_text query on Passage ===")
    print(f"query: {near_text_topic!r}")
    resp = passages_col.query.near_text(
        query=near_text_topic,
        limit=5,
        return_metadata=MetadataQuery(distance=True),
    )
    for i, o in enumerate(resp.objects, 1):
        sim = 1 - (o.metadata.distance or 0)
        title = o.properties.get("title", "Untitled")
        ci = o.properties.get("chunk_index", "?")
        snippet = _normalize_ws(o.properties.get("content", ""))[:100]
        print(f"  [{i}] sim={sim:.3f} {title!r} #chunk{ci}: {snippet}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild Passage from Article.")
    parser.add_argument(
        "--rebuild",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Wipe Passage before inserting (default: on). Use --no-rebuild to append.",
    )
    args = parser.parse_args()

    client, articles_col, passages_col = rebuild(args.rebuild)
    try:
        spot_check(articles_col, passages_col)
    finally:
        client.close()
