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

openai_client = OpenAI(api_key=openai_api_key)


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

# Decoding settings for every pipeline LLM call (planning, grading, follow-ups).
# temperature=0 plus a fixed seed makes repeat runs of the same query return the
# same plans/grades/quotes. NOTE: OpenAI's seed is best-effort reproducibility,
# not a guarantee — outputs can still change across their backend updates.
LLM_TEMPERATURE = 0.0
LLM_SEED = 42

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

# Adaptive query planning (plan_queries): the most sub-queries one user message
# may be decomposed into.
MAX_PLANNED_QUERIES = 4

# Best-quote selection (select_best_sentences): target quote length, a contiguous
# 2–3 sentence chunk.
BEST_CHUNK_SENTENCES = 3

# Structured search (plan_search router + metadata listing path).
# LISTING_MAX_RESULTS caps pure metadata listings ("all discourses from 1976")
# regardless of what the router or caller asks for; LISTING_SNIPPET_SENTENCES is
# how many leading sentences a listing card shows as its snippet (there is no
# answering quote to extract — nothing was asked about the content).
LISTING_MAX_RESULTS = 25
LISTING_SNIPPET_SENTENCES = 3
CATALOG_TTL_SECONDS = 6 * 3600  # corpus vocabulary cache for the router prompt

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
