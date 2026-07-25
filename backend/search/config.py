"""Shared bootstrap and tunable constants for the search package.

This is the single source of truth for the OpenAI client, logging, and every
knob the retrieval pipeline turns. All other modules in the package — and the
top-level ``chat.py`` — import their client and constants from here, so there is
exactly one OpenAI client instance and one logging configuration across the
backend.

Nothing in here talks to Weaviate or OpenAI at call time; it only *constructs*
the client and *declares* the constants. Keeping it dependency-free is what lets
every other module import from it without risking a circular import.
"""

import os
import logging
import configparser

from dotenv import load_dotenv
from openai import OpenAI


# ===========================================================================
# Logging, environment, and the shared OpenAI client
# ===========================================================================

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

# max_retries/timeout added after real-traffic replay showed transient OpenAI
# connection drops (the SDK's default 2 retries were exhausting) turning into
# silent empty results. The SDK retries connection errors itself; we raise the
# ceiling and bound each call so a hang can't blow the API Gateway budget.
openai_client = OpenAI(api_key=openai_api_key, max_retries=3, timeout=20.0)


# ===========================================================================
# Passage-search pipeline configuration
#
# Introduces hybrid search (BM25 + vector), query expansion, Cohere reranking,
# and LLM relevance grading over the Passage collection. None of this existed
# in the codebase before; the legacy Article path (search_browse) is untouched.
#
# COHERE_API_KEY is read from the environment and is OPTIONAL — the pipeline
# degrades gracefully when it is absent (see rerank_passages). For reranking in
# deployment, set COHERE_API_KEY in the Elastic Beanstalk environment.
# ===========================================================================
COHERE_API_KEY = os.getenv("COHERE_API_KEY")

# Bound external calls so a hang or blip can't run past the API Gateway timeout.
# COHERE_TIMEOUT closes the observed 97s rerank hang (client had no timeout);
# WEAVIATE_RETRY_ATTEMPTS retries transient Weaviate connection drops before the
# pipeline reports an honest service error rather than a false empty result.
COHERE_TIMEOUT = 5
WEAVIATE_RETRY_ATTEMPTS = 2

PASSAGE_OVERFETCH = 40
RERANK_KEEP = 15
RERANK_MODEL = "rerank-v3.5"
GRADE_MODEL = "gpt-4o-mini"
JUDGE_MODEL = "gpt-4o"  # stronger judge for unified grade + verbatim quote extraction
GRADE_MIN_RELEVANCE = 0.5
HYBRID_ALPHA = 0.5

# Router v2: when enabled, plan_queries classifies the question's INTENT and
# search_browse dispatches on it (meta/out-of-domain short-circuit to guidance,
# factual/named-text/org-doctrine route to the structured knowledge lookup, etc.).
# Off => the pre-v2 behavior (every query takes the semantic route). Flag lets the
# richer routing roll out and roll back with one line.
ROUTER_V2_ENABLED = True

# Which Weaviate collection the passage pipeline reads. Phase 3 re-embeds the
# corpus into "Passage_v2" (Cohere embed-v4) alongside the original "Passage"
# (OpenAI text-embedding-3-large); flip this to cut over after the eval harness
# confirms the new embeddings win, or flip back to roll back instantly.
PASSAGE_COLLECTION = "Passage"

# Server-side result cache for repeated questions (Phase 4). Off by default — the
# frontend already caches per-session, so this only helps across users/sessions;
# enable when you want popular questions ("what is faith") to short-circuit the
# whole pipeline. Keyed on the normalized question + context.
SEMANTIC_CACHE_ENABLED = False
SEMANTIC_CACHE_SIZE = 512

# LLM temperatures. Adversarial probing showed the unpinned default (1.0) made
# plan_queries interpret the SAME question differently run-to-run (e.g. "value of
# Truth" sometimes kept the aspect, sometimes collapsed to bare "truth") — users
# experienced this as flaky search. The planner keeps a little freedom for
# glossing/decomposition; the grader is a pure judgment call and gets none.
PLAN_TEMPERATURE = 0.2
JUDGE_TEMPERATURE = 0.0

# RRF-merge sizing. The merged candidate list is capped, but with multiple facets
# a flat cap starved facets entirely (probing: a 4-facet question left one facet
# with ZERO graded passages). The cap now scales with facet count, and every
# facet is guaranteed its top MERGE_MIN_PER_FACET passages in the merged list.
MERGE_MIN_PER_FACET = 3
MERGE_PER_FACET_CAP = 5  # cap = max(RERANK_KEEP, MERGE_PER_FACET_CAP * facets)

# Per-facet retrieval+rerank runs concurrently (one worker per facet). Probing
# showed the sequential loop pushed 3-4 facet queries to 26-31s total — past API
# Gateway's 29s integration timeout in production. Facets are independent, so
# they fan out like followup verification does. Max facets = MAX_PLANNED_QUERIES.
FACET_MAX_WORKERS = 4

# Collections excluded from passage search. "SSIO Guidelines" are organizational/
# administrative documents, not Swami's discourses — they rank well on "Sathya
# Sai" wording but are the wrong content for a discourse search (probing surfaced
# them for "birthday discourse" and children queries).
EXCLUDED_COLLECTIONS = ["SSIO Guidelines"]

# Result-quality thresholds for the transparency trace (search/transparency.py).
# A result set is "strong" only when it has at least this many discourses AND the
# top discourse clears this relevance bar; otherwise it is "partial" (or "none"
# when empty), which is what gates the frontend's refinement guidance. These are
# heuristic — tune here without touching the pipeline.
QUALITY_STRONG_MIN_RESULTS = 3
QUALITY_STRONG_MIN_RELEVANCE = 0.7

# When the grader rejects everything but the caller still needs grounding
# context (chat, allow_empty=False), fall back to this many top reranked
# (pre-grade) passages aggregated to discourses. Browse (allow_empty=True)
# never falls back — an empty result is the honest answer there.
CHAT_FALLBACK_DISCOURSES = 3

# Adaptive query planning (plan_queries): the most sub-queries one user message
# may be decomposed into.
MAX_PLANNED_QUERIES = 4

# Best-quote selection (select_best_sentences): target quote length, a contiguous
# 2–3 sentence chunk.
BEST_CHUNK_SENTENCES = 3

# Follow-up question generation (generate_followups). One cheap LLM call proposes
# candidate follow-ups grounded in the discourses already retrieved for the answer,
# then EACH candidate is verified by running the real retrieve->rerank->grade
# pipeline; only candidates that surface a directly-answering quote
# (relevance >= GRADE_MIN_RELEVANCE) survive, and survivors are ranked by the top
# relevance they reach (the funnel). Smaller fetch/keep than the main pipeline since
# verification only needs to confirm answering quotes exist, not assemble results.
FOLLOWUP_CANDIDATES = 6      # how many candidate questions the LLM proposes
FOLLOWUP_KEEP = 3            # how many verified follow-ups to return
FOLLOWUP_OVERFETCH = 20      # hybrid candidates fetched per candidate during verification
FOLLOWUP_RERANK_KEEP = 5     # passages kept after rerank, then graded
FOLLOWUP_MIN_HITS = 1        # min directly-answering discourses for a candidate to survive
FOLLOWUP_MAX_WORKERS = 5     # bounded concurrency for per-candidate verification
