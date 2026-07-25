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

from .config import openai_client, GRADE_MODEL, MAX_PLANNED_QUERIES, PLAN_TEMPERATURE

# The intent taxonomy the router classifies into; search_browse dispatches on it.
# Anything outside this set normalizes to "conceptual" (the plain semantic route),
# which is always a safe fallback.
KNOWN_INTENTS = {
    "conceptual", "scenario", "aspect", "factual", "named_text",
    "occasion", "comparative", "org_doctrine", "meta", "out_of_domain",
}


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

def plan_queries(message: str, history=None, trace_out=None) -> list:
    """Turn a (possibly long, multi-turn) user message into 1-N standalone search
    queries for the English discourse corpus. One gpt-4o-mini JSON call that:
      - resolves references to earlier turns so each query stands alone,
      - distills emotional/narrative scenarios to the underlying spiritual concept(s),
      - glosses romanized Sanskrit/Telugu terms (corpus spelling + variants + meaning),
      - adaptively returns ONE query for a single concept, or 2-4 when the message
        clearly spans multiple distinct concepts.
    Falls back to [message] on any failure (today's behavior). Subsumes
    expand_short_query for the search path.

    ``trace_out`` (optional dict): when provided,
      - ``trace_out['fallback']`` is set True if planning fell back to the raw
        message, so the transparency trace can distinguish a deliberate
        single-query plan from a planner crash;
      - ``trace_out['occasion']`` is set to the occasion/festival name when the
        message asks for discourses from a specific occasion (e.g. "Dasara"),
        which the pipeline turns into a Weaviate metadata filter;
      - ``trace_out['intent']`` is set to "factual" when the message is a
        biographical/factual question the discourse search can't directly answer,
        so the UI can show an honest note.
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
            'STANDALONE search queries as JSON: '
            '{"queries":["..."],"occasion":null,"intent":"conceptual","is_comparison":false,"entities":[]}. Rules: '
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
            # Rules 5-8 close nuance-drop holes found by adversarial probing: the
            # distillation rule (2) alone erased aspects, stories, and enumerations,
            # and invented topics for gibberish.
            "(5) PRESERVE THE ASKED-ABOUT ASPECT. When the message asks about an aspect of a "
            "topic — the value of X, stages of X, obstacles to X, signs of X, why X, how to X, "
            "difference between X and Y — at least one query MUST keep that aspect wording "
            "(e.g. 'value of truth', 'stages of meditation'); never reduce the message to the "
            "bare topic alone. "
            "(6) STORY LOOKUPS ARE LITERAL. When the message asks for a story, parable, or "
            "example Swami tells, keep the concrete imagery in the query (e.g. 'story "
            "sandalwood tree') — the narrative detail IS the search target; do not abstract "
            "it to its moral or symbolism. "
            "(7) NEVER GUESS ENUMERATIONS. When the message references a named list (the "
            "'three P's', the 'five human values'), keep the reference verbatim in the query; "
            "do not substitute your own guess of the list's contents. "
            "(8) NO INVENTED INTENT. If the message has no discernible meaning or topic "
            "(gibberish, random characters), return it unchanged as the single query — do NOT "
            "invent a spiritual topic for it. "
            # Rule 9 added after auditing real questions: terse 1-4 word inputs ("commitment
            # to god", "what is faith") retrieved too little because a bare facet misses the
            # corpus's own vocabulary. Broaden them with synonyms/closely-related terms.
            "(9) EXPAND SHORT/BARE-TOPIC QUERIES. When the message is a short query or bare "
            "topic (roughly 1-4 meaningful words, e.g. 'commitment to god', 'letting go', "
            "'what is faith'), enrich the query with the synonymous and closely-related "
            "vocabulary the discourses actually use, so retrieval isn't starved. Examples: "
            "'commitment to god' -> 'commitment surrender dedication self-offering devotion "
            "to God'; 'faith' -> 'faith trust conviction and confidence in God'; 'letting go' "
            "-> 'letting go detachment renunciation non-attachment'. This still describes ONE "
            "concept (keep it a single query unless the message truly spans separate concepts). "
            "OCCASION: if the message asks for discourses from a specific occasion or festival, "
            'set "occasion" to that occasion\'s name using the corpus spelling — e.g. "Dasara", '
            '"Shivarathri", "Guru Purnima", "Christmas", "Ugadi", "Onam", "Krishna Jayanthi", '
            '"Summer Course" — otherwise null. '
            # INTENT drives routing (search/pipeline.py): the pipeline sends each
            # intent to the retrieval strategy that fits it, so classify carefully.
            'INTENT: classify the message into exactly one of: '
            '"conceptual" (a spiritual concept or bare topic — "faith", "what is karma"); '
            '"scenario" (a personal situation asking for guidance — "I struggle with X, how do I…"); '
            '"aspect" (asks about a specific aspect of a topic — the value/stages/obstacles/why/how of X); '
            '"factual" (a biographical or factual question about a specific person, place, date, or event — '
            '"Who was Swami\'s mother?", "When was Baba born?", "Where is Puttaparthi?"); '
            '"named_text" (asks about a specific named text/scripture, or "where is this quote from" — '
            '"Tripura Rahasyam", a Gita verse, a quoted line to locate); '
            '"occasion" (asks for discourses from a specific occasion/festival — pair with the occasion field); '
            '"comparative" (compares two things or asks which is better — "difference between bhakti and jnana"); '
            '"org_doctrine" (asks about Sathya Sai organization doctrine/terms — "Nine Point Code of Conduct", '
            '"SSE", "Balvikas", guidelines); '
            '"meta" (a request to the product itself, not the corpus — "give me some follow ups"); '
            '"out_of_domain" (unrelated to spiritual discourses, or gibberish — "best pizza"). '
            "When unsure between conceptual/scenario/aspect, prefer the most specific that fits. "
            'IS_COMPARISON: set "is_comparison" true when the message compares two or more things or asks '
            "which of them is better/more important. "
            'ENTITIES: list any specific named people, places, texts, org terms, or festivals mentioned '
            '(e.g. ["Easwaramma"], ["Bhagavad Geetha"], ["Nine Point Code of Conduct"]); [] if none. '
            "JSON only, no prose."
        )
        response = openai_client.chat.completions.create(
            model=GRADE_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": hist_block + "Message: " + message},
            ],
            response_format={"type": "json_object"},
            temperature=PLAN_TEMPERATURE,
        )
        raw = (response.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        data = json.loads(raw)
        qs = [q.strip() for q in data.get("queries", []) if isinstance(q, str) and q.strip()]
        # The routing fields are optional and best-effort: anything malformed
        # degrades to a safe default so a bad LLM response never breaks retrieval.
        if trace_out is not None:
            occasion = data.get("occasion")
            trace_out["occasion"] = occasion.strip() if isinstance(occasion, str) and occasion.strip() else None
            # Normalize intent to the known taxonomy; unknown/missing -> "conceptual"
            # (the plain semantic route), which is always a safe fallback.
            intent = data.get("intent")
            intent = intent.strip().lower() if isinstance(intent, str) else ""
            trace_out["intent"] = intent if intent in KNOWN_INTENTS else "conceptual"
            trace_out["is_comparison"] = bool(data.get("is_comparison"))
            ents = data.get("entities")
            trace_out["entities"] = [e.strip() for e in ents if isinstance(e, str) and e.strip()] if isinstance(ents, list) else []
        return qs[:MAX_PLANNED_QUERIES] or [message]
    except Exception as e:
        logging.error(f"plan_queries failed: {e}; using raw message.")
        if trace_out is not None:
            trace_out["fallback"] = True
        return [message]
