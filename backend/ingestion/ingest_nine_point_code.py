"""
Ingest the "Nine Point Code of Conduct" reference document into Weaviate.

Standalone and idempotent — keyed on the source PDF `link`, so re-running
deletes any prior copy (Article + its Passages) before re-inserting. The
Article's content is chunked with the shared paragraph-aware chunker from
chunk_articles.py and Weaviate auto-vectorizes each Passage.

Source: https://sathyasai.ca/wp-content/uploads/2019/02/9-Point-Code-of-Conduct.pdf

Usage:
    source venv/bin/activate
    python ingest_nine_point_code.py
"""

import os as _os, sys as _sys
# This script lives in a subdirectory but imports the backend's top-level
# modules (search, weaviate_client, …), so put the backend root on sys.path
# before those imports. Keeps the script runnable from anywhere.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))


from dotenv import load_dotenv
from weaviate.classes.query import Filter

from weaviate_client import get_client
from chunk_articles import chunk_content, build_passage_props

load_dotenv()

LINK = "https://sathyasai.ca/wp-content/uploads/2019/02/9-Point-Code-of-Conduct.pdf"

ARTICLE = {
    "title": "Nine Point Code of Conduct",
    "collection_name": "Nine Point Code of Conduct",
    "link": LINK,
    "location": "",
    "occasion": "",
    "date": "",
    # Cleaned from the source PDF (line-break artifacts normalized). Blank lines
    # delimit paragraphs so the chunker treats intro + list sensibly.
    "content": (
        "Nine Point Code of Conduct\n\n"
        "Bhagawan Sri Sathya Sai Baba has given us the Nine Point Code of Conduct "
        "for our spiritual and personal development.\n\n"
        "1. Daily meditation and prayer (according to one's own religious practice).\n"
        "2. Devotional singing / prayer with members of the family once a week.\n"
        "3. Participation in Sai Spiritual Education (Bal Vikas Program) by children of the family.\n"
        "4. Participation in community service and other programs of the Organization.\n"
        "5. Attendance at least once a month in group devotional singing organized by the Sai Centre.\n"
        "6. Regular study of Sai literature and literature of all the great religions and saints.\n"
        "7. Speaking softly and lovingly with everyone.\n"
        "8. Not indulging in talking ill of others, especially in their absence.\n"
        "9. Putting into practice the principle of 'Ceiling on Desires' and striving "
        "continuously to eliminate the tendency to waste time, money, food and energy "
        "– and utilizing any savings thereby generated for the service of mankind."
    ),
}


def delete_existing(client):
    """Remove any prior copy of this document so re-runs don't duplicate."""
    articles = client.collections.get("Article")
    passages = client.collections.get("Passage")

    existing = articles.query.fetch_objects(
        filters=Filter.by_property("link").equal(LINK), limit=100
    )
    for obj in existing.objects:
        uid = str(obj.uuid)
        passages.data.delete_many(filters=Filter.by_property("article_id").equal(uid))
        articles.data.delete_by_id(obj.uuid)
        print(f"Removed existing Article {uid} and its passages.")


def main():
    client = get_client()
    try:
        delete_existing(client)

        articles = client.collections.get("Article")
        passages = client.collections.get("Passage")

        article_uuid = articles.data.insert(ARTICLE)
        print(f"Inserted Article {article_uuid}: {ARTICLE['title']!r}")

        chunks, _units = chunk_content(ARTICLE["content"])
        rows = [
            build_passage_props(article_uuid, ARTICLE, c["content"], idx)
            for idx, c in enumerate(chunks)
        ]
        if rows:
            passages.data.insert_many(rows)
        print(f"Inserted {len(rows)} Passage chunk(s).")

        for idx, c in enumerate(chunks):
            print(f"  [chunk {idx}] {c['content'][:80]!r}...")
    finally:
        client.close()


if __name__ == "__main__":
    main()
