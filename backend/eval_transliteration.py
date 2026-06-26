"""Transliteration-robustness eval + HYBRID_ALPHA tuning.

Measures whether romanized/misspelled Sanskrit-Telugu queries retrieve the same
discourses as the clean English query for the same concept, across HYBRID_ALPHA.

Target (no manual gold labels): for each concept, the English query's top-K
discourses are the reference; recall@K = fraction of that set a variant recovers.
Operates at the retrieval+rerank level (alpha only affects retrieval). Each query
is expanded ONCE, then alpha is swept, so alpha is the only variable.

Run from backend/:  venv/bin/python eval_transliteration.py
"""
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import utils
from utils import search_passages, rerank_passages, RERANK_KEEP, plan_queries, PASSAGE_OVERFETCH

K = 10
ALPHAS = [0.3, 0.4, 0.5, 0.6, 0.7]
GOLD_ALPHA = 0.5  # the English reference is computed at the current baseline

CONCEPTS = {
    "non-violence":     {"english": "non-violence and not harming others",      "variants": ["ahimsa", "ahinsa"]},
    "truth":            {"english": "truth and truthfulness",                    "variants": ["sathya", "satya"]},
    "mind control":     {"english": "control and mastery of the mind",           "variants": ["mano nigraham", "manonigraham"]},
    "liberation":       {"english": "liberation and freedom from rebirth",       "variants": ["moksha", "moksa"]},
    "name of god":      {"english": "chanting and remembering the name of god",  "variants": ["namasmarana", "nama smarana"]},
    "devotion":         {"english": "devotion and love for god",                 "variants": ["bhakti", "bhakthi"]},
    "spiritual effort": {"english": "spiritual discipline and practice",         "variants": ["sadhana", "saadhana"]},
    "peace":            {"english": "inner peace and calm",                      "variants": ["shanti", "santhi", "shaanti"]},
    "selfless service": {"english": "selfless service to others",                "variants": ["seva", "sewa"]},
    "righteousness":    {"english": "righteousness and right conduct",           "variants": ["dharma", "dharmam"]},
    "desire":           {"english": "control of desire and attachment",          "variants": ["vairagya", "vairaagya"]},
    "illusion":         {"english": "worldly illusion",                          "variants": ["maya", "maaya"]},
}

# 1) Prepare every unique query ONCE via the planner (gloss/distill), then freeze it
#    so the sweep only varies alpha. (search_passages no longer self-expands.)
unique = set()
for c in CONCEPTS.values():
    unique.add(c["english"])
    unique.update(c["variants"])
prepared = {q: (plan_queries(q) or [q])[0] for q in unique}


def discourse_topk(query, alpha, k=K):
    utils.HYBRID_ALPHA = alpha
    pq = prepared.get(query, query)
    cands = search_passages(pq, PASSAGE_OVERFETCH)
    reranked = rerank_passages(pq, cands, RERANK_KEEP)
    seen, ids = set(), []
    for p in reranked:
        aid = p.get("article_id")
        if aid and aid not in seen:
            seen.add(aid)
            ids.append(aid)
        if len(ids) >= k:
            break
    return ids


# 2) Gold = English query's top-K at the baseline alpha.
GOLD = {c: set(discourse_topk(v["english"], GOLD_ALPHA)) for c, v in CONCEPTS.items()}

print(f"Concepts: {len(CONCEPTS)} | variants: {sum(len(v['variants']) for v in CONCEPTS.values())} | K={K}\n")
print(f"{'alpha':>5} | {'variant recall@K':>16} | {'english self-recall@K':>21}")
print("-" * 50)
best = None
for a in ALPHAS:
    vrec, erec = [], []
    for c, v in CONCEPTS.items():
        gold = GOLD[c]
        if not gold:
            continue
        erec.append(len(set(discourse_topk(v["english"], a)) & gold) / len(gold))
        for var in v["variants"]:
            vrec.append(len(set(discourse_topk(var, a)) & gold) / len(gold))
    vavg = sum(vrec) / len(vrec)
    eavg = sum(erec) / len(erec)
    print(f"{a:>5.1f} | {vavg:>16.3f} | {eavg:>21.3f}")
    if best is None or vavg > best[1]:
        best = (a, vavg)
print("-" * 50)
print(f"\nBest HYBRID_ALPHA for transliteration robustness: {best[0]} (variant recall@K={best[1]:.3f})")
