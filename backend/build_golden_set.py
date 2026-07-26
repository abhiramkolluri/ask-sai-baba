"""Build an expanded golden evaluation set from REAL user traffic.

The existing golden set is 33 questions — too thin to trust a judge-model swap on.
This samples ~120 more from what people actually asked, weighted by the measured
traffic taxonomy so the eval set looks like production rather than like whatever
came to mind when the first set was written.

Read-only against Weaviate (UserQuery + ChatThread). User emails are never read
or written.

    venv/bin/python build_golden_set.py

Output: golden_questions_draft.json — for HUMAN REVIEW. This script deliberately
never touches golden_questions.json; promote the draft yourself once the labels
look right.

Labels are LLM-drafted and WILL contain mistakes. The two that matter most:
  - expect="abstain" means "returning nothing is the correct answer" (junk input,
    meta-requests, corpus gaps). A wrong abstain label silently inverts the
    abstention metric, so check these first.
  - expect="structured" means the question SHOULD resolve via the Entity KB. Until
    that collection is backfilled these will fail route_match — that is the point:
    it measures the gap rather than hiding it.
"""

import json
import re
import collections
import random

from dotenv import load_dotenv

load_dotenv()

from weaviate_client import get_client  # noqa: E402
from search.config import openai_client, GRADE_MODEL  # noqa: E402

OUT_FILE = "golden_questions_draft.json"
EXISTING_FILE = "golden_questions.json"
TARGET_SAMPLED = 120
BATCH = 12
random.seed(20260725)  # reproducible draft

# Traffic shares measured across 3,549 logged questions (1,567 unique). The sample
# is drawn to match this shape, so the eval set stresses the pipeline the way real
# users do — a third of it is how-to/practice, not evenly spread across types.
TAXONOMY = [
    ("howto_practice", 0.334, r"^\s*how (do|to|can|should|is|does)\b"),
    ("open_topical", 0.195, None),  # catch-all
    ("what_said", 0.169, r"what (does|did) (sai baba|swami|baba|bhagawan)\b"),
    ("personal_situation", 0.099, r"\b(i am|i'm|my |i feel|i struggle|struggling|help me|i want to)\b"),
    ("definitional", 0.071, r"^\s*what (is|are)\b"),
    ("factual_when", 0.064, r"^\s*when\b"),
    ("factual_who_where", 0.047, r"^\s*(who|whose|where)\b"),
    ("why", 0.022, r"^\s*why\b"),
    ("quoted_phrase", 0.008, r'"'),
    ("comparison", 0.005, r"\b(vs\.?|versus|difference between|compare)\b"),
]

# Regression cases the current golden set has no coverage for. These are real
# strings from the logs (or minimal variants), each pinned to the behavior we want
# rather than the behavior we currently get.
REGRESSION_CASES = [
    # Junk / out-of-domain. All three scored 0.55-0.59 in production — inside the
    # normal band — i.e. the pipeline dressed nonsense up as a result.
    {"question": "Hello", "expect": "abstain", "intent_hint": "out_of_domain"},
    {"question": "test", "expect": "abstain", "intent_hint": "out_of_domain"},
    {"question": "xyz123", "expect": "abstain", "intent_hint": "out_of_domain"},
    {"question": "how do i prepare french fries", "expect": "abstain", "intent_hint": "out_of_domain"},
    {"question": "Can you prescribe me medication for anxiety?", "expect": "abstain", "intent_hint": "out_of_domain"},
    # Meta — a request to the product, not the corpus.
    {"question": "Give me some follow ups", "expect": "abstain", "intent_hint": "meta"},
    # Factual. "When did Sai Baba say the best time to wake up?" was asked 145
    # times and is the clearest example of a factual question being semanticized.
    {"question": "When did Sai Baba say the best time to wake up?", "expect": "structured", "intent_hint": "factual"},
    {"question": "Who was Swami's mother?", "expect": "structured", "intent_hint": "factual"},
    {"question": "where was sathya sai baba born", "expect": "structured", "intent_hint": "factual"},
    {"question": "when was Swami born", "expect": "structured", "intent_hint": "factual"},
    # Org doctrine — asked many ways, historically always partial with n=1.
    {"question": "What are the guiding principles of SSE", "expect": "structured", "intent_hint": "org_doctrine"},
    {"question": "Nine Point Code of Conduct", "expect": "structured", "intent_hint": "org_doctrine"},
    {"question": "What are the guiding principles of Balvikas", "expect": "structured", "intent_hint": "org_doctrine"},
    # Quoted-phrase lookup — the exact-phrase promise must stay honest.
    {"question": '"Heroes, not Zeros"', "expect": "answer", "intent_hint": "named_text"},
    {"question": 'retrieve the discourse on "Love and Truth"', "expect": "answer", "intent_hint": "named_text"},
    # Corpus spelling drift (planner pins 'Geetha', users type 'Gita').
    {"question": "What does the Gita say about duty?", "expect": "answer", "intent_hint": "conceptual"},
    # Multi-turn: the follow-up only resolves against history.
    {"question": "What about for children?", "history": ["How do I meditate?"],
     "expect": "answer", "intent_hint": "aspect"},
    {"question": "Can you elaborate on that?", "history": ["What does Swami say about selfless service?"],
     "expect": "answer", "intent_hint": "aspect"},
    # Fragment — must not be inflated into an invented intent.
    {"question": "How", "expect": "abstain", "intent_hint": "out_of_domain"},
    # Typo'd real question (verbatim from the logs).
    {"question": "How do you have meaninful connect6ions with people", "expect": "answer", "intent_hint": "scenario"},
    # Comparison — both sides, no composed comparison.
    {"question": "What does Sai Baba say about detachment vs perseverance?",
     "expect": "answer", "intent_hint": "comparative"},
    # Listing — ordered enumeration, not relevance search.
    {"question": "return the first 5 chapters from Prema Vahini", "expect": "listing", "intent_hint": "listing"},
]


