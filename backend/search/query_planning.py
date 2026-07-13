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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import (
    openai_client,
    GRADE_MODEL,
    MAX_PLANNED_QUERIES,
    LLM_TEMPERATURE,
    LLM_SEED,
    LISTING_MAX_RESULTS,
)
from .catalog import get_catalog, canonical_book, format_catalog_for_prompt


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


# ===========================================================================
# Adaptive multi-query planning (the live pipeline's entry stage)
# ===========================================================================

# The semantic-query rules, shared verbatim between plan_queries and
# plan_search so the router cannot regress how semantic queries are planned.
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
            + QUERY_RULES +
            " JSON only, no prose."
        )
        response = openai_client.chat.completions.create(
            model=GRADE_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": hist_block + "Message: " + message},
            ],
            response_format={"type": "json_object"},
            temperature=LLM_TEMPERATURE,
            seed=LLM_SEED,
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


# ===========================================================================
# Structured search routing — intent + metadata filters + semantic queries
# ===========================================================================

FILTER_KEYS = (
    "book", "volume", "chapter_start", "chapter_end",
    "year_start", "year_end", "location", "occasion",
)

# Sanity bounds for router-emitted years (the corpus spans 1953-2006; leave
# headroom rather than trusting the catalog to be loaded).
YEAR_MIN, YEAR_MAX = 1930, 2011


@dataclass
class SearchPlan:
    """What one user message asks the search engine to do.

    intent:  "semantic" (topic search, today's behavior), "listing" (pure
             metadata enumeration — browse by book/chapter/year/place/occasion),
             or "hybrid" (topic search constrained by metadata filters).
    queries: standalone semantic sub-queries (empty for pure listings).
    filters: validated subset of FILTER_KEYS; chapter_* are 1-based as the
             user speaks them (storage is 0-based; converted at filter build).
    sort:    "chapter" | "date" | None — listing order preference.
    limit:   user-implied result count ("first five" -> 5), or None.
    """
    intent: str = "semantic"
    queries: List[str] = field(default_factory=list)
    filters: Dict[str, Any] = field(default_factory=dict)
    sort: Optional[str] = None
    limit: Optional[int] = None


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


def _validate_plan(data, message: str, catalog=None) -> SearchPlan:
    """Turn the router's raw JSON into a trusted SearchPlan.

    Pure (no LLM/network beyond the passed-in catalog) so it can be unit
    tested. Never raises: anything malformed degrades toward
    intent="semantic" with the raw message, i.e. today's behavior.
    """
    if not isinstance(data, dict):
        data = {}
    raw_filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}

    queries = [
        q.strip() for q in (data.get("queries") or [])
        if isinstance(q, str) and q.strip()
    ][:MAX_PLANNED_QUERIES]

    filters: Dict[str, Any] = {}
    book = canonical_book(raw_filters.get("book"), catalog)
    if book:
        filters["book"] = book
    volume = _coerce_int(raw_filters.get("volume"), 1, 99)
    if volume is not None:
        # A volume number implies Sathya Sai Speaks, the corpus's only
        # volume-organized book — infer it when the router left book null.
        if not book:
            inferred = canonical_book("Sathya Sai Speaks", catalog)
            if inferred:
                filters["book"] = inferred
        filters["volume"] = volume

    ch_start = _coerce_int(raw_filters.get("chapter_start"), 1, 999)
    ch_end = _coerce_int(raw_filters.get("chapter_end"), 1, 999)
    if ch_start is not None or ch_end is not None:
        ch_start = ch_start if ch_start is not None else ch_end
        ch_end = ch_end if ch_end is not None else ch_start
        if ch_end < ch_start:
            ch_start, ch_end = ch_end, ch_start
        # A chapter number is meaningless without a book to count within.
        if filters.get("book"):
            filters["chapter_start"], filters["chapter_end"] = ch_start, ch_end

    y_start = _coerce_int(raw_filters.get("year_start"), YEAR_MIN, YEAR_MAX)
    y_end = _coerce_int(raw_filters.get("year_end"), YEAR_MIN, YEAR_MAX)
    if y_start is not None or y_end is not None:
        y_start = y_start if y_start is not None else y_end
        y_end = y_end if y_end is not None else y_start
        if y_end < y_start:
            y_start, y_end = y_end, y_start
        filters["year_start"], filters["year_end"] = y_start, y_end

    location = _clean_keyword(raw_filters.get("location"))
    if location:
        filters["location"] = location
    occasion = _clean_keyword(raw_filters.get("occasion"))
    if occasion:
        filters["occasion"] = occasion

    intent = data.get("intent")
    if intent not in ("semantic", "listing", "hybrid"):
        intent = "semantic"
    # Guardrails: a structured intent with nothing to filter on is just a
    # semantic search; a semantic intent must never filter.
    if intent != "semantic" and not filters:
        intent = "semantic"
    if intent == "semantic":
        filters = {}
    if intent != "listing" and not queries:
        queries = [message]

    sort = data.get("sort") if data.get("sort") in ("chapter", "date") else None
    limit = _coerce_int(data.get("limit"), 1, LISTING_MAX_RESULTS)

    return SearchPlan(intent=intent, queries=queries, filters=filters, sort=sort, limit=limit)


