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

from .config import (openai_client, GRADE_MODEL, PLAN_MODEL, MAX_PLANNED_QUERIES,
                     REASONING_EFFORT, PLAN_IS_REASONING, PLAN_TEMPERATURE,
                     LISTING_MAX_RESULTS, LLM_SEED)
from .catalog import get_catalog, canonical_book, format_catalog_for_prompt

# The intent taxonomy the router classifies into; search_browse dispatches on it.
# Anything outside this set normalizes to "conceptual" (the plain semantic route),
# which is always a safe fallback.
KNOWN_INTENTS = {
    "conceptual", "scenario", "aspect", "factual", "named_text",
    "occasion", "comparative", "org_doctrine", "meta", "out_of_domain",
    "listing", "unanswerable",
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
            temperature=LLM_TEMPERATURE,
            seed=LLM_SEED,
        )
        expanded = (response.choices[0].message.content or "").strip().strip('"')
        return expanded or query
    except Exception as e:
        logging.error(f"expand_short_query failed: {e}")
        return query


# Words naming a WRITING. Refusal reason (a) — "nobody ranked the discourses" —
# only holds if the question is actually about one, so these are what make that
# reason true.
_TEXT_WORDS = re.compile(
    r"\b(discourse|discourses|vahini|vahinis|chapter|chapters|book|books|text|texts"
    r"|writing|writings|scripture|scriptures|teaching|teachings|volume|sutra)\b",
    re.I,
)

# Reason (c) — a prediction or ruling about ONE person's own life.
_PERSONAL_FUTURE = re.compile(
    r"\b(will i\b|will my\b|should i\b|shall i\b|am i going to\b|is it worth me\b"
    r"|for me to\b|my future\b|my career\b|my marriage\b)",
    re.I,
)

# Reason (b) — asking the app itself to pick, which no discourse can settle.
_ASKS_OUR_OPINION = re.compile(
    r"\b(do you think|in your opinion|what do you recommend|would you recommend"
    r"|read first|start with|begin with)\b",
    re.I,
)


# Syntax that belongs to code, not to a question about discourses: braces, dunder
# names, statement terminators, tags, GraphQL/SQL shapes. No question a devotee
# asks contains these, so matching one is decisive.
_LOOKS_LIKE_CODE = re.compile(
    r"[{}<>;]|__\w|\w__|\bselect\b.+\bfrom\b|\bdrop\s+table\b|</?\w+>", re.I
)


def _is_code_or_injection(message):
    """True for input that is a code/query snippet rather than a question.

    Deterministic on purpose. The router classifies these as out_of_domain most
    of the time, but it is only ever a *tendency* — "queryy { __typename }"
    started coming back `conceptual` after an unrelated edit elsewhere in the
    same prompt, which sends a GraphQL probe into a semantic search instead of
    the honest "this library holds discourses" reply. Cheap to settle in code.
    """
    return bool(_LOOKS_LIKE_CODE.search(message or ""))


# The one thing the model over-reads. The guard below only engages when a
# superlative is present, because that is the whole failure mode: a superlative
# about a practice or virtue looks, to the router, like a superlative about a text.
_SUPERLATIVE = re.compile(
    r"\b(best|most|greatest|highest|finest|foremost|supreme|ideal|top|"
    r"important|essential|primary)\b",
    re.I,
)


def _refusal_is_warranted(message):
    """True when "unanswerable" is defensible for this message.

    WHY THIS IS CODE AND NOT PROMPT TEXT
    The refusal boundary is the one place a routing error is actively harmful:
    declining a question the discourses DO answer is worse than answering a
    vague one loosely. It also proved to be the boundary the model holds least
    reliably — "the best time to wake up" (asked 145x in real traffic) came back
    unanswerable on 2 of 3 runs even while the prompt listed it verbatim as
    answerable. Three prompt rewrites were measured against eval_router.py and
    every one made it WORSE (2 failures -> 4 -> 5): the more prompt spent on this
    intent, the more the model reached for it. So the rule moved into code.

    This only ever DOWNGRADES a refusal to the normal semantic route; it can
    never cause one. A false negative here costs a search we would have run
    anyway, while a false positive costs a user their answer.

    The superlative is not the signal — the OBJECT is. "which is the best kind of
    yoga" is a question about a practice, which Swami ranks constantly; "which is
    the best discourse on meditation" is a question about a text, which nobody
    ranked. Same wording, opposite answerability.

    SCOPE: only questions containing a superlative are second-guessed at all.
    An earlier version overrode *every* refusal that did not match (a)-(c), which
    silently broke the ones the router declines for unrelated but sound reasons —
    the golden set caught "how do i hate?" and the bare fragment "What does swami
    say" being pushed into a semantic search. Those have no superlative, so the
    router's own verdict now stands.
    """
    m = message or ""
    if not _SUPERLATIVE.search(m):
        return True  # not the failure mode this guard exists for — do not interfere
    return bool(_TEXT_WORDS.search(m)
                or _PERSONAL_FUTURE.search(m)
                or _ASKS_OUR_OPINION.search(m))