def _norm(q):
    return " ".join((q or "").lower().split())


def _is_junk(q):
    if len(q.split()) < 2:
        return True
    if re.search(r"__schema|query\s*\{|\bmutation\b|<[a-z]+>|SELECT\s|;\s*--", q, re.I):
        return True
    if not re.search(r"[a-zA-Z]{3,}", q):
        return True
    return False


def harvest():
    """Every distinct real question, with how often it was asked."""
    client = get_client()
    counts = collections.Counter()
    originals = {}

    uq = client.collections.get("UserQuery")
    for o in uq.query.fetch_objects(limit=10000, return_properties=["query_text"]).objects:
        q = (o.properties.get("query_text") or "").strip()
        if q:
            counts[_norm(q)] += 1
            originals.setdefault(_norm(q), q)

    ct = client.collections.get("ChatThread")
    for o in ct.query.fetch_objects(limit=5000, return_properties=["messages_json"]).objects:
        try:
            msgs = json.loads(o.properties.get("messages_json") or "[]")
        except Exception:
            continue
        if not isinstance(msgs, list):
            continue
        for m in msgs:
            if isinstance(m, dict):
                q = (m.get("question") or "").strip()
                if q:
                    counts[_norm(q)] += 1
                    originals.setdefault(_norm(q), q)

    client.close()
    return counts, originals


def classify(q):
    for name, _share, rx in TAXONOMY:
        if rx and re.search(rx, q, re.I):
            return name
    return "open_topical"


