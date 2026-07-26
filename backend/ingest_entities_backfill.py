"""Ingest the reviewed entity backfill into the live Entity collection.

Reads entities_review.json (produced by backfill_entities.py, human-reviewed) and
writes it to Weaviate. Follows ingest_entities.py's conventions, with three
additions that matter for a write of this size:

  1. BACKUP FIRST. Every existing Entity row is exported to a timestamped file
     before anything is written, and --rollback restores from it. The collection
     currently holds hand-curated rows that predate the backfill; losing them to
     a bad run would be unrecoverable otherwise.

  2. HAND-CURATED ROWS WIN. An extracted entity whose name or aliases collide
     with an existing row is SKIPPED, not merged and not overwritten. Those rows
     were verified by a human against the corpus; an automated extraction is not
     evidence enough to replace one. Skips are listed so you can see what was
     deferred.

  3. IDEMPOTENT. Re-running deletes this script's own prior inserts (matched by
     name) before re-inserting, so a partial run can simply be repeated.

    venv/bin/python ingest_entities_backfill.py --dry-run   # report, write nothing
    venv/bin/python ingest_entities_backfill.py             # ingest
    venv/bin/python ingest_entities_backfill.py --rollback entity_backup_<ts>.json
"""

import argparse
import json
import os
import re
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from weaviate_client import get_client  # noqa: E402
from weaviate.classes.query import Filter  # noqa: E402

REVIEW_FILE = "entities_review.json"
BATCH_REPORT = "entity_ingest_report.json"
# The collection as it stood before this script ever wrote to it. Committed, and
# the authoritative definition of "hand-curated" — NOT "whatever is in the
# collection now", which after one run includes this script's own inserts.
CURATED_BASELINE = "entity_baseline_curated.json"

# Properties the Entity collection actually stores. Review-only keys (_article_count,
# _selected_by, ...) are stripped — they exist for human eyes, not for retrieval.
FIELDS = ("name", "aliases", "summary", "entity_type", "canonical_article_ids", "in_corpus")

# Curated aliases for high-demand PARAPHRASES — the phrasings people actually
# type, which share no words with the entity's name.
#
# knowledge.py guards against BM25 false hits by requiring a content-word overlap
# between the question and the entity's name+aliases. That guard is right, but it
# means "When did Sai Baba say the best time to wake up?" (asked 145 times) can
# never match "Brahma muhurta" — zero shared words — even though the KB holds the
# answer. Extraction cannot invent these; they come from reading the query logs.
#
# Keep each phrase specific. A vague one here would capture unrelated questions,
# which is the failure mode the guard exists to prevent.
CURATED_ALIASES = {
    "Brahma muhurta": [
        "best time to wake up", "when to wake up", "what time to wake up",
        "best time to get up", "when to rise", "early morning hours",
        "best time to meditate", "auspicious hour",
    ],
    "Namasmarana": ["repeating God's name", "chanting the name", "remembering the name"],
}
# NOTE: a diet entity ("Sathwic food") was wanted here but the corpus extraction
# produced no such entity, so there is nothing to attach the paraphrases to.
# Diet questions ("what does Swami say about diet") already answer well on the
# semantic route, so this is left alone rather than inventing an entity.


def norm(n):
    return re.sub(r"[^a-z0-9 ]", "", (n or "").lower()).strip()


def existing_rows(entities):
    out = []
    for o in entities.iterator():
        p = dict(o.properties)
        p["_uuid"] = str(o.uuid)
        out.append(p)
    return out


def backup(rows):
    path = f"entity_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
    json.dump(rows, open(path, "w"), indent=2)
    print(f"  backed up {len(rows)} existing rows -> {path}")
    return path


