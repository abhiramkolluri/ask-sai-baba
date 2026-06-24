import os
import re
import json
import logging
from datetime import datetime
from typing import List, Dict, Any
from dotenv import load_dotenv
import configparser

from openai import OpenAI
import weaviate

from fine_tuning import load_fine_tuned_model_id_from_file
from weaviate_client import get_client
from weaviate.classes.query import Filter, MetadataQuery

# Configure logging
logging.basicConfig(
    filename='embedding_generation.log', 
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

load_dotenv()
config = configparser.ConfigParser()

# Setting up OpenAI
openai_api_key = os.getenv('OPENAI_API_KEY')
if not openai_api_key:
    config.read('openai.ini')
    openai_api_key = config.get('OpenAI', 'api_key', fallback=None)

openai_client = OpenAI(api_key=openai_api_key)

# ---------------------------------------------------------------------------
# Passage-search pipeline configuration
#
# Introduces hybrid search (BM25 + vector), query expansion, Cohere reranking,
# and LLM relevance grading over the Passage collection. None of this existed
# in the codebase before; the legacy Article path (search_browse) is untouched.
#
# COHERE_API_KEY is read from the environment and is OPTIONAL — the pipeline
# degrades gracefully when it is absent (see rerank_passages). For reranking in
# deployment, set COHERE_API_KEY in the Elastic Beanstalk environment.
# ---------------------------------------------------------------------------
COHERE_API_KEY = os.getenv("COHERE_API_KEY")

PASSAGE_OVERFETCH = 40
RERANK_KEEP = 15
RERANK_MODEL = "rerank-v3.5"
GRADE_MODEL = "gpt-4o-mini"
JUDGE_MODEL = "gpt-4o"  # stronger judge for unified grade + verbatim quote extraction
GRADE_MIN_RELEVANCE = 0.5
HYBRID_ALPHA = 0.5

# When the grader rejects everything but the caller still needs grounding
# context (chat, allow_empty=False), fall back to this many top reranked
# (pre-grade) passages aggregated to discourses. Browse (allow_empty=True)
# never falls back — an empty result is the honest answer there.
CHAT_FALLBACK_DISCOURSES = 3

def check_vector_store_health():
    """Check if Weaviate is properly initialized and has data."""
    try:
        client = get_client()
        if client and client.is_live():
            articles = client.collections.get("Article")
            count = articles.aggregate.over_all(total_count=True)
            return count.total_count > 0
        return False
    except Exception as e:
        logging.error(f"Vector store health check failed: {e}")
        return False

def extract_quoted_phrase(query: str):
    """
    Extract the first double-quoted phrase from a user query string.

    If the user's message contains a phrase wrapped in double quotes,
    this function returns that phrase in lowercase with surrounding
    whitespace stripped. If no quoted phrase is found, returns None.

    Examples:
        'retrieve the discourse on "Love and Truth"' -> 'love and truth'
        '"Duty and Devotion"'                        -> 'duty and devotion'
        'tell me about love and truth'               -> None

    Args:
        query: The raw user query string, which may contain quoted substrings.

    Returns:
        The first quoted phrase as a lowercase string, or None if not found.
    """
    if not query or not isinstance(query, str):
        return None
    match = re.search(r'"([^"]+)"', query)
    if match:
        return match.group(1).strip().lower()
    return None

def get_embedding(text):
    """Generate an embedding for the given text using OpenAI directly."""
    if not text or not isinstance(text, str):
        return None
    try:
        response = openai_client.embeddings.create(
            model="text-embedding-3-large",
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        logging.error(f"Error generating embedding: {e}")
        return None

def search_browse(query: str, limit: int = 5, exact_phrase: str = None, allow_empty: bool = True) -> List[Dict[str, Any]]:
    """Search discourses via the passage pipeline (hybrid -> rerank -> grade -> aggregate).

    Keeps the quoted-phrase exact-match shortcut from the legacy implementation.
    `allow_empty` controls the no-results behavior:
      - True (browse): return [] when nothing directly answers the query.
      - False (chat): fall back to the top reranked passages so the generator
        still has grounding context.

    The original near_text implementation is preserved verbatim as
    search_browse_articles_legacy() for rollback.
    """
    # (a) Quoted-phrase shortcut: try exact match first, exactly as before.
    if exact_phrase:
        exact_results = search_exact(exact_phrase, limit=limit)
        if exact_results:
            # Normalize to the pipeline output shape so downstream consumers
            # (format_docs, frontend) see matched_passage/passage_index.
            for r in exact_results:
                r.setdefault("matched_passage", r.get("content", ""))
                r.setdefault("passage_index", 0)
            return select_best_sentences(query, exact_results)
        # (b) No exact match — fall through using the phrase as the query, but
        # route into the passage pipeline instead of near_text.
        query = exact_phrase

    # (c) Run the passage pipeline.
    candidates = search_passages(query, PASSAGE_OVERFETCH)
    reranked = rerank_passages(query, candidates, RERANK_KEEP)
    # Unified extractive grade: judges relevance AND extracts the verbatim answering
    # quote in one pass, attaching `best_sentence` to each kept passage.
    graded = grade_and_quote_passages(query, reranked)
    results = aggregate_to_discourses(graded, limit)

    # (d) Handle the empty case.
    if not results:
        if allow_empty:
            return []
        # Chat path: ground the generator on the top reranked (pre-grade)
        # passages even though the grader was not satisfied.
        fallback = aggregate_to_discourses(reranked[:CHAT_FALLBACK_DISCOURSES], CHAT_FALLBACK_DISCOURSES)
        return select_best_sentences(query, fallback)

    # Quotes are already attached by the unified grade step.
    return results

def search_browse_articles_legacy(query: str, limit: int = 5, exact_phrase: str = None) -> List[Dict[str, Any]]:
    """Search articles using Weaviate's native near_text capabilities.

    Verbatim copy of the original search_browse() body, preserved as a rollback
    path before Prompt 5 replaces search_browse with the passage pipeline.
    """
    # If an exact phrase was extracted from a quoted user query, attempt exact
    # matching first (title priority, then content). Only fall through to
    # semantic search if no exact results are found, in which case we search
    # on just the phrase rather than the full raw query sentence.
    if exact_phrase:
        exact_results = search_exact(exact_phrase, limit=limit)
        if exact_results:
            return exact_results
        # No exact matches found — run semantic search on the phrase alone,
        # not the full raw query, so the vector search is focused.
        query = exact_phrase

    try:
        client = get_client()
        if not client:
            logging.error("Weaviate client not available for search.")
            return []

        articles = client.collections.get("Article")
        response = articles.query.near_text(
            query=query,
            limit=limit,
            return_metadata=weaviate.classes.query.MetadataQuery(distance=True)
        )

        results = []
        for obj in response.objects:
            results.append({
                "_id": str(obj.uuid),
                "title": obj.properties.get("title", "Untitled"),
                "content": obj.properties.get("content", ""),
                "score": 1 - (obj.metadata.distance or 0),
                "location": obj.properties.get("location", ""),
                "occasion": obj.properties.get("occasion", ""),
                "link": obj.properties.get("link", ""),
                "collection": obj.properties.get("collection_name", ""),
                "date": obj.properties.get("date", ""),
            })
        return results
    except Exception as e:
        logging.error(f"Weaviate search failed: {e}")
        return []

# ---------------------------------------------------------------------------
# Passage-search pipeline
#
# search_passages -> rerank_passages -> grade_passages -> aggregate_to_discourses
#
# Hybrid retrieval over chunked passages, optional Cohere reranking, an LLM
# relevance grade, then aggregation back up to one result per discourse. New in
# this codebase; not wired into any endpoint yet.
# ---------------------------------------------------------------------------

def expand_short_query(query: str) -> str:
    """Expand a very short (1-3 word) query into a richer search phrase.

    Best-effort and cheap: a single gpt-4o-mini call. Longer queries pass
    through unchanged, and any error returns the original query.
    """
    if not query or not isinstance(query, str):
        return query
    if len(query.split()) > 3:
        return query
    try:
        response = openai_client.chat.completions.create(
            model=GRADE_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Expand the user's short search query into a single richer search "
                        "phrase capturing its likely intent for searching a collection of "
                        "spiritual discourses by Sathya Sai Baba. For example 'dharma' -> "
                        "'the meaning and practice of dharma in spiritual life'. Return only "
                        "the expanded phrase, with no quotes or extra commentary."
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

def search_passages(query: str, overfetch: int = PASSAGE_OVERFETCH) -> List[Dict[str, Any]]:
    """Hybrid (BM25 + vector) search over the Passage collection."""
    try:
        expanded = expand_short_query(query)
        client = get_client()
        if not client:
            logging.error("Weaviate client not available for passage search.")
            return []

        passages = client.collections.get("Passage")
        response = passages.query.hybrid(
            query=expanded,
            alpha=HYBRID_ALPHA,
            limit=overfetch,
            query_properties=["content", "title"],
            return_metadata=MetadataQuery(score=True)
        )

        results = []
        for obj in response.objects:
            props = obj.properties
            results.append({
                "_id": str(obj.uuid),
                "article_id": props.get("article_id", ""),
                "chunk_index": props.get("chunk_index", 0),
                "content": props.get("content", ""),
                "title": props.get("title", ""),
                "location": props.get("location", ""),
                "occasion": props.get("occasion", ""),
                "link": props.get("link", ""),
                "collection_name": props.get("collection_name", ""),
                "date_authored": props.get("date_authored", ""),
                "score": obj.metadata.score or 0.0,
            })
        return results
    except Exception as e:
        logging.error(f"Passage hybrid search failed: {e}")
        return []

def rerank_passages(query: str, candidates: List[Dict[str, Any]], keep: int = RERANK_KEEP) -> List[Dict[str, Any]]:
    """Rerank passage candidates with Cohere, or fall back to hybrid-score order.

    Works without Cohere: if COHERE_API_KEY is unset (or the call fails), the
    candidates are simply sorted by their hybrid score and truncated.
    """
    if not candidates:
        return []

    if not COHERE_API_KEY:
        logging.warning(
            "COHERE_API_KEY not set; skipping Cohere rerank and sorting by hybrid "
            "score. Set COHERE_API_KEY in the EB environment to enable reranking."
        )
        ranked = sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)
        return ranked[:keep]

    try:
        import cohere
        co = cohere.Client(COHERE_API_KEY)
        documents = [c.get("content", "") for c in candidates]
        response = co.rerank(
            model=RERANK_MODEL,
            query=query,
            documents=documents,
            top_n=min(keep, len(documents)),
        )
        reranked = []
        for result in response.results:
            candidate = dict(candidates[result.index])
            candidate["rerank_score"] = result.relevance_score
            reranked.append(candidate)
        return reranked
    except Exception as e:
        logging.error(f"Cohere rerank failed: {e}; falling back to hybrid-score order.")
        ranked = sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)
        return ranked[:keep]

def _split_sentences(text: str) -> List[str]:
    """Split text into sentences with a lightweight regex (no nltk dependency).

    Drops empty/trivial fragments. Abbreviations (e.g. "Mr.") can over-split, but
    that is acceptable: the rerank below still picks the most relevant fragment.
    """
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p and len(p.strip()) > 1]