# ===========================================================================
# Adaptive multi-query planning (the live pipeline's entry stage)
# ===========================================================================

# The semantic-query rules, shared verbatim between plan_queries and
# the merged router so semantic-query planning cannot silently regress.
QUERY_RULES = (
    "(1) Resolve any references to earlier turns so each query stands alone. "
    "(2) Distill long or emotional scenarios down to the underlying spiritual "
    "concept(s) being asked about; drop names and incidental narrative detail. "
    "EXCEPTION: when the user asks about a specific story, parable, person, or "
    "incident (e.g. 'the story of Alexander', 'what did Krishna teach Kuchela'), "
    "KEEP those proper nouns and story references verbatim in the query — they are "
    "the search anchor, not incidental detail. "
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
    "anger AND forgiveness AND attachment to money). When in doubt, return one."
)


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
            '{"queries":["..."],"occasion":null,"intent":"conceptual","is_comparison":false,"entities":[],'
            '"filters":{"book":null,"volume":null,"chapter_start":null,"chapter_end":null,'
            '"year_start":null,"year_end":null,"location":null,"occasion":null},'
            '"sort":null,"limit":null,"list_order":"first"}. Rules: '
            "(1) Resolve any references to earlier turns so each query stands alone. "
            "(2) Distill long or emotional scenarios down to the underlying spiritual "
            "concept(s) being asked about; drop names and incidental narrative detail. "
            "(3) For romanized Sanskrit/Telugu terms, EXPAND (never shorten): output the "
            "corpus spelling AND the user's spelling (skip the duplicate when they are the "
            "same) AND 2-3 English meaning words — no more, so the term stays the focus. "
            "Examples: 'ahinsa' -> 'ahimsa ahinsa non-violence'; 'satya' -> "
            "'sathya satya truth'; 'moksa' -> 'moksha liberation from rebirth'. Rule (2)'s "
            "distillation applies to long narratives, NOT to short term queries.(4) DEFAULT TO ONE QUERY. Most messages are about a single "
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
            # corpus's own vocabulary. Broaden them — but KEEP FOCUS: over-padding a bare
            # topic with loosely-related words dilutes retrieval so the top result drifts
            # off-concept (probing: 'faith' -> 'faith trust conviction and confidence in God'
            # surfaced a passage about trust, not about faith itself).
            "(9) EXPAND SHORT/BARE-TOPIC QUERIES — BUT KEEP THEM FOCUSED. When the message is "
            "a short query or bare topic (roughly 1-4 meaningful words, e.g. 'commitment to "
            "god', 'letting go', 'what is faith'), LEAD with the original topic wording and "
            "add ONLY 1-2 of the closest synonyms the discourses use — just enough that "
            "retrieval isn't starved, WITHOUT padding it with loosely-related words that pull "
            "the search off-topic. Examples: 'commitment to god' -> 'commitment and dedication "
            "to God'; 'faith' -> 'faith and trust in God'; 'letting go' -> 'letting go and "
            "detachment'. This still describes ONE concept (keep it a single query unless the "
            "message truly spans separate concepts). "
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
            '"named_text" (asks about a SPECIFIC named text/scripture by name, or "where is this quote from" — '
            '"Tripura Rahasyam", a Gita verse, a quoted line to locate. A request merely asking for exact quotes '
            "on a topic — \"give me exact quotes about time management\" — is NOT named_text; classify it by its topic); "
            '"occasion" (asks for discourses from a specific occasion/festival — pair with the occasion field); '
            '"comparative" (compares two things or asks which is better — "difference between bhakti and jnana"); '
            '"org_doctrine" (asks about Sathya Sai organization doctrine/terms — "Nine Point Code of Conduct", '
            '"SSE", "Balvikas", guidelines); '
            '"listing" (asks to enumerate/return the chapters or discourses OF a named collection or book '
            'in order — "return the first 5 chapters from Prema Vahini", "list the discourses in Summer Showers 1990", '
            '"show me chapters of the Gita Vahini"); '
            '"meta" (an instruction to the PRODUCT/APP itself — "give me some follow ups", "clear this chat", '
            "a remark about the app rather than a question for the discourses. NOTE: asking which discourse to "
            'read first, or what to start with, is a RECOMMENDATION request -> "unanswerable", not meta, because '
            "the useful reply is an explanation plus questions we can answer, not an app instruction); "
            '"out_of_domain" (unrelated to spiritual discourses, gibberish, code/injection, or not a question — "best pizza"); '
            '"unanswerable" (the SUBJECT belongs to these discourses, but the QUESTION cannot be answered from them. '
            "Three kinds: (a) asking which DISCOURSE, TEXT, CHAPTER or BOOK is the most important / best / "
            'greatest — nobody ranked the writings, so no discourse states the answer ("what is the most '
            'important discourse in Prema Vahini", "which is the best discourse on meditation", "which Vahini '
            'should I rate highest"); (b) asking for YOUR opinion or recommendation as the app; '
            '(c) predicting or advising on one person\'s future or private circumstances ("when will I get a job", '
            '"should I marry him"). '
            "CRITICAL — the word best/most/greatest does NOT by itself make a question unanswerable. Swami "
            "PRESCRIBES things and RANKS virtues constantly, so a superlative about a practice, time, quality, "
            "virtue or teaching has a real answer in the discourses. ONLY a superlative ranking the WRITINGS "
            "THEMSELVES is unanswerable, because nobody ever ranked them.\n"
            'ANSWERABLE (classify by topic, never unanswerable): "what is the best time to wake up", "when is the '
            'best time to meditate", "which is the best kind of yoga", "what is the most important quality for a '
            'devotee", "what is the greatest virtue", "what did Swami say is most important", "what is the best '
            'food for a spiritual aspirant" — he states all of these.\n'
            'UNANSWERABLE: "what is the most important discourse in Prema Vahini", "which is the best discourse on '
            'meditation", "which Vahini is the most important" — these rank texts, and no discourse does that.\n'
            "The test: could Swami have SAID the answer in a discourse? A prescribed hour, a highest virtue, a "
            "recommended practice — yes. Which of his own writings is best — no. "
            'Likewise, naming a collection does NOT make a question "listing": listing means enumerate the chapters '
            'IN ORDER; a question asking which chapter is best is "unanswerable". '
            "When in doubt, prefer an answerable intent — refusing a question the discourses do address is worse "
            "than answering one loosely. "
            "When unsure between conceptual/scenario/aspect, prefer the most specific that fits. "
            'IS_COMPARISON: set "is_comparison" true when the message compares two or more things or asks '
            "which of them is better/more important. "
            'ENTITIES: list any specific named people, places, texts, org terms, or festivals mentioned '
            '(e.g. ["Easwaramma"], ["Bhagavad Geetha"], ["Nine Point Code of Conduct"]); [] if none. '
            # FILTERS are not listing-only: a topic question constrained by
            # metadata ("what does the Geeta Vahini say about karma") keeps its
            # intent and gains a filter, which narrows retrieval instead of
            # replacing it.
            'FILTERS: set a filter ONLY when the user states it as a CONSTRAINT. A scripture or place '
            'named as SUBJECT MATTER is not a filter: "teachings of the Gita", "discourses about '
            'Brindavan\'s beauty" -> no filters. But naming a book as the SOURCE ("what does the Geeta '
            'Vahini say about X", "in the Prema Vahini") IS a book filter, and a place where discourses '
            'were DELIVERED ("in Brindavan", "at Kodaikanal") IS a location filter. Note "the Gita" / '
            '"Bhagavad Gita" is the scripture Swami comments on, NOT the book "Geeta Vahini" — only an '
            'explicit Vahini reference filters. Ordinals are 1-based chapters: "first five" -> '
            'chapter_start 1, chapter_end 5; "chapter 3" -> 3 and 3. Decades expand: "the 1970s" -> '
            'year_start 1970, year_end 1979; "from 1976" -> 1976 and 1976. location/occasion take one '
            'lowercase keyword spelled as the corpus spells it ("shivarathri", "dasara", "onam", '
            '"ugadi"). Map book names onto the EXACT strings listed below ("Gita Vahini" -> "Geeta '
            'Vahini"); if the book is not in the list, leave book null and keep the phrase in queries. '
            '"limit" is the count implied ("first 5" -> 5), else null. "sort" is "chapter" for '
            'within-book order, "date" for chronological, else null. "list_order" is "first", "last", '
            'or "all" — "the last 3 chapters" -> "last". '
            + format_catalog_for_prompt(get_catalog()) + " "
            # The only model-written prose the product shows a user, and it landed
            # generic ("This question cannot be answered from the discourses") on
            # exactly the questions where a person most needs a real explanation.
            # Stating the rule did not move it; the worked contrast did.
            #
            # Kept to two examples on purpose — the first draft used three, and
            # the extra length pushed a `named_text` question in the golden set
            # over to `conceptual`. Every line here is paid for by the questions
            # this prompt ALSO has to classify.
            'REASON (only when intent is "unanswerable"): ONE plain sentence to the user naming the specific '
            "thing THIS question asks for and why the discourses cannot supply it. Write it fresh — these are "
            "the shape, not phrasing to copy. A question about the future is a prediction, not a decision. "
            'GOOD: "The discourses are not ranked against one another, so none of them names a most important '
            'chapter of Prema Vahini." / "No discourse foretells when an individual will find work." '
            'BAD: "This question cannot be answered from the discourses." Never apologise, never speculate '
            "about what Swami would have said. Leave null for every other intent. "
            "JSON only, no prose."
        )
        response = openai_client.chat.completions.create(
            model=PLAN_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": hist_block + "Message: " + message},
            ],
            response_format={"type": "json_object"},
            **({"reasoning_effort": REASONING_EFFORT} if PLAN_IS_REASONING
               else {"temperature": PLAN_TEMPERATURE, "seed": LLM_SEED}),
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
            # Listing fields (used only when intent == "listing"), parsed defensively.
            # Filters go through the same validation the structured-search work
            # built: bounded ints, canonical book names, 1-based chapters, and a
            # chapter range dropped when there is no book to count within.
            trace_out["filters"] = _validate_filters(data.get("filters"), message=message)
            raw_book = (data.get("filters") or {}).get("book") if isinstance(data.get("filters"), dict) else None
            trace_out["book_requested"] = (
                raw_book.strip() if isinstance(raw_book, str) and raw_book.strip()
                and not trace_out["filters"].get("book") else None
            )
            trace_out["limit"] = _coerce_int(data.get("limit"), 1, LISTING_MAX_RESULTS)
            srt = data.get("sort")
            trace_out["sort"] = srt if srt in ("chapter", "date") else None
            lo = data.get("list_order")
            trace_out["list_order"] = lo if lo in ("first", "last", "all") else "first"

            intent = data.get("intent")
            intent = intent.strip().lower() if isinstance(intent, str) else ""
            intent = intent if intent in KNOWN_INTENTS else "conceptual"
            if _is_code_or_injection(message):
                intent = "out_of_domain"
            # A refusal that arrives WITH a concrete locator — a chapter, a
            # volume, a year — is incoherent: "show me discourse 10 of Sathya
            # Sai Speaks volume 14" is a browse request, and refusing it is the
            # exact failure this guard exists to prevent. Requires no
            # superlative, so "the most important discourse from Prema Vahini"
            # (book only, superlative present) still refuses correctly.
            _locators = ("chapter_start", "volume", "year_start")
            if (intent == "unanswerable"
                    and not _SUPERLATIVE.search(message or "")
                    and any(k in (trace_out.get("filters") or {}) for k in _locators)):
                logging.info(f"router: {message[:60]!r} refused but names a locator; treating as listing")
                intent = "listing"
                data["reason"] = None
            if intent == "unanswerable" and not _refusal_is_warranted(message):
                # See _refusal_is_warranted: three prompt rewrites could not hold
                # this boundary, so it is enforced in code instead.
                logging.info(f"router: downgraded unwarranted refusal of {message[:80]!r}")
                intent = "conceptual"
                data["reason"] = None
            trace_out["intent"] = intent
            trace_out["is_comparison"] = bool(data.get("is_comparison"))
            ents = data.get("entities")
            trace_out["entities"] = [e.strip() for e in ents if isinstance(e, str) and e.strip()] if isinstance(ents, list) else []
            # The refusal sentence is the only model-written text this product shows
            # a user, so it is bounded here rather than trusted: non-string, empty,
            # or runaway output is dropped and the frontend falls back to static
            # copy. A missing reason degrades the message, never the routing.
            rsn = data.get("reason")
            rsn = rsn.strip() if isinstance(rsn, str) else ""
            trace_out["unanswerable_reason"] = rsn[:300] if 12 <= len(rsn) <= 300 else None
        return qs[:MAX_PLANNED_QUERIES] or [message]
    except Exception as e:
        logging.error(f"plan_queries failed: {e}; using raw message.")
        if trace_out is not None:
            trace_out["fallback"] = True
        return [message]