def sample(counts, originals, exclude):
    """Draw TARGET_SAMPLED questions matching the traffic taxonomy.

    Within each bucket the highest-volume questions are taken first — those are
    the ones whose failure costs the most — then the rest are sampled randomly so
    the long tail is represented too.
    """
    buckets = collections.defaultdict(list)
    for norm_q, n in counts.items():
        if norm_q in exclude or _is_junk(originals[norm_q]):
            continue
        buckets[classify(originals[norm_q])].append((n, originals[norm_q]))

    picked = []
    for name, share, _rx in TAXONOMY:
        want = round(TARGET_SAMPLED * share)
        pool = sorted(buckets.get(name, []), key=lambda x: -x[0])
        if not pool:
            continue
        head = pool[:max(1, want // 2)]          # highest-volume half
        tail_pool = pool[len(head):]
        tail = random.sample(tail_pool, min(want - len(head), len(tail_pool))) if tail_pool else []
        picked += [(name, q) for _n, q in head + tail]
    return picked


def draft_labels(pairs):
    """Ask a cheap model to label each question with expect + intent_hint."""
    system = (
        "You label evaluation questions for a search tool over English-translated "
        "spiritual discourses by Sathya Sai Baba. The tool returns DISCOURSE QUOTES "
        "only — it never synthesizes answers, and it abstains rather than guessing.\n\n"
        "WHAT THIS CORPUS ACTUALLY COVERS — it is broad, and it speaks directly to "
        "ordinary life. Roughly 2,400 discourses on: devotion, bhajans and singing, "
        "meditation and japa, food and diet (including vegetarianism and sathwik "
        "food), health, family life, parenting and children's education, work and "
        "duty, money, friendship and good company, anger, fear, desire, bad habits, "
        "the purpose of life, death and grief, service, truth, love, peace, "
        "non-violence, right conduct, scripture (Geetha, Ramayana, Bhagavatha, "
        "Upanishads), festivals, and the Sai organization's own codes.\n\n"
        "THE MOST COMMON LABELING MISTAKE IS OVER-ABSTAINING. A question being "
        "personal, emotional, mundane, or hard is NOT a reason to abstain — these "
        "discourses address exactly such struggles. 'I feel guilty about work-life "
        "balance', 'how do I stop bad habits', 'what is love', 'how do I sing "
        "bhajans', 'how do I raise my child well' are all ANSWER.\n\n"
        "For each question return:\n"
        '  "expect": one of "answer" | "abstain" | "structured" | "listing"\n'
        "    answer     = the discourses address this, even loosely or thematically. "
        "This is the DEFAULT — use it unless a rule below clearly applies.\n"
        "    abstain    = returning NOTHING is correct. ONLY for: nonsense or "
        "gibberish; greetings and tests ('hi', 'test'); single-word fragments; code "
        "or injection probes; requests aimed at the app itself; predicting an "
        "individual's future ('when will I get a job'); specific medical, legal, or "
        "financial advice; contemporary politics; or a genuinely secular topic the "
        "discourses have no bearing on (databases, cooking recipes, sports).\n"
        "    structured = a specific FACT — a person, place, date, or named "
        "organizational code — that should resolve to a canonical discourse rather "
        "than a thematic match. Includes 'when is the best time to wake up' "
        "(a prescribed hour), 'who was Swami's mother', 'the Nine Point Code'.\n"
        "    listing    = asks to enumerate chapters/volumes of a named collection in order\n"
        '  "intent_hint": one of conceptual, scenario, aspect, factual, named_text, '
        "occasion, comparative, org_doctrine, meta, out_of_domain, listing\n\n"
        'Respond ONLY with JSON: {"labels":[{"id":0,"expect":"answer","intent_hint":"conceptual"}]}'
    )
    out = {}
    for i in range(0, len(pairs), BATCH):
        chunk = pairs[i:i + BATCH]
        listing = "\n".join(f'[{j}] {q}' for j, (_c, q) in enumerate(chunk))
        try:
            r = openai_client.chat.completions.create(
                model=GRADE_MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": listing}],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            data = json.loads(r.choices[0].message.content or "{}")
            for item in data.get("labels", []):
                j = item.get("id")
                if isinstance(j, int) and 0 <= j < len(chunk):
                    out[i + j] = (item.get("expect"), item.get("intent_hint"))
        except Exception as e:
            print(f"  label batch {i} failed: {e}")
        print(f"  labelled {min(i + BATCH, len(pairs))}/{len(pairs)}")
    return out


def main():
    existing = json.load(open(EXISTING_FILE))
    exclude = {_norm(g["question"]) for g in existing}
    exclude |= {_norm(r["question"]) for r in REGRESSION_CASES}

    print("Harvesting real questions from Weaviate...")
    counts, originals = harvest()
    print(f"  {sum(counts.values())} events, {len(counts)} unique")

    picked = sample(counts, originals, exclude)
    print(f"Sampled {len(picked)} questions across {len(set(c for c, _ in picked))} buckets:")
    for name, n in collections.Counter(c for c, _ in picked).most_common():
        print(f"    {name:22s} {n}")

    print("Drafting labels...")
    labels = draft_labels(picked)

    VALID_EXPECT = {"answer", "abstain", "structured", "listing"}
    VALID_INTENT = {"conceptual", "scenario", "aspect", "factual", "named_text",
                    "occasion", "comparative", "org_doctrine", "meta",
                    "out_of_domain", "listing"}

    out = list(existing)  # keep the existing 33 verbatim
    n = len(existing)
    for r in REGRESSION_CASES:
        n += 1
        out.append({"id": f"r{n:03d}", **r})
    for idx, (bucket, q) in enumerate(picked):
        expect, intent = labels.get(idx, (None, None))
        if expect not in VALID_EXPECT:
            expect = "answer"
        if intent not in VALID_INTENT:
            intent = "conceptual"
        n += 1
        out.append({"id": f"s{n:03d}", "question": q, "expect": expect,
                    "intent_hint": intent, "_bucket": bucket})

    json.dump(out, open(OUT_FILE, "w"), indent=2)

    dist = collections.Counter(g["expect"] for g in out)
    print(f"\nWrote {OUT_FILE}: {len(out)} questions "
          f"({len(existing)} existing + {len(REGRESSION_CASES)} regression + {len(picked)} sampled)")
    print("  expect distribution:", dict(dist))
    print("\nREVIEW BEFORE PROMOTING. Check the 'abstain' labels first — a wrong one "
          "inverts the abstention metric. Then rename over golden_questions.json.")


if __name__ == "__main__":
    main()
