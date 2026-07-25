"""Seed the Entity knowledge collection (Router v2 / Phase 2).

Read-only against Article; writes the Entity collection. Idempotent — keyed on
entity name, so re-running replaces prior copies. Same convention as the other
ingest_*.py scripts:

    venv/bin/python ingest_entities.py

Each seed entry either resolves to a canonical Article (by title search) and is
marked in_corpus=True, or is left not-in-corpus so the pipeline abstains honestly
("the discourses don't directly cover this") instead of returning spurious
thematic matches. We NEVER store a fabricated answer — `summary` is internal
match text only; the product returns Swami's discourses or an honest gap.

Start small (the highest-demand entities from the coverage-gap diagnostic and the
audits); grow over time.
"""

import json

from dotenv import load_dotenv

load_dotenv()

from weaviate_client import get_client  # noqa: E402
from weaviate.classes.query import Filter  # noqa: E402

# entity_type: person | place | text | org_term | occasion
# resolve_titles: candidate substrings to find a canonical Article by title.
#   If any resolves, in_corpus=True and we point at it; else in_corpus=False.
# force_gap=True marks it not-in-corpus regardless (known gaps).
SEED = [
    {
        "name": "Nine Point Code of Conduct",
        "aliases": "9 point code of conduct; nine point code; code of conduct",
        "summary": "The Sathya Sai organization's Nine Point Code of Conduct for members.",
        "entity_type": "org_term",
        "resolve_titles": ["Nine Point Code of Conduct", "Nine-Point Code"],
    },
    {
        "name": "Sathya Sai Education (SSE)",
        "aliases": "SSE; Sathya Sai Education in Human Values; Bal Vikas; Balvikas",
        "summary": "Sathya Sai Education / Bal Vikas — the organization's education-in-human-values programme.",
        "entity_type": "org_term",
        "resolve_titles": ["Bal Vikas", "Balvikas", "Sathya Sai Education", "Educare"],
    },
    {
        "name": "Easwaramma",
        "aliases": "Swami's mother; Baba's mother; Eswaramma; mother of Sathya Sai Baba",
        "summary": "Easwaramma was the earthly mother of Sathya Sai Baba.",
        "entity_type": "person",
        "resolve_titles": ["Easwaramma", "Eswaramma"],
    },
    {
        "name": "Puttaparthi",
        "aliases": "Prasanthi Nilayam; Swami's birthplace; Parthi",
        "summary": "Puttaparthi, the village in Andhra Pradesh where Sathya Sai Baba was born and where Prasanthi Nilayam is located.",
        "entity_type": "place",
        "resolve_titles": ["Puttaparthi", "Prasanthi Nilayam"],
    },
    {
        "name": "Tripura Rahasya",
        "aliases": "Tripura Rahasyam; Tripurarahasya",
        "summary": "Tripura Rahasya, a classical Advaita text. Distinct from the 'three cities' (tripura) metaphor.",
        "entity_type": "text",
        "force_gap": True,  # audits showed the corpus does not cover this text
    },
    {
        "name": "Bhagavad Geetha",
        "aliases": "Bhagavad Gita; the Gita; Geetha; Bhagavadgita",
        "summary": "The Bhagavad Geetha, the scripture Swami frequently expounds.",
        "entity_type": "text",
        "resolve_titles": ["Geetha", "Bhagavad"],
    },
]


def resolve_article_ids(articles, titles):
    """Return canonical Article UUIDs whose title contains any candidate substring."""
    ids = []
    for t in titles:
        resp = articles.query.fetch_objects(
            filters=Filter.by_property("title").like(f"*{t}*"), limit=3
        )
        ids.extend(str(o.uuid) for o in resp.objects)
    # dedupe, cap
    return list(dict.fromkeys(ids))[:5]


def main():
    client = get_client()
    articles = client.collections.get("Article")
    entities = client.collections.get("Entity")

    seeded = 0
    for e in SEED:
        # Idempotent: drop any prior copy of this entity by name.
        entities.data.delete_many(where=Filter.by_property("name").equal(e["name"]))

        if e.get("force_gap"):
            article_ids, in_corpus = [], False
        else:
            article_ids = resolve_article_ids(articles, e.get("resolve_titles", []))
            in_corpus = bool(article_ids)

        # Only seed confident HITS (resolved to canonical articles) or KNOWN GAPS
        # (force_gap). An entity that simply didn't resolve isn't necessarily
        # missing from the corpus — it may be covered thematically — so leave it
        # out and let it fall through to the semantic route instead of abstaining.
        if not article_ids and not e.get("force_gap"):
            print(f"  skipped {e['name']!r:40} {e['entity_type']:9} unresolved -> semantic fallback")
            continue

        seeded += 1
        entities.data.insert({
            "name": e["name"],
            "aliases": e["aliases"],
            "summary": e["summary"],
            "entity_type": e["entity_type"],
            "canonical_article_ids": json.dumps(article_ids),
            "in_corpus": in_corpus,
        })
        status = f"in_corpus={in_corpus}" + (f" ({len(article_ids)} article(s))" if article_ids else " [honest gap]")
        print(f"  seeded {e['name']!r:40} {e['entity_type']:9} {status}")

    print(f"\nSeeded {seeded} entities into Entity collection ({len(SEED) - seeded} skipped).")


if __name__ == "__main__":
    main()
