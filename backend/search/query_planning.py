"""Stage 1 of the pipeline — turn a raw user message into search queries.

The corpus is English-translated discourses, but users phrase things in long
multi-turn scenarios and in romanized Sanskrit/Telugu with inconsistent spelling
(e.g. "satya" vs the corpus's "sathya"). These helpers normalize a message into
1–N standalone, glossed search queries before any retrieval happens, so BM25 and
the vector search hit the corpus regardless of how the user phrased things.

``plan_queries`` is the one used by the live pipeline; ``expand_short_query`` is
its single-query predecessor, kept because it is still a useful standalone helper.
"""

import re
import json
import logging

from .config import openai_client, GRADE_MODEL, MAX_PLANNED_QUERIES


# ===========================================================================
# Single-query expansion (legacy helper, superseded by plan_queries for search)
# ===========================================================================

def expand_short_query(query: str) -> str:
    """Expand a short query into a richer search phrase.

    The corpus is English-translated discourses, but users search Sanskrit/Telugu
    concepts in romanized Latin with inconsistent spelling (e.g. "satya" vs the
    corpus's "sathya"). So for transliterated terms we expand to BOTH the English
    meaning (what actually matches the English text) and common spelling variants,
    making BM25 and the vector search hit the corpus regardless of romanization.

    Best-effort and cheap: a single gpt-4o-mini call. Longer queries pass through
    unchanged, and any error returns the original query.
    """
    if not query or not isinstance(query, str):
        return query
    if len(query.split()) > 6:
        return query
    try:
        response = openai_client.chat.completions.create(
            model=GRADE_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You expand a short search query for a corpus of English-translated "
                        "spiritual discourses by Sathya Sai Baba. Rewrite it into a single "
                        "richer search phrase that captures its intent. IMPORTANT: if the "
                        "query contains romanized Sanskrit or Telugu terms, include (a) the "
                        "term's English meaning and (b) common alternative spellings of the "
                        "term, so it matches the English text regardless of transliteration. "
                        "Examples: 'ahinsa' -> 'ahimsa ahinsa non-violence and not harming "
                        "others'; 'mano nigraham' -> 'mano nigraham control and mastery of the "
                        "mind'; 'satya' -> 'sathya satya truth and truthfulness'; 'dharma' -> "
                        "'dharma righteousness and right conduct in spiritual life'. Return "
                        "only the expanded phrase, with no quotes or extra commentary."
                    ),
                },
                {"role": "user", "content": query},
            ],
        )
        expanded = (response.choices[0].message.content or "").strip().strip('"')
        return expanded or query
    except Exception as e:
        logging.error(f"expand_short_query failed: {e}")
        return query


# ===========================================================================
# Adaptive multi-query planning (the live pipeline's entry stage)
# ===========================================================================

def plan_queries(message: str, history=None) -> list:
    """Turn a (possibly long, multi-turn) user message into 1-N standalone search
    queries for the English discourse corpus. One gpt-4o-mini JSON call that:
      - resolves references to earlier turns so each query stands alone,
      - distills emotional/narrative scenarios to the underlying spiritual concept(s),
      - glosses romanized Sanskrit/Telugu terms (corpus spelling + variants + meaning),
      - adaptively returns ONE query for a single concept, or 2-4 when the message
        clearly spans multiple distinct concepts.
    Falls back to [message] on any failure (today's behavior). Subsumes
    expand_short_query for the search path.
    """
    if not message or not isinstance(message, str):
        return [message] if message else []
    history = history or []
    try:
        hist_block = ""
        if history:
            recent = "\n".join(f"- {h}" for h in history[-3:] if isinstance(h, str) and h.strip())
            if recent:
                hist_block = "Earlier questions in this conversation (context only):\n" + recent + "\n\n"
        system = (
            "You convert a user's message into search queries for a corpus of "
            "English-translated spiritual discourses by Sathya Sai Baba. Output 1 to 4 "
            'STANDALONE search queries as JSON: {"queries":["..."]}. Rules: '
            "(1) Resolve any references to earlier turns so each query stands alone. "
            "(2) Distill long or emotional scenarios down to the underlying spiritual "
            "concept(s) being asked about; drop names and incidental narrative detail. "
            "(3) For romanized Sanskrit/Telugu terms, EXPAND (never shorten): output BOTH the "
            "corpus spelling AND the user's spelling AND 3-5 English meaning words. Examples: "
            "'ahinsa' -> 'ahimsa ahinsa non-violence and not harming others'; 'satya' -> "
            "'sathya satya truth and truthfulness'; 'moksa' -> 'moksha liberation freedom from "
            "rebirth'. Rule (2)'s distillation applies to long narratives, NOT to short term "
            "queries. (4) DEFAULT TO ONE QUERY. Most messages are about a single "
            "concept -> return a single query (e.g. 'controlling the mind' -> [\"control and "
            "mastery of the mind\"]). Different sub-aspects, synonyms, or rephrasings of the "
            "SAME concept are NOT multiple concepts. Return 2-4 queries ONLY when the message "
            "explicitly involves clearly different, separable concepts (e.g. a scenario about "
            "anger AND forgiveness AND attachment to money). When in doubt, return one. "
            "JSON only, no prose."
        )
        response = openai_client.chat.completions.create(
            model=GRADE_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": hist_block + "Message: " + message},
            ],
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        data = json.loads(raw)
        qs = [q.strip() for q in data.get("queries", []) if isinstance(q, str) and q.strip()]
        return qs[:MAX_PLANNED_QUERIES] or [message]
    except Exception as e:
        logging.error(f"plan_queries failed: {e}; using raw message.")
        return [message]