BEST_CHUNK_SENTENCES = 3  # target quote length: a contiguous 2–3 sentence chunk

def _sentence_windows(sentences: List[str], size: int = BEST_CHUNK_SENTENCES) -> List[str]:
    """Contiguous windows of up to `size` sentences (sliding, step 1), each joined
    into one string. Passages with `size` or fewer sentences yield a single
    whole-passage window.
    """
    n = len(sentences)
    if n == 0:
        return []
    if n <= size:
        return [" ".join(sentences)]
    return [" ".join(sentences[i:i + size]) for i in range(0, n - size + 1)]

def select_best_sentences(query: str, discourses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Annotate each discourse with `best_sentence`: the contiguous 2–3 sentence
    chunk from its matched passage that best answers `query`.

    Uses ONE Cohere rerank call over the candidate chunks of all passages combined,
    then assigns each discourse its highest-ranked chunk. Degrades to the passage's
    leading chunk when COHERE_API_KEY is unset or the call fails — never throws.
    """
    if not discourses:
        return discourses

    per_doc_windows = []
    flat = []  # (discourse_index, chunk)
    for d_idx, d in enumerate(discourses):
        sentences = _split_sentences(d.get("matched_passage", "") or d.get("content", ""))
        windows = _sentence_windows(sentences)
        per_doc_windows.append(windows)
        for w in windows:
            flat.append((d_idx, w))

    def _default(d_idx):
        windows = per_doc_windows[d_idx]
        if windows:
            return windows[0]
        d = discourses[d_idx]
        return d.get("matched_passage", "") or d.get("content", "")

    chosen = {}  # discourse_index -> best chunk
    if COHERE_API_KEY and flat:
        try:
            import cohere
            co = cohere.Client(COHERE_API_KEY)
            documents = [w for (_, w) in flat]
            response = co.rerank(
                model=RERANK_MODEL,
                query=query,
                documents=documents,
                top_n=len(documents),
            )
            # Results are best-first; the first chunk seen per discourse wins.
            for result in response.results:
                d_idx, chunk = flat[result.index]
                if d_idx not in chosen:
                    chosen[d_idx] = chunk
        except Exception as e:
            logging.error(f"Cohere best-chunk selection failed: {e}; using leading chunk.")
            chosen = {}

    for d_idx, d in enumerate(discourses):
        d["best_sentence"] = chosen.get(d_idx) or _default(d_idx)
    return discourses

def _locate_verbatim(quote: str, passage: str):
    """Return the exact substring of `passage` matching `quote`, tolerant of
    whitespace/newline differences, or None when the quote is not actually present.
    """
    if not quote or not passage:
        return None
    tokens = quote.split()
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    m = re.search(pattern, passage)
    return m.group(0) if m else None

def grade_and_quote_passages(query: str, passages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Unified extractive grade: ONE stronger-LLM call judges whether each passage
    directly answers `query` AND extracts the verbatim span that answers it.

    Keeps only passages where `answers` is true, `relevance >= GRADE_MIN_RELEVANCE`,
    and a non-null quote can be located verbatim in the passage — attaching the exact
    substring as `best_sentence` and `grade_relevance`. Because the quote is the
    evidence for the verdict, relevance and the shown quote can no longer disagree.

    On any LLM/parse failure, degrades to the Cohere chunk baseline
    (`select_best_sentences`) and keeps everything (nothing dropped).
    """
    if not passages:
        return []

    system_prompt = (
        "The passages below are excerpts from spiritual discourses (lectures) delivered by "
        "Sathya Sai Baba, a revered Indian spiritual teacher. His teachings often illustrate "
        "points with stories, scripture, and Sanskrit terms.\n\n"
        "You judge whether each passage DIRECTLY answers the user's question, and if so "
        "extract the exact quote that answers it. A passage that merely mentions the topic, "
        "or is broadly on-theme but does not address what was asked, does NOT answer it. For "
        "each passage return its id, `answers` (true/false), `relevance` (0.0-1.0), and "
        "`quote`: the shortest contiguous span of 1 to 3 sentences copied EXACTLY (verbatim) "
        "from the passage that answers the question, or null if the passage does not answer "
        "it. Never quote generic, introductory, or closing remarks (e.g. 'I shall bring my "
        "discourse to a close'). Respond ONLY with JSON: "
        '{"results":[{"id":0,"answers":true,"relevance":0.0,"quote":"..."}]}. No prose.'
    )
    lines = [f"Question: {query}", "", "Passages:"]
    for idx, p in enumerate(passages):
        lines.append(f"[{idx}] {p.get('content', '')}")
    user_content = "\n\n".join(lines)

    try:
        response = openai_client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        data = json.loads(raw)

        kept = []
        for item in data.get("results", []):
            idx = item.get("id")
            if not isinstance(idx, int) or idx < 0 or idx >= len(passages):
                continue
            if item.get("answers") is not True:
                continue
            relevance = float(item.get("relevance", 0.0) or 0.0)
            if relevance < GRADE_MIN_RELEVANCE:
                continue
            exact = _locate_verbatim(item.get("quote"), passages[idx].get("content", ""))
            if not exact:
                continue  # no verbatim answering span -> drop (avoid hallucinated quotes)
            p = dict(passages[idx])
            p["grade_relevance"] = relevance
            p["best_sentence"] = exact
            kept.append(p)
        return kept
    except Exception as e:
        logging.error(f"grade_and_quote_passages failed: {e}; falling back to Cohere chunks (no drop).")
        return select_best_sentences(query, passages)

def grade_passages(query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """LLM relevance grade: keep only passages that directly answer the query.

    A single batched gpt-4o-mini call in JSON mode. On any parse/API failure the
    reranked candidates are returned unfiltered rather than crashing.
    """
    if not candidates:
        return []

    system_prompt = (
        "You judge whether each passage from a collection of spiritual discourses directly "
        "answers the user's question. A passage that merely mentions the topic, or is broadly "
        "on-theme but does not address what was asked, does NOT answer it. For each passage "
        "return its id, `answers` (true/false), and `relevance` (0.0-1.0). Respond ONLY with "
        'JSON: {"results":[{"id":0,"answers":true,"relevance":0.0}]}. No prose, no markdown.'
    )

    lines = [f"Question: {query}", "", "Passages:"]
    for idx, candidate in enumerate(candidates):
        lines.append(f"{idx}: {candidate.get('content', '')}")
    user_content = "\n".join(lines)

    try:
        response = openai_client.chat.completions.create(
            model=GRADE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        # Strip code fences defensively before parsing.
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        data = json.loads(raw)

        graded = []
        for item in data.get("results", []):
            idx = item.get("id")
            if not isinstance(idx, int) or idx < 0 or idx >= len(candidates):
                continue
            relevance = float(item.get("relevance", 0.0) or 0.0)
            if item.get("answers") is True and relevance >= GRADE_MIN_RELEVANCE:
                candidate = dict(candidates[idx])
                candidate["grade_relevance"] = relevance
                graded.append(candidate)
        return graded
    except Exception as e:
        logging.error(f"grade_passages failed: {e}; returning reranked candidates unfiltered.")
        return candidates

def aggregate_to_discourses(graded: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Collapse graded passages to one result per discourse (best passage wins)."""
    best_by_article = {}
    for passage in graded:
        article_id = passage.get("article_id", "")
        relevance = passage.get("grade_relevance", passage.get("score", 0.0))
        existing = best_by_article.get(article_id)
        if existing is None or relevance > existing["_relevance"]:
            best_by_article[article_id] = {"_relevance": relevance, "passage": passage}

    discourses = []
    for article_id, entry in best_by_article.items():
        passage = entry["passage"]
        relevance = entry["_relevance"]
        matched_passage = passage.get("content", "")
        discourses.append({
            "_id": article_id,
            "title": passage.get("title", ""),
            "matched_passage": matched_passage,
            "passage_index": passage.get("chunk_index", 0),
            "content": matched_passage,  # keep `content` = passage for frontend/format_docs
            "best_sentence": passage.get("best_sentence", ""),  # verified answering quote
            "score": relevance,
            "location": passage.get("location", ""),
            "occasion": passage.get("occasion", ""),
            "link": passage.get("link", ""),
            "collection": passage.get("collection_name", ""),  # output key is `collection`
            "date_authored": passage.get("date_authored", ""),
        })

    discourses.sort(key=lambda d: d["score"], reverse=True)
    return discourses[:limit]

def search_exact(
    query: str,
    limit: int = 5,
    full_match_score: float = 1.0,
    partial_match_score: float = 0.9
) -> List[Dict[str, Any]]:
    """Search articles using exact string matching in title or content."""
    try:
        client = get_client()
        if not client:
            logging.error("Weaviate client not available for exact search.")
            return []
            
        articles = client.collections.get("Article")
        # First try exact match in title
        response = articles.query.fetch_objects(
            filters=Filter.by_property("title").equal(query),
            limit=limit
        )
        
        results = []
        for obj in response.objects:
            results.append({
                "_id": str(obj.uuid),
                "title": obj.properties.get("title", "Untitled"),
                "content": obj.properties.get("content", ""),
                "score": full_match_score,  # Exact match gets perfect score
                "location": obj.properties.get("location", ""),
                "occasion": obj.properties.get("occasion", ""),
                "link": obj.properties.get("link", ""),
                "collection": obj.properties.get("collection_name", ""),
            })
        
        # If no exact title matches, search for substring in content
        if not results:
            response = articles.query.fetch_objects(
                filters=Filter.by_property("content").contains_any([query]),
                limit=limit
            )
            
            for obj in response.objects:
                results.append({
                    "_id": str(obj.uuid),
                    "title": obj.properties.get("title", "Untitled"),
                    "content": obj.properties.get("content", ""),
                    "score": partial_match_score,  # Substring match gets high score
                    "location": obj.properties.get("location", ""),
                    "occasion": obj.properties.get("occasion", ""),
                    "link": obj.properties.get("link", ""),
                    "collection": obj.properties.get("collection_name", ""),
                })
        
        return results[:limit]  # Ensure we don't exceed limit
    except Exception as e:
        logging.error(f"Weaviate exact search failed: {e}")
        return []

def search(user_query: str, collection=None) -> List[Dict[str, Any]]:
    """Search for documents."""
    return search_browse(user_query)

def get_full_article(id, collection=None):
    """Retrieve full article by UUID or slug from Weaviate."""
    try:
        client = get_client()
        articles = client.collections.get("Article")

        obj = None
        # Try UUID lookup first
        try:
            import uuid
            uuid_obj = uuid.UUID(id)
            obj = articles.query.fetch_object_by_id(uuid_obj)
        except ValueError:
            # Not a UUID — try slug-based lookup by searching title similarity
            logging.info(f"Non-UUID id '{id}', attempting slug-based search")
            # Convert slug to search query: replace hyphens with spaces, strip trailing numbers
            import re
            search_query = re.sub(r'\d+$', '', id).replace('-', ' ').strip()
            if search_query:
                from weaviate.classes.query import MetadataQuery
                response = articles.query.near_text(
                    query=search_query,
                    limit=1,
                    return_metadata=MetadataQuery(distance=True)
                )
                if response.objects:
                    obj = response.objects[0]

        if not obj:
            return None

        article = {
            "_id": str(obj.uuid),
            "title": obj.properties.get("title", ""),
            "content": obj.properties.get("content", ""),
            "location": obj.properties.get("location", ""),
            "occasion": obj.properties.get("occasion", ""),
            "link": obj.properties.get("link", ""),
            "collection": obj.properties.get("collection_name", "")
        }

        # Convert to markdown format
        markdown_article = f"# {article['title']}\n\n"
        markdown_article += f"**Location:** {article['location']}\n\n"
        markdown_article += f"**Occasion:** {article['occasion']}\n\n"
        markdown_article += f"**Collection:** {article['collection']}\n\n"
        markdown_article += f"**Link:** [{article['link']}]({article['link']})\n\n"
        markdown_article += f"## Content:\n\n{article['content']}\n"
        article['markdown_format'] = markdown_article

        return article
    except Exception as e:
        logging.error(f"Error fetching article by id: {e}")
        return None

def format_docs(docs):
    """Format documents for context."""
    formatted_docs = []
    if not docs:
        logging.warning("No documents provided to format_docs")
        return "From discourse 'General Spiritual Guidance': This discourse provides general spiritual guidance and wisdom from Sai Baba's teachings."
    
    for doc in docs:
        if isinstance(doc, dict):
            title = doc.get('title', 'Untitled')
            # Prefer the vetted matched passage in full — it is already short
            # (post-chunking) and was confirmed relevant by the grader. Only
            # fall back to truncating raw `content` when no passage is present
            # (e.g. exact-match full-article results).
            matched_passage = doc.get('matched_passage')
            if matched_passage:
                excerpt = str(matched_passage)
            else:
                excerpt = str(doc.get('content', 'N/A'))[:1500]
            formatted_docs.append(f"From discourse '{title}': {excerpt}")

    if not formatted_docs:
        return "From discourse 'General Spiritual Guidance': This discourse provides general spiritual guidance and wisdom from Sai Baba's teachings."
    
    return "\n\n".join(formatted_docs)

def load_conversation_history(session_id: str, user_id: str = None) -> list:
    """Load previous conversation exchanges from Weaviate."""
    try:
        client = get_client()
        conv_col = client.collections.get("Conversation")
        response = conv_col.query.fetch_objects(
            filters=Filter.by_property("session_id").equal(session_id),
            limit=1
        )
        if response.objects:
            obj = response.objects[0]
            messages_json = obj.properties.get("messages_json", "[]")
            messages = json.loads(messages_json)
            formatted = []
            for msg in messages:
                role = "user" if msg.get("type", "").lower() == "human" else "assistant"
                formatted.append({"role": role, "content": msg.get("content", "")})
            return formatted[-20:] # limit to last 10 exchanges
        return []
    except Exception as e:
        logging.error(f"Error loading conversation history: {e}")
        return []

def save_conversation_turn(session_id: str, user_id: str, query: str, answer: str):
    """Save the latest turn to Weaviate."""
    try:
        client = get_client()
        conv_col = client.collections.get("Conversation")
        response = conv_col.query.fetch_objects(
            filters=Filter.by_property("session_id").equal(session_id),
            limit=1
        )
        
        now = datetime.now()
        new_human = {"type": "human", "content": query, "timestamp": now.isoformat()}
        new_ai = {"type": "ai", "content": answer, "timestamp": now.isoformat()}
        
        if response.objects:
            obj = response.objects[0]
            messages_json = obj.properties.get("messages_json", "[]")
            messages = json.loads(messages_json)
            messages.extend([new_human, new_ai])
            
            conv_col.data.update(
                uuid=obj.uuid,
                properties={
                    "messages_json": json.dumps(messages),
                    "last_updated": now
                }
            )
        else:
            messages = [new_human, new_ai]
            conv_col.data.insert(properties={
                "session_id": session_id,
                "user_id": user_id or "",
                "messages_json": json.dumps(messages),
                "created_at": now,
                "last_updated": now
            })
    except Exception as e:
        logging.error(f"save_conversation_turn error: {e}")

def clear_conversation_memory(session_id: str, user_id: str = None):
    """Clear conversation corresponding to a session_id."""
    try:
        client = get_client()
        conv_col = client.collections.get("Conversation")
        conv_col.data.delete_many(where=Filter.by_property("session_id").equal(session_id))
        return True
    except Exception as e:
        logging.error(f"clear_conversation_memory error: {e}")
        return False

def store_new_user_query(query_text, response, get_knowledge, user_email=None):
    """Log the QA search metadata to Weaviate UserQuery."""
    try:
        client = get_client()
        query_col = client.collections.get("UserQuery")
        
        citationString = ''
        score = 0.0
        if get_knowledge:
            score = get_knowledge[0].get('score', 0.0)
            if score < 0.75: # Legacy condition migrated
                for knowledge in get_knowledge:
                    citationString += f"{knowledge.get('_id', '')} -- {knowledge.get('title', '')} -- {knowledge.get('score', 0)}\n"
                    
                query_col.data.insert(properties={
                    "query_text": query_text,
                    "response": response,
                    "score": float(score),
                    "citation": citationString,
                    "user_email": user_email or "",
                    "created_at": datetime.now()
                })
    except Exception as exp:
        logging.error(f"Error storing user query: {exp}")

def classify_query(query: str):
    """
    Classify if a query is inappropriate.
    Returns: (is_allowed, confidence_score, reason)
    """
    try:
        classification_prompt = '''You are a query classifier for an AI assistant. Your task is to block queries that are:
- Vulgar, hateful, or explicit
- Seeking medical advice (e.g., asking for prescriptions, diagnoses, or treatment recommendations)
- Seeking legal advice (e.g., asking for legal interpretations or recommendations)
- Seeking financial advice (e.g., asking for investment, tax, or financial planning advice)
Otherwise, allow the query and provide a broad category (spiritual, personal, general, etc.).

Return your classification as a JSON object with these fields:
{
    "is_allowed": boolean,
    "confidence": float between 0 and 1,
    "category": string (one of: "medical", "legal", "financial", "vulgar", "spiritual", "personal", "general"),
    "reason": string explaining your decision
}'''
        model_id = load_fine_tuned_model_id_from_file()
        response = openai_client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": classification_prompt},
                {"role": "user", "content": query}
            ],
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        return result.get("is_allowed", True), result.get("confidence", 1.0), result.get("reason", "")
    except Exception as e:
        logging.error(f"Error in classify_query: {e}")
        return True, 0.5, "Error in classification"

def handle_user_query(query: str, collection=None, session_id: str = None, user_id: str = None, user_email: str = None, search_results = None):
    """Handle user query completely without LangChain memory structures."""
    try:
        is_allowed, confidence, reason = classify_query(query)
        if not is_allowed:
            suggestion = (
                "Please avoid asking questions that are vulgar, or seek medical, legal, or financial advice. "
                "Try rephrasing your question to focus on spiritual, personal, or general topics. "
                "If you believe your question is valid and this was a mistake, please try asking again in a different way, as the website can sometimes make errors."
            )
            return (
                f"I'm sorry, but I cannot answer this question. Reason: {reason}\n"
                f"{suggestion}",
                ""
            )

        # Extract any quoted phrase from the user's query for exact/title-priority search.
        # This handles natural language queries like: retrieve the discourse on "Love and Truth"
        # as well as fully-quoted queries like: "Love and Truth"
        exact_phrase = extract_quoted_phrase(query)
        if exact_phrase:
            if search_results is None:
                search_results = search_exact(exact_phrase, limit=5)
        else:
            if search_results is None:
                search_results = search_browse(query, limit=5, allow_empty=False)

        # Ensure we have at least 5 results (by grabbing random docs if search fails)
        # Assuming we aren't performing random augmentation here anymore due to semantic search efficiency,
        # but the fallback might just use what we have. 
            
        context = format_docs(search_results)
        
        chat_history = []
        if session_id:
            chat_history = load_conversation_history(session_id, user_id)

        system_prompt = """You are an AI assistant that provides spiritual guidance based on Sathya Sai Baba's teachings.

        Your response should be simple and direct:
        "Here are some discourses where you can start learning about the topic:"

        Then list ONLY the actual titles of the discourses provided to you, one per line with a dash, like this:
        - [actual title from the provided discourses]
        - [actual title from the provided discourses]
        - [actual title from the provided discourses]

        Do not provide any descriptions, summaries, or quotes. Do not use placeholder text like "[title of discourse 1]". Use the real titles from the discourses provided.

        Always provide spiritual guidance based on the provided discourses, even if the question seems unrelated. Use the discourses to offer relevant wisdom and insights that can help the user in their spiritual journey."""

        messages = [{"role": "system", "content": system_prompt}]
        for msg in chat_history:
            messages.append(msg)
            
        messages.append({
            "role": "user",
            "content": f"Answer this query: {query}\n\nBased on the following discourses: {context}"
        })

        model_id = load_fine_tuned_model_id_from_file()
        response = openai_client.chat.completions.create(
            model=model_id,
            messages=messages
        )
        answer = response.choices[0].message.content

        # Post-process message
        if "misunderstanding" in answer.lower() or "seems like there might be" in answer.lower():
            answer = "Based on Sai Baba's teachings, here is spiritual guidance that can help you: " + (answer.split(".")[-1] if "." in answer else answer)

        if session_id:
            save_conversation_turn(session_id, user_id, query, answer)

        store_new_user_query(query, answer, search_results, user_email)

        return answer
    except Exception as e:
        logging.error(f"Error in handle_user_query: {e}")
        return "An error occurred while processing your query.", ""