def rollback(client, path):
    rows = json.load(open(path))
    entities = client.collections.get("Entity")
    print(f"Restoring {len(rows)} rows from {path} (wipes current contents)...")
    for o in entities.iterator():
        entities.data.delete_by_id(o.uuid)
    for r in rows:
        entities.data.insert({k: r.get(k) for k in FIELDS})
    print(f"Restored {len(rows)} rows.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    ap.add_argument("--rollback", metavar="BACKUP", help="Restore Entity from a backup file.")
    ap.add_argument("--protected-from", metavar="BACKUP",
                    help="Treat this backup's rows as the hand-curated baseline "
                         "(default: the oldest entity_backup_*.json).")
    args = ap.parse_args()

    client = get_client()
    entities = client.collections.get("Entity")

    if args.rollback:
        rollback(client, args.rollback)
        client.close()
        return

    reviewed = json.load(open(REVIEW_FILE))
    current = existing_rows(entities)
    print(f"Entity collection currently holds {len(current)} rows.")
    print(f"{REVIEW_FILE} holds {len(reviewed)} reviewed entities.")

    # Which rows are HAND-CURATED? Not "everything currently in the collection" —
    # after one run that includes this script's own 1,059 inserts, and a re-run
    # would then skip all of them and become a no-op. The oldest backup is the
    # collection as it stood before this script ever wrote to it, so that is the
    # authoritative baseline. No backup => first run => everything present is
    # hand-curated.
    if args.protected_from:
        baseline, src = json.load(open(args.protected_from)), args.protected_from
    elif os.path.exists(CURATED_BASELINE):
        baseline, src = json.load(open(CURATED_BASELINE)), CURATED_BASELINE
    else:
        # First ever run: whatever is here now is hand-curated by definition, and
        # gets frozen as the baseline so later runs cannot mistake this script's
        # own inserts for human work.
        baseline, src = current, "current collection (first run)"
        json.dump(current, open(CURATED_BASELINE, "w"), indent=2)
        print(f"  froze {len(current)} rows as the curated baseline -> {CURATED_BASELINE}")
    print(f"  hand-curated baseline: {len(baseline)} rows from {src}")

    protected = {}
    for r in baseline:
        for key in [r.get("name", "")] + (r.get("aliases") or "").split(";"):
            k = norm(key)
            if k:
                protected[k] = r.get("name", "")

    to_insert, skipped, curated = [], [], []
    for e in reviewed:
        keys = [norm(e["name"])] + [norm(a) for a in (e.get("aliases") or "").split(";")]
        hit = next((protected[k] for k in keys if k and k in protected), None)
        if hit:
            skipped.append((e["name"], hit))
            continue
        row = {k: e[k] for k in FIELDS}
        extra = CURATED_ALIASES.get(row["name"])
        if extra:
            have = {norm(a) for a in (row["aliases"] or "").split(";") if norm(a)}
            add = [a for a in extra if norm(a) not in have]
            if add:
                row["aliases"] = "; ".join(
                    [a.strip() for a in (row["aliases"] or "").split(";") if a.strip()] + add)
                curated.append((row["name"], len(add)))
        to_insert.append(row)

    print(f"\n  to insert : {len(to_insert)}")
    print(f"  skipped   : {len(skipped)} (collide with an existing hand-curated row)")
    for name, owner in skipped[:12]:
        print(f"      {name[:34]:34s} -> already covered by {owner!r}")
    if len(skipped) > 12:
        print(f"      ... and {len(skipped) - 12} more")

    for name, n in curated:
        print(f"  + {n} curated paraphrase aliases on {name!r}")
    missing_curated = [k for k in CURATED_ALIASES if not any(e["name"] == k for e in to_insert)]
    if missing_curated:
        print(f"  !! curated aliases target entities not in the insert set: {missing_curated}")

    gaps = sum(1 for e in to_insert if not e["in_corpus"])
    print(f"  of those, {gaps} are honest gaps (in_corpus=False)")

    if args.dry_run:
        client.close()
        print("\nDRY RUN — nothing written.")
        return

    backup_path = backup(current)

    # Idempotent: clear any prior copy of the names we are about to write, so a
    # repeated run cannot accumulate duplicates.
    names = {e["name"] for e in to_insert}
    cleared = 0
    for r in current:
        if r.get("name") in names:
            entities.data.delete_by_id(r["_uuid"])
            cleared += 1
    if cleared:
        print(f"  cleared {cleared} prior copies of names being re-written")

    inserted, failed = 0, 0
    with entities.batch.dynamic() as batch:
        for e in to_insert:
            batch.add_object(properties=e)
            inserted += 1
    if entities.batch.failed_objects:
        failed = len(entities.batch.failed_objects)
        print(f"  !! {failed} objects failed to insert")
        for f in entities.batch.failed_objects[:3]:
            print(f"       {f.message[:120]}")

    final = len(existing_rows(entities))
    json.dump({
        "backup": backup_path,
        "inserted": inserted - failed,
        "failed": failed,
        "skipped": [{"name": n, "covered_by": o} for n, o in skipped],
        "final_count": final,
    }, open(BATCH_REPORT, "w"), indent=2)

    client.close()
    print(f"\nInserted {inserted - failed}, failed {failed}.")
    print(f"Entity collection now holds {final} rows.")
    print(f"Report: {BATCH_REPORT}   Rollback: --rollback {backup_path}")


if __name__ == "__main__":
    main()
