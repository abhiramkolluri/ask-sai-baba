"""Re-embed the passage corpus into Passage_v2 with Cohere embed-v4 (Phase 3).

One-time, deliberately-triggered migration — NOT part of init_schema (a missing
text2vec-cohere module would otherwise break backend startup). Run it explicitly:

    venv/bin/python reembed_corpus.py            # dry-run: report counts, don't write
    venv/bin/python reembed_corpus.py --run      # create Passage_v2 + copy passages

It copies every object from the existing `Passage` collection into a new
`Passage_v2` collection whose only difference is the vectorizer (Cohere embed-v4
instead of OpenAI text-embedding-3-large). Copying (rather than re-chunking from
Article) guarantees identical chunk boundaries, so the A/B measures the embedding
change alone. Weaviate auto-embeds each inserted object with Cohere.

Prerequisites:
  - COHERE_API_KEY set (already used for reranking).
  - The **text2vec-cohere module must be enabled on the Weaviate Cloud cluster**
    (a cluster config/ops step). If it isn't, collection creation fails with a
    clear error — enable the module and re-run.

After it completes, run the eval harness (eval_ragas.py) A/B and only then flip
search/config.py::PASSAGE_COLLECTION to "Passage_v2".
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

from weaviate_client import get_client  # noqa: E402
import weaviate.classes.config as wcd  # noqa: E402

SRC = "Passage"
DST = "Passage_v2"
COHERE_EMBED_MODEL = "embed-v4.0"


def ensure_dst(client):
    """Create Passage_v2 (Cohere-vectorized, same properties as Passage)."""
    if client.collections.exists(DST):
        print(f"'{DST}' already exists.")
        return
    try:
        client.collections.create(
            name=DST,
            vectorizer_config=wcd.Configure.Vectorizer.text2vec_cohere(model=COHERE_EMBED_MODEL),
            properties=[
                wcd.Property(name="content", data_type=wcd.DataType.TEXT),
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
        print(f"Created '{DST}' (text2vec-cohere / {COHERE_EMBED_MODEL}).")
    except Exception as e:
        print(f"ERROR creating '{DST}': {e}")
        print("If this is a module error, enable text2vec-cohere on the Weaviate cluster and re-run.")
        sys.exit(1)


PROPS = ["content", "article_id", "chunk_index", "title", "link",
         "location", "occasion", "collection_name", "date_authored"]


def main():
    run = "--run" in sys.argv
    client = get_client()
    src = client.collections.get(SRC)
    total = src.aggregate.over_all(total_count=True).total_count
    print(f"Source '{SRC}' has {total} passages.")

    if not run:
        print("Dry run (no --run). Would create Passage_v2 and copy all passages.")
        return

    ensure_dst(client)
    dst = client.collections.get(DST)

    copied = 0
    with dst.batch.dynamic() as batch:
        for obj in src.iterator():
            props = {k: obj.properties.get(k) for k in PROPS}
            batch.add_object(properties=props)  # Weaviate embeds with Cohere on insert
            copied += 1
            if copied % 500 == 0:
                print(f"  copied {copied}/{total}…")

    failed = len(dst.batch.failed_objects) if hasattr(dst.batch, "failed_objects") else 0
    print(f"Done: copied {copied} passages into '{DST}' ({failed} failed).")
    print("Next: run eval_ragas.py A/B, then flip PASSAGE_COLLECTION to 'Passage_v2' if it wins.")


if __name__ == "__main__":
    main()