# ===========================================================================
# Metadata filter validation (from the structured-search work)
#
# The router is asked for filters; nothing downstream trusts them until they
# pass through here. Pure — no LLM, no network beyond the catalog handed in —
# so test_router_unit.py can exercise it offline.
# ===========================================================================

FILTER_KEYS = (
    "book", "volume", "chapter_start", "chapter_end",
    "year_start", "year_end", "location", "occasion",
)

# A question ABOUT a thing, rather than a request for discourses delivered at or
# in it. Deliberately narrow: "what did Swami say in Brindavan" is a constraint
# and must not match.
_ASKS_WHAT_IT_MEANS = re.compile(
    r"\b(what is|what are|meaning of|significance of|what does .{0,24} mean)\b", re.I
)

# Sanity bounds for router-emitted years (the corpus spans 1953-2006; leave
# headroom rather than trusting the catalog to be loaded).
YEAR_MIN, YEAR_MAX = 1930, 2011


def _coerce_int(value, lo, hi):
    """Int within [lo, hi], else None. Accepts numeric strings."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def _clean_keyword(value):
    """Router-emitted location/occasion value -> lowercase keyword string."""
    if not isinstance(value, str):
        return None
    cleaned = " ".join(re.findall(r"[a-z]+", value.lower()))
    return cleaned or None


def _validate_filters(raw, catalog=None, message=""):
    """Router JSON -> a trusted filters dict (possibly empty). Never raises."""
    if not isinstance(raw, dict):
        return {}
    filters = {}

    book = canonical_book(raw.get("book"), catalog)
    if book:
        filters["book"] = book

    volume = _coerce_int(raw.get("volume"), 1, 99)
    if volume is not None:
        # A volume number implies Sathya Sai Speaks, the corpus's only
        # volume-organized book — infer it when the router left book null.
        if not book:
            inferred = canonical_book("Sathya Sai Speaks", catalog)
            if inferred:
                filters["book"] = inferred
        filters["volume"] = volume

    ch_start = _coerce_int(raw.get("chapter_start"), 1, 999)
    ch_end = _coerce_int(raw.get("chapter_end"), 1, 999)
    if ch_start is not None or ch_end is not None:
        ch_start = ch_start if ch_start is not None else ch_end
        ch_end = ch_end if ch_end is not None else ch_start
        if ch_end < ch_start:
            ch_start, ch_end = ch_end, ch_start
        # A chapter number is meaningless without a book to count within.
        if filters.get("book"):
            filters["chapter_start"], filters["chapter_end"] = ch_start, ch_end

    y_start = _coerce_int(raw.get("year_start"), YEAR_MIN, YEAR_MAX)
    y_end = _coerce_int(raw.get("year_end"), YEAR_MIN, YEAR_MAX)
    if y_start is not None or y_end is not None:
        y_start = y_start if y_start is not None else y_end
        y_end = y_end if y_end is not None else y_start
        if y_end < y_start:
            y_start, y_end = y_end, y_start
        filters["year_start"], filters["year_end"] = y_start, y_end

    for prop in ("location", "occasion"):
        value = _clean_keyword(raw.get(prop))
        if value:
            filters[prop] = value

    # "the Gita" / "Bhagavad Gita" is the scripture Swami COMMENTS ON; "Geeta
    # Vahini" is his own book about it. The router conflates them — the prompt
    # says so explicitly and it still filtered "what does the Gita teach about
    # karma?" down to the Geeta Vahini, which silently hides the rest of the
    # corpus from a question about the scripture. Settled in code: the book
    # filter survives only if the user actually wrote "Vahini".
    # Same confusion for festivals and places: an occasion/location named as the
    # SUBJECT ("what is the meaning of Dasara?") is not a request for discourses
    # delivered there. Filtering on it narrows the corpus to exactly the wrong
    # axis and can hide the discourse that explains the thing being asked about.
    if _ASKS_WHAT_IT_MEANS.search(message or ""):
        for key in ("occasion", "location"):
            if filters.pop(key, None):
                logging.info(f"router: dropped {key} filter — the question asks what it MEANS")

    if filters.get("book") == "Geeta Vahini" and not re.search(r"vahini", message or "", re.I):
        logging.info("router: dropped Geeta Vahini book filter — no 'Vahini' in the message")
        for key in ("book", "chapter_start", "chapter_end", "volume"):
            filters.pop(key, None)

    return filters