def plan_search(message: str, history=None) -> SearchPlan:
    """Route one user message: detect structured intent, extract metadata
    filters, and plan semantic sub-queries — one gpt-4o-mini JSON call that
    replaces plan_queries at the head of the pipeline.

    On any failure, returns a semantic plan built from plan_queries' output,
    which is exactly today's behavior.
    """
    if not message or not isinstance(message, str):
        return SearchPlan(intent="semantic", queries=[message] if message else [])
    history = history or []
    try:
        catalog = get_catalog()
        hist_block = ""
        if history:
            recent = "\n".join(f"- {h}" for h in history[-3:] if isinstance(h, str) and h.strip())
            if recent:
                hist_block = "Earlier questions in this conversation (context only):\n" + recent + "\n\n"

        system = (
            "You route a user's message for a search tool over English-translated "
            "spiritual discourses by Sathya Sai Baba, organized into books with "
            "chapters, delivery dates, locations, and occasions. Respond ONLY with JSON:\n"
            '{"intent":"semantic|listing|hybrid",'
            '"queries":["standalone search query", "..."],'
            '"filters":{"book":null,"volume":null,"chapter_start":null,"chapter_end":null,'
            '"year_start":null,"year_end":null,"location":null,"occasion":null},'
            '"sort":null,"limit":null}\n\n'
            "INTENT — \"semantic\": the user asks about a topic, teaching, story, or "
            "concept (the default). \"listing\": the user asks to enumerate or browse "
            "discourses purely by book, chapter, date, place, or occasion, with NO topic "
            "(e.g. 'the first five discourses of Geeta Vahini', 'all discourses from 1976'). "
            "\"hybrid\": a topic question explicitly constrained by such metadata "
            "(e.g. 'discourses from 1976 about surrender'). When in doubt, choose "
            "\"semantic\" and leave every filter null.\n\n"
            "FILTERS — set a filter ONLY when the user explicitly states it as a "
            "constraint. A topic that mentions a scripture or place as SUBJECT MATTER is "
            "NOT a filter: 'teachings of the Gita' and 'discourses about Brindavan's "
            "beauty' are semantic with null filters. But naming a known book as the "
            "SOURCE ('what does the Geeta Vahini say about X', 'in the Prema Vahini') IS "
            "a book filter, and naming a place where discourses were DELIVERED ('in "
            "Brindavan', 'at Kodaikanal') IS a location filter — even inside a topic "
            "question (-> hybrid). Note 'the Gita'/'Bhagavad Gita' is the scripture "
            "Sathya Sai Baba comments on, NOT the book 'Geeta Vahini' — only an explicit "
            "Vahini reference filters; conversely, a question naming '<Name> Vahini' "
            "verbatim ALWAYS gets that book filter (topic questions -> hybrid). Ordinals map to 1-based chapters: 'first five' -> "
            "chapter_start 1, chapter_end 5; 'chapter 3' -> 3 and 3. Decades expand: "
            "'the 1970s' -> year_start 1970, year_end 1979; 'from 1976' -> 1976 and 1976. "
            "location/occasion take a single lowercase keyword ('given in Brindavan' -> "
            "location 'brindavan'; 'Dasara discourses' -> occasion 'dasara'), spelled the "
            "way the corpus spells it: 'shivarathri' (not 'shivaratri'), 'dasara', "
            "'onam', 'ugadi'. Map book "
            "names the user writes onto the EXACT known strings below (e.g. 'Gita Vahini' "
            "-> 'Geeta Vahini'); if the user names a book that is not in the list, leave "
            "book null and keep the phrase in the semantic queries. `limit` is the count "
            "the user implied ('first five' -> 5), else null. `sort` is \"chapter\" for "
            "within-book order, \"date\" for chronological listings, else null.\n\n"
            + format_catalog_for_prompt(catalog) + "\n\n"
            "QUERIES — for semantic and hybrid intents, plan 1 to 4 standalone search "
            "queries with these rules: " + QUERY_RULES + " For pure listings, return "
            'queries as [].\n\n'
            "EXAMPLES:\n"
            "'return the first five discourses of the Gita Vahini' -> {\"intent\":\"listing\","
            "\"queries\":[],\"filters\":{\"book\":\"Geeta Vahini\",\"chapter_start\":1,"
            "\"chapter_end\":5},\"sort\":\"chapter\",\"limit\":5}\n"
            "'all discourses from 1976' -> {\"intent\":\"listing\",\"queries\":[],"
            "\"filters\":{\"year_start\":1976,\"year_end\":1976},\"sort\":\"date\",\"limit\":null}\n"
            "'discourses from the 1970s about surrender' -> {\"intent\":\"hybrid\","
            "\"queries\":[\"surrender to God and self-surrender\"],\"filters\":{"
            "\"year_start\":1970,\"year_end\":1979},\"sort\":null,\"limit\":null}\n"
            "'what does the Geeta Vahini say about karma?' -> {\"intent\":\"hybrid\","
            "\"queries\":[\"karma action and its consequences\"],\"filters\":{"
            "\"book\":\"Geeta Vahini\"},\"sort\":null,\"limit\":null}\n"
            "'what does the Gita teach about karma?' -> {\"intent\":\"semantic\","
            "\"queries\":[\"Bhagavad Gita teachings on karma action and duty\"],"
            "\"filters\":{},\"sort\":null,\"limit\":null}\n"
            "'Dasara discourses' -> {\"intent\":\"listing\",\"queries\":[],"
            "\"filters\":{\"occasion\":\"dasara\"},\"sort\":\"date\",\"limit\":null}\n"
            "'the story of Alexander' -> {\"intent\":\"semantic\",\"queries\":"
            "[\"the story of Alexander\"],\"filters\":{},\"sort\":null,\"limit\":null}\n"
            "'discourse 10 of volume 14' -> {\"intent\":\"listing\",\"queries\":[],"
            "\"filters\":{\"book\":\"Sathya Sai Speaks\",\"volume\":14,\"chapter_start\":10,"
            "\"chapter_end\":10},\"sort\":\"chapter\",\"limit\":null}\n"
            "'Summer Showers discourses from 1976' -> {\"intent\":\"listing\",\"queries\":[],"
            "\"filters\":{\"book\":\"Summer Showers\",\"year_start\":1976,\"year_end\":1976},"
            "\"sort\":\"date\",\"limit\":null}\n"
            "'what did Swami say about seva in Kodaikanal?' -> {\"intent\":\"hybrid\","
            "\"queries\":[\"seva selfless service and helping others\"],\"filters\":{"
            "\"location\":\"kodaikanal\"},\"sort\":null,\"limit\":null}\n\n"
            "Unstated filter fields may be omitted or null. JSON only, no prose."
        )
        response = openai_client.chat.completions.create(
            model=GRADE_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": hist_block + "Message: " + message},
            ],
            response_format={"type": "json_object"},
            temperature=LLM_TEMPERATURE,
            seed=LLM_SEED,
        )
        raw = (response.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        plan = _validate_plan(json.loads(raw), message, catalog)
        logging.info(
            f"plan_search: intent={plan.intent} filters={plan.filters} "
            f"queries={plan.queries} sort={plan.sort} limit={plan.limit}"
        )
        return plan
    except Exception as e:
        logging.error(f"plan_search failed: {e}; falling back to plan_queries.")
        return SearchPlan(intent="semantic", queries=plan_queries(message, history))
