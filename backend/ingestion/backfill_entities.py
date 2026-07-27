"""Backfill the Entity collection from the discourse corpus.

WHY
The structured-knowledge route (search/knowledge.py) works well but has almost
nothing to work with: the live Entity collection holds 5 rows, while factual and
definitional questions are ~18% of real traffic. So "Who was Swami's mother?"
resolves, and "when was Swami born" falls through to thematic search and returns
discourses that merely feel related.

Entities are extracted FROM the corpus, so an extracted entity is by definition
covered by it — its canonical articles are the ones that actually discuss it.
Known GAPS (things people ask about that the discourses don't cover, e.g. the
Tripura Rahasya text) can't be discovered this way and are curated below; those
are what drive honest KB_KNOWN_GAP abstention instead of a homonym guess.

    venv/bin/python backfill_entities.py --dry-run       # 15 articles, print, no file
    venv/bin/python backfill_entities.py --limit 200     # partial pass
    venv/bin/python backfill_entities.py --all           # full corpus (~2,409 articles)

Output: entities_review.json — FOR HUMAN REVIEW. This script never writes to
Weaviate. Ingest happens separately, after you have read the file.

Resumable: partial extractions are checkpointed, so an interrupted run restarts
where it stopped.
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
import re
import collections
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

load_dotenv()

from weaviate_client import get_client  # noqa: E402
from search.config import openai_client, GRADE_MODEL  # noqa: E402
from search.retrieval import search_passages  # noqa: E402
from search.ranking import rerank_passages  # noqa: E402

OUT_FILE = _artifact("entities_review.json")
CHECKPOINT = _artifact("entities_extract_checkpoint.json")
REJECTED_FILE = _artifact("entities_rejected.json")
MAX_WORKERS = 8
# Entities cluster in the opening of a discourse (and in its title). Sending the
# whole article would triple cost for very little extra recall.
CONTENT_WINDOW = 5000
# An entity mentioned in only one article is usually a passing reference, not
# something the corpus can be said to cover. Two is a low but meaningful bar.
MIN_ARTICLES = 2
# An entity with no discourse titled after it can still be well covered — the
# corpus discusses Brahma muhurta across 75 articles without ever naming a
# discourse for it. For those, the canonical article is chosen by RERANKING the
# candidates against "what is X", which preserves the property the title gate was
# protecting (the article must actually EXPLAIN the entity) without requiring the
# title to say so. Frequency alone would not: the most frequent mention is
# usually a passing one.
ENTITY_RERANK_MIN_SCORE = 0.65
ENTITY_RERANK_WORKERS = 6
ENTITY_SNIPPET = 1200

_ASK = {"person": "Who is {}?", "place": "Where is {}? What is {}?",
        "text": "What is the text {}?", "festival": "What is the festival {}?",
        "org_term": "What is {}?", "concept": "What is {}?"}


def _entity_question(name, etype):
    tpl = _ASK.get(etype, "What is {}?")
    return tpl.replace("{}", name)


def pick_canonical_by_rerank(name, etype, candidates, by_uuid):
    """Choose the article that best EXPLAINS this entity, at PASSAGE granularity.

    Reranking whole-article openings was tried first and does not work: the
    entity is usually discussed deep inside a discourse, so the opening 1,200
    chars the reranker saw often didn't mention it at all. That picked "Respect
    for Parents" as the canonical article for Brahma muhurta at 0.605.

    Searching passages instead puts the reranker in front of the text that
    actually discusses the entity. Same probe, passage-level: "The Practice of
    Meditation" at 0.828 — the right discourse, and a confident score.

    Two guards, because a wrong canonical article is worse than none (it turns a
    factual question into a confident wrong answer, where falling through to
    semantic search would merely have been vague):
      1. the top passage must clear ENTITY_RERANK_MIN_SCORE;
      2. the passage must LITERALLY mention the entity — the reranker will
         happily return thematically-adjacent text that never names it.

    Returns (article_metas, top_score); empty list means "leave it to search".
    """
    q = _entity_question(name, etype)
    try:
        cands = search_passages(q, 40)
        ranked = rerank_passages(q, cands, 8)
    except Exception as e:
        logging.error(f"canonical rerank failed for {name!r}: {e}")
        return [], 0.0
    if not ranked or ranked[0].get("rerank_degraded"):
        return [], 0.0

    toks = [t for t in re.findall(r"[a-z]+", name.lower()) if len(t) > 2]
    top = ranked[0].get("rerank_score", 0.0)
    if top < ENTITY_RERANK_MIN_SCORE:
        return [], top

    seen, out = set(), []
    for p in ranked:
        if p.get("rerank_score", 0.0) < ENTITY_RERANK_MIN_SCORE:
            break
        body = (p.get("content", "") + " " + p.get("title", "")).lower()
        if toks and not all(t in body for t in toks):
            continue  # thematically close but never names the entity
        aid = p.get("article_id", "")
        if aid and aid not in seen:
            seen.add(aid)
            out.append({"uuid": aid, "title": p.get("title", "")})
        if len(out) >= 3:
            break
    return out, top


VALID_TYPES = {"person", "place", "text", "festival", "org_term", "concept"}

# Aliases too broad to identify anything. The extractor happily offers "God" and
# "Self" as aliases for Brahman, and "virtue" for Dharma — but knowledge.py
# matches on name + aliases, so those would route ordinary thematic questions
# into a canned three-article answer. Rejected wholesale.
GENERIC_ALIASES = {
    "god", "the divine", "divine", "self", "the self", "soul", "individual soul",
    "spirit", "lord", "the lord", "truth", "love", "peace", "virtue", "duty",
    "righteousness", "righteous living", "devotion", "faith", "grace", "mind",
    "the almighty", "almighty", "creator", "supreme", "the supreme", "reality",
    "consciousness", "knowledge", "wisdom", "bliss", "nature", "the world",
}

# Things real users ask about that the corpus does NOT explain. These cannot be
# discovered by extraction (they're absent by definition) and are what make
# honest abstention possible. Each is a homonym or near-miss trap: without the
# gap row, semantic search returns a confident wrong answer.
KNOWN_GAPS = [
    {
        "name": "Tripura Rahasya",
        "aliases": "Tripura Rahasyam; Tripurarahasya",
        "entity_type": "text",
        "summary": "Tripura Rahasya, a classical Advaita text. Distinct from the "
                   "'three cities' (tripura) metaphor the discourses do discuss.",
    },
]

# Demand-ranked seeds. Extraction should find all of these, but they are the
# entities whose failure is most visible in the logs, so the review file flags
# them explicitly — if one is missing or unresolved, that is a bug worth chasing
# before ingest rather than a long-tail miss.
PRIORITY_ENTITIES = [
    "Easwaramma", "Puttaparthi", "Prasanthi Nilayam", "Krishna", "Rama",
    "Shirdi Sai Baba", "Nine Point Code of Conduct", "Sathya Sai Education",
    "Bal Vikas", "Geetha Vahini", "Prema Vahini", "Dharma Vahini",
    "Bhagavad Geetha", "Ramayana", "Bhagavatha", "Shivarathri", "Dasara",
    "Guru Purnima", "Brahma muhurta",
]

SYSTEM = (
    "You extract named entities from a discourse by Sathya Sai Baba, for a "
    "knowledge base that answers FACTUAL questions ('who was Swami's mother', "
    "'what is the Nine Point Code', 'when is Brahma muhurta').\n\n"
    "Extract only entities this discourse actually SAYS SOMETHING ABOUT — a name "
    "in a passing list is not an entity worth recording.\n\n"
    "Types:\n"
    "  person    — a named individual (Easwaramma, Krishna, Shirdi Sai Baba)\n"
    "  place     — a named location (Puttaparthi, Prasanthi Nilayam)\n"
    "  text      — a named scripture or book (Geetha Vahini, Ramayana)\n"
    "  festival  — a named observance (Shivarathri, Dasara, Guru Purnima)\n"
    "  org_term  — a named programme, code, or organizational term (Nine Point "
    "Code of Conduct, Bal Vikas, Sathya Sai Education)\n"
    "  concept   — a named doctrinal practice or prescribed thing with a specific "
    "referent (Brahma muhurta, Namasmarana, Japa). NOT broad themes like 'love', "
    "'truth', 'peace' or 'devotion' — those are what thematic search already "
    "handles, and putting them here would hijack ordinary questions into the "
    "factual route.\n\n"
    "For each entity give: name (canonical form), type, and a ONE-SENTENCE factual "
    "summary drawn from THIS discourse. Include obvious alternate spellings or "
    "referring phrases in aliases (e.g. \"Swami's mother\" for Easwaramma).\n\n"
    'Respond ONLY with JSON: {"entities":[{"name":"...","type":"person",'
    '"aliases":["..."],"summary":"..."}]}. Return {"entities":[]} if none qualify.'
)


def _norm(name):
    return re.sub(r"[^a-z0-9 ]", "", (name or "").lower()).strip()


def load_articles(client, limit=0):
    col = client.collections.get("Article")
    out = []
    for obj in col.iterator():
        p = obj.properties
        out.append({
            "uuid": str(obj.uuid),
            "title": p.get("title", "") or "",
            "collection_name": p.get("collection_name", "") or "",
            "content": (p.get("content", "") or "")[:CONTENT_WINDOW],
        })
        if limit and len(out) >= limit:
            break
    return out


def extract_one(article):
    """Extract entities from one article. Never raises — a failed article is
    simply skipped rather than aborting a multi-hour run."""
    try:
        r = openai_client.chat.completions.create(
            model=GRADE_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content":
                 f"Discourse title: {article['title']}\n"
                 f"Collection: {article['collection_name']}\n\n{article['content']}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        data = json.loads(r.choices[0].message.content or "{}")
        ents = []
        for e in data.get("entities", []):
            name = (e.get("name") or "").strip()
            etype = (e.get("type") or "").strip().lower()
            if not name or etype not in VALID_TYPES:
                continue
            aliases = e.get("aliases") or []
            ents.append({
                "name": name,
                "type": etype,
                "aliases": [a.strip() for a in aliases if isinstance(a, str) and a.strip()],
                "summary": (e.get("summary") or "").strip(),
            })
        return article["uuid"], ents
    except Exception as e:
        logging.error(f"entity extraction failed for {article['title']!r}: {e}")
        return article["uuid"], []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="15 articles, print, write nothing.")
    ap.add_argument("--limit", type=int, default=0, help="Process only N articles.")
    ap.add_argument("--all", action="store_true", help="Process the whole corpus.")
    args = ap.parse_args()

    if not (args.dry_run or args.limit or args.all):
        ap.error("pick one of --dry-run, --limit N, or --all "
                 "(the full pass is ~2,409 LLM calls, so it is opt-in)")

    client = get_client()
    limit = 15 if args.dry_run else args.limit
    print("Loading articles...")
    articles = load_articles(client, limit)
    print(f"  {len(articles)} articles")

    # Resume: skip articles already extracted in a previous run.
    per_article = {}
    if not args.dry_run and os.path.exists(CHECKPOINT):
        per_article = json.load(open(CHECKPOINT))
        print(f"  resuming: {len(per_article)} already extracted")
    todo = [a for a in articles if a["uuid"] not in per_article]

    print(f"Extracting from {len(todo)} articles ({MAX_WORKERS} workers)...")
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(extract_one, a): a for a in todo}
        for fut in as_completed(futures):
            uuid, ents = fut.result()
            per_article[uuid] = ents
            done += 1
            if done % 50 == 0 or done == len(todo):
                print(f"  {done}/{len(todo)}")
                if not args.dry_run:
                    json.dump(per_article, open(CHECKPOINT, "w"))

    by_uuid = {a["uuid"]: a for a in articles}

    if args.dry_run:
        for uuid, ents in list(per_article.items())[:15]:
            art = by_uuid.get(uuid)
            if not art or not ents:
                continue
            print(f"\n--- {art['title']} ---")
            for e in ents:
                print(f"    [{e['type']:9s}] {e['name']}  aliases={e['aliases']}")
                print(f"                {e['summary'][:110]}")
        client.close()
        print("\nDRY RUN — no file written.")
        return

    # --- Aggregate: entity -> the articles that discuss it ---
    agg = {}
    for uuid, ents in per_article.items():
        art = by_uuid.get(uuid)
        if not art:
            continue  # from a previous, larger run
        for e in ents:
            key = _norm(e["name"])
            if not key:
                continue
            slot = agg.setdefault(key, {
                "names": collections.Counter(), "type": collections.Counter(),
                "aliases": set(), "summaries": [], "articles": [],
            })
            slot["names"][e["name"]] += 1
            slot["type"][e["type"]] += 1
            slot["aliases"].update(e["aliases"])
            if e["summary"]:
                slot["summaries"].append((uuid, e["summary"]))
            # An entity named in the TITLE is what that discourse is *about* —
            # a far stronger canonical signal than a body mention.
            in_title = _norm(e["name"]) in _norm(art["title"])
            slot["articles"].append({"uuid": uuid, "title": art["title"],
                                     "in_title": in_title})

    # --- Merge alias-linked entities -------------------------------------
    # Extraction is per-article, so the same thing surfaces under different
    # names in different discourses ("Geetha" here, "Bhagavad Gita" there).
    # Left unmerged they compete: each holds half the evidence and points at a
    # different canonical article, and knowledge.py's hybrid match gets weaker
    # for both. Merge when one entity's NAME is another's declared ALIAS and the
    # types agree; the entity seen in more articles wins the canonical name.
    parent = {k: k for k in agg}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    alias_index = collections.defaultdict(set)
    for key, slot in agg.items():
        for a in slot["aliases"]:
            na = _norm(a)
            if na and na != key:
                alias_index[na].add(key)

    for key in list(agg):
        for other in alias_index.get(key, ()):
            if other not in agg:
                continue
            ka, kb = find(key), find(other)
            if ka == kb:
                continue
            # Different types means a genuine homonym, not an alias — keep apart.
            if agg[ka]["type"].most_common(1)[0][0] != agg[kb]["type"].most_common(1)[0][0]:
                continue
            # The better-evidenced entity absorbs the other.
            big, small = (ka, kb) if len(agg[ka]["articles"]) >= len(agg[kb]["articles"]) else (kb, ka)
            parent[small] = big

    merged = {}
    for key, slot in agg.items():
        root = find(key)
        if root == key:
            merged.setdefault(root, slot)
            continue
        tgt = merged.setdefault(root, agg[root])
        if tgt is slot:
            continue
        tgt["names"].update(slot["names"])
        tgt["type"].update(slot["type"])
        tgt["aliases"].update(slot["aliases"])
        tgt["aliases"].add(slot["names"].most_common(1)[0][0])
        tgt["summaries"].extend(slot["summaries"])
        seen_ids = {a["uuid"] for a in tgt["articles"]}
        tgt["articles"].extend(a for a in slot["articles"] if a["uuid"] not in seen_ids)
    n_merged = len(agg) - len(merged)
    agg = merged
    print(f"  merged {n_merged} alias-duplicate entities -> {len(agg)} distinct")

    entities = []
    rejected = []
    pending_rerank = []
    for key, slot in agg.items():
        if len(slot["articles"]) < MIN_ARTICLES:
            continue
        name = slot["names"].most_common(1)[0][0]
        etype = slot["type"].most_common(1)[0][0]

        # Recompute title matching against the CANONICAL name, not the variant
        # each article happened to use. Extraction called it "Lord Rama" in the
        # discourse titled "Rama Avatara", so matching on the per-article name
        # missed the very article the entity is named after. Aliases count too —
        # "Bhagavad Gita" should match the discourse titled "The Gita Balance".
        match_forms = {_norm(name)} | {_norm(a) for a in slot["aliases"]}
        match_forms = {m for m in match_forms if len(m) > 2}
        for a in slot["articles"]:
            nt = _norm(a["title"])
            a["in_title"] = any(m in nt for m in match_forms)

        # Canonical articles: title matches first, then most-discussed. Capped at
        # 3 — knowledge.py returns all of them as the answer, and a wall of
        # results defeats the point of a canonical lookup.
        ranked = sorted(slot["articles"], key=lambda a: (not a["in_title"],))
        canonical = ranked[:3]
        summary = slot["summaries"][0][1] if slot["summaries"] else ""
        aliases = sorted(a for a in slot["aliases"] if _norm(a) != key)

        # Drop aliases that are too generic to be identifying. knowledge.py
        # matches on name + aliases, so an alias like "God" or "soul" on Brahman
        # would capture ordinary thematic questions ("how do I find God?") and
        # answer them from 3 fixed articles instead of searching properly.
        aliases = [a for a in aliases if _norm(a) not in GENERIC_ALIASES]

        # An entity whose NAME is a broad theme ("God", "Dharma", "Prema") is the
        # same hazard as a generic alias: knowledge.py would capture "what is
        # dharma?" and answer it from three fixed discourses, when that is
        # precisely the question thematic search is good at. Held back, not
        # deleted — add any of these back by hand if a canonical answer really is
        # better than a search.
        if _norm(name) in GENERIC_ALIASES:
            rejected.append({
                "name": name, "entity_type": etype,
                "_article_count": len(slot["articles"]),
                "_reason": "name is a broad theme — routing it to the structured "
                           "route would hijack questions thematic search answers better",
                "_sample_titles": [a["title"] for a in canonical[:2]],
            })
            continue

        # THE GATE: an entity is only canonical if some discourse is ABOUT it —
        # i.e. names it in the title. Without that we have articles that merely
        # mention it, and pointing a factual question at those produces a
        # confident wrong answer. Falling through to semantic search is strictly
        # better, so these are recorded for review but not marked in_corpus.
        if any(a["in_title"] for a in canonical):
            # Keep only the title-matching articles; a mention-only article
            # alongside a real one just dilutes the answer.
            canonical = [a for a in canonical if a["in_title"]][:3]
            selected_by = "title"
        else:
            # No discourse is named after it — defer to the reranker pass below,
            # which asks "which of these articles actually explains X?".
            pending_rerank.append({
                "key": key, "name": name, "etype": etype, "aliases": aliases,
                "summary": summary, "slot": slot,
            })
            continue
        entities.append({
            "name": name,
            "aliases": "; ".join(aliases),
            "summary": summary,
            "entity_type": etype,
            "canonical_article_ids": json.dumps([a["uuid"] for a in canonical]),
            "in_corpus": True,
            # Review-only fields; strip before ingest.
            "_article_count": len(slot["articles"]),
            "_title_match": any(a["in_title"] for a in canonical),
            "_selected_by": selected_by,
            "_canonical_titles": [a["title"] for a in canonical],
        })

    # --- Reranker pass: canonical selection for untitled-but-covered entities ---
    if pending_rerank:
        print(f"  reranking canonical articles for {len(pending_rerank)} untitled entities...")

        def _resolve(item):
            kept, top = pick_canonical_by_rerank(
                item["name"], item["etype"], item["slot"]["articles"], by_uuid)
            return item, kept, top

        done_n = 0
        with ThreadPoolExecutor(max_workers=ENTITY_RERANK_WORKERS) as pool:
            for fut in as_completed([pool.submit(_resolve, i) for i in pending_rerank]):
                item, kept, top = fut.result()
                done_n += 1
                if done_n % 100 == 0:
                    print(f"    {done_n}/{len(pending_rerank)}")
                if not kept:
                    rejected.append({
                        "name": item["name"], "entity_type": item["etype"],
                        "_article_count": len(item["slot"]["articles"]),
                        "_reason": f"no discourse titled after it, and no article "
                                   f"explained it well enough (best rerank {top:.2f} "
                                   f"< {ENTITY_RERANK_MIN_SCORE})",
                        "_sample_titles": [a["title"] for a in item["slot"]["articles"][:2]],
                    })
                    continue
                entities.append({
                    "name": item["name"],
                    "aliases": "; ".join(item["aliases"]),
                    "summary": item["summary"],
                    "entity_type": item["etype"],
                    "canonical_article_ids": json.dumps([a["uuid"] for a in kept]),
                    "in_corpus": True,
                    "_article_count": len(item["slot"]["articles"]),
                    "_title_match": False,
                    "_selected_by": "reranker",
                    "_rerank_score": round(top, 3),
                    "_canonical_titles": [a["title"] for a in kept],
                })

    for gap in KNOWN_GAPS:
        entities.append({**gap, "canonical_article_ids": "[]", "in_corpus": False,
                         "_article_count": 0, "_title_match": False,
                         "_selected_by": "curated_gap", "_canonical_titles": []})

    # --- Drop non-identifying aliases -------------------------------------
    # An alias exists to identify ONE entity. Extraction is per-article, so the
    # same phrase gets attached to whatever the discourse was discussing:
    # "Swami's mother" ended up on Devaki, Kunti, Gandhari, Kausalya and Sita as
    # well as Easwaramma. knowledge.py matches on name + aliases, so shipping
    # that means "Who was Swami's mother?" can resolve to Krishna's mother.
    #
    # Any alias claimed by more than one entity is removed from all of them. The
    # name is always kept, so nothing becomes unfindable — worst case we lose a
    # legitimate synonym's recall, which is far cheaper than mis-routing a
    # factual question. (Some collisions are genuine near-duplicates —
    # Ajnana/Ajnanam/avidya all glossed "ignorance" — but merging those needs
    # the same human judgement as the transliteration families, so they are
    # dropped rather than guessed at.)
    claims = collections.defaultdict(set)
    for e in entities:
        for a in (e["aliases"] or "").split(";"):
            k = _norm(a)
            if k:
                claims[k].add(e["name"])
    ambiguous = {k for k, v in claims.items() if len(v) > 1}
    purged = 0
    for e in entities:
        kept = [a for a in (e["aliases"] or "").split(";")
                if a.strip() and _norm(a) not in ambiguous]
        purged += len((e["aliases"] or "").split(";")) - len(kept) if e["aliases"] else 0
        e["aliases"] = "; ".join(a.strip() for a in kept)
    print(f"  purged {purged} non-identifying alias attachments "
          f"({len(ambiguous)} phrases claimed by >1 entity)")

    # Alphabetical, so a human reviewing 1,000+ rows can find a specific entity
    # instead of scanning. Case- and punctuation-insensitive so "Bal Vikas" and
    # "bhajan" sort where a reader expects, not by codepoint. Demand is still
    # legible per row via _article_count.
    entities.sort(key=lambda e: (_norm(e["name"]), e["name"]))
    rejected.sort(key=lambda r: (_norm(r["name"]), r["name"]))

    json.dump(entities, open(OUT_FILE, "w"), indent=2)
    json.dump(rejected, open(REJECTED_FILE, "w"), indent=2)

    found = {_norm(e["name"]) for e in entities}
    found |= {_norm(a) for e in entities for a in e["aliases"].split(";")}
    missing = [p for p in PRIORITY_ENTITIES if _norm(p) not in found]

    print(f"\nWrote {OUT_FILE}: {len(entities)} entities "
          f"({len(entities) - len(KNOWN_GAPS)} extracted + {len(KNOWN_GAPS)} known gaps)")
    print("  by type:", dict(collections.Counter(e["entity_type"] for e in entities)))
    print(f"\nWrote {REJECTED_FILE}: {len(rejected)} entities held back "
          "(no discourse is titled after them — semantic search serves these better)")
    # The FILES are alphabetical for review; this summary still ranks by demand,
    # because "which held-back entity is most discussed" is the question you ask
    # when deciding whether the gate is too strict.
    for r in sorted(rejected, key=lambda x: -x["_article_count"])[:6]:
        print(f"    {r['name'][:28]:28s} ({r['_article_count']} articles) e.g. {r['_sample_titles'][:1]}")
    if missing:
        print(f"\n  !! {len(missing)} priority entities NOT found — investigate before ingest:")
        for m in missing:
            print(f"       {m}")
    else:
        print("\n  all priority entities present")
    print("\nREVIEW BEFORE INGEST. Check that canonical_article_ids point at "
          "discourses that actually EXPLAIN the entity, not ones that merely "
          "mention it — a wrong canonical article turns a factual question into a "
          "confident wrong answer, which is worse than falling through to search.")


if __name__ == "__main__":
    main()
