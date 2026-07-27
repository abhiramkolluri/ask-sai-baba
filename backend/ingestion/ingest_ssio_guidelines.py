"""
Ingest the "SSIO Guidelines (July 2017)" PDF into Weaviate.

The 48-page PDF is split into one Article per chapter (collection_name
"SSIO Guidelines"). An Article's own `content` is vectorized by Weaviate, and
text-embedding-3-large caps at ~8k tokens, so any chapter above
MAX_ARTICLE_TOKENS is further split at paragraph boundaries into "(Part k)"
Articles. Each Article is then chunked into Passages via the shared
chunk_articles chunker (Weaviate auto-vectorizes those too).

Idempotent: keyed on collection_name, so a re-run deletes the prior copy
(all Articles + their Passages) before re-inserting.

Source PDF: ~/Downloads/ssio-guidelines.pdf

Usage:
    source venv/bin/activate
    python ingest_ssio_guidelines.py
"""

import os as _os, sys as _sys
# This script lives in a subdirectory but imports the backend's top-level
# modules (search, weaviate_client, …), so put the backend root on sys.path
# before those imports. Keeps the script runnable from anywhere.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))


import os
import re

from dotenv import load_dotenv
from pypdf import PdfReader
from weaviate.classes.query import Filter

from weaviate_client import get_client
from chunk_articles import chunk_content, build_passage_props, n_tokens, split_paragraphs

load_dotenv()

PDF_PATH = os.path.expanduser("~/Downloads/ssio-guidelines.pdf")
COLLECTION_NAME = "SSIO Guidelines"
LINK = ""  # No canonical public URL supplied; identity carried by title/collection.
DATE = "July 2017"

# Below the 8191-token embedding ceiling, with headroom.
MAX_ARTICLE_TOKENS = 6000

# Clean chapter titles (the PDF renders them in garbled small-caps).
CHAPTER_TITLES = {
    1: "Introduction",
    2: "Fundamental Principles from Sathya Sai Baba's Teachings",
    3: "Goal of the Sathya Sai International Organisation",
    4: "Structure of the Sathya Sai International Organisation",
    5: "Unity Within the SSIO",
    6: "Sathya Sai Centres – Programmes and Practices",
    7: "Types of Meetings and Welcoming Newcomers",
    8: "The International Sathya Sai Young Adults",
    9: "Sai Education Initiatives in the Community",
    10: "Legal, Administration, and Trust Matters",
}

# Running header/footer lines, e.g. "16 | SSIO Guidelines, July 2017" and
# "SSIO Guidelines, July 2017 | 17".
_FOOTER = re.compile(r"^\s*(\d+\s*\|\s*)?SSIO Guidelines,?\s*July 2017(\s*\|\s*\S+)?\s*$")
# Standalone "Chapter  6" marker line.
_CHAP_MARK = re.compile(r"^\s*Chapter\s+\d+\s*$")


def clean(segment: str) -> str:
    """Drop header/footer and chapter-marker lines; normalize whitespace."""
    lines = [l for l in segment.split("\n") if not _FOOTER.match(l) and not _CHAP_MARK.match(l)]
    txt = "\n".join(lines)
    txt = re.sub(r"[ \t]{2,}", " ", txt)      # collapse the PDF's big space runs
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def extract_chapters():
    """Return [(chapter_no, cleaned_text), ...] for chapters 1..10."""
    reader = PdfReader(PDF_PATH)
    pages = [p.extract_text() or "" for p in reader.pages]
    body = "\n".join(pages[8:])  # pages 0-7 are cover / rights / TOC

    # Locate each "Chapter N" heading in order (skips stray "[chapter 18]" refs).
    starts, cursor = [], 0
    for n in range(1, 11):
        m = re.search(r"Chapter\s+%d\b" % n, body[cursor:])
        if not m:
            raise RuntimeError(f"Could not locate Chapter {n} heading in PDF body.")
        off = cursor + m.start()
        starts.append(off)
        cursor = off + 1
    starts.append(len(body))

    return [(n, clean(body[starts[i]:starts[i + 1]])) for i, n in enumerate(range(1, 11))]


def split_to_articles(text: str):
    """Greedily pack paragraphs into sub-articles each <= MAX_ARTICLE_TOKENS."""
    if n_tokens(text) <= MAX_ARTICLE_TOKENS:
        return [text]
    parts, cur, cur_tok = [], [], 0
    for para in split_paragraphs(text):
        pt = n_tokens(para)
        if cur and cur_tok + pt > MAX_ARTICLE_TOKENS:
            parts.append("\n\n".join(cur))
            cur, cur_tok = [], 0
        cur.append(para)
        cur_tok += pt
    if cur:
        parts.append("\n\n".join(cur))
    return parts


def delete_existing(client):
    articles = client.collections.get("Article")
    passages = client.collections.get("Passage")
    existing = articles.query.fetch_objects(
        filters=Filter.by_property("collection_name").equal(COLLECTION_NAME), limit=500
    )
    for obj in existing.objects:
        uid = str(obj.uuid)
        passages.data.delete_many(filters=Filter.by_property("article_id").equal(uid))
        articles.data.delete_by_id(obj.uuid)
    if existing.objects:
        print(f"Removed {len(existing.objects)} existing '{COLLECTION_NAME}' Article(s) + passages.")


def main():
    client = get_client()
    try:
        delete_existing(client)
        articles = client.collections.get("Article")
        passages = client.collections.get("Passage")

        total_articles = total_passages = 0
        for chno, text in extract_chapters():
            base_title = f"SSIO Guidelines — Chapter {chno}: {CHAPTER_TITLES[chno]}"
            parts = split_to_articles(text)
            for pi, part in enumerate(parts):
                title = base_title if len(parts) == 1 else f"{base_title} (Part {pi + 1})"
                article = {
                    "title": title,
                    "content": part,
                    "collection_name": COLLECTION_NAME,
                    "link": LINK,
                    "location": "",
                    "occasion": "",
                    "date": DATE,
                }
                uuid = articles.data.insert(article)
                total_articles += 1

                chunks, _ = chunk_content(part)
                rows = [build_passage_props(uuid, article, c["content"], idx)
                        for idx, c in enumerate(chunks)]
                if rows:
                    passages.data.insert_many(rows)
                total_passages += len(rows)
                print(f"  {title}  ->  {n_tokens(part)} tok, {len(rows)} passage(s)")

        print(f"\nDone: {total_articles} Article(s), {total_passages} Passage(s) for '{COLLECTION_NAME}'.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
