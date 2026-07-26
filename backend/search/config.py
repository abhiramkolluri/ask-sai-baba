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
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")

# Which reranking provider the pipeline uses: "voyage" or "cohere". Voyage is the
# default after an audit found the Cohere key was a TRIAL key capped at 10 calls/
# minute — a single search issues 1-4 rerank calls, so 28% of facet reranks in a
# real-traffic replay silently fell back to hybrid-score order (294 logged 429s).
# Voyage rerank-2.5-lite is also ~7.7x cheaper at our 40-document rerank size
# (token-billed vs Cohere's per-search billing) and scores higher on NDCG@10.
# Flip this back to "cohere" to roll the swap back in one line.
RERANK_PROVIDER = os.getenv("RERANK_PROVIDER", "voyage")

# Bound external calls so a hang or blip can't run past the API Gateway timeout.
# COHERE_TIMEOUT closes the observed 97s rerank hang (client had no timeout);
# WEAVIATE_RETRY_ATTEMPTS retries transient Weaviate connection drops before the
# pipeline reports an honest service error rather than a false empty result.
COHERE_TIMEOUT = 5
# voyageai.Client defaults to max_retries=0 and no timeout; both are set
# explicitly so a transient blip retries and a hang stays inside our budget.
VOYAGE_TIMEOUT = 5
VOYAGE_MAX_RETRIES = 2
WEAVIATE_RETRY_ATTEMPTS = 2

PASSAGE_OVERFETCH = 40
RERANK_KEEP = 15
# Per-provider model ids; the active one is chosen by RERANK_PROVIDER.
RERANK_MODEL = "rerank-2.5-lite"      # voyage
COHERE_RERANK_MODEL = "rerank-v3.5"   # cohere (rollback path)
GRADE_MODEL = "gpt-4o-mini"   # cheap utility calls (eval judge, follow-up proposals)
# The router (plan_queries) and the extractive judge both run on gpt-5-mini.
# gpt-5-mini is a REASONING model: it rejects `temperature` outright, and its
# default reasoning effort is far slower than gpt-4o — "minimal" is what makes it
# a latency win rather than a regression, so treat it as required, not tuning.
# Env-overridable so a bad model can be rolled back without a code deploy, and
# so an A/B baseline can be captured against the same golden set.
# Judge selected by measurement, not by list price. All four run against the same
# 218-question golden set:
#
#   judge          quote  exact_first  p50      worst    >29s  $/1M in-out
#   gpt-4.1        65%    62%          4,221ms  10.1s    0     $2 / $8    <- chosen
#   gpt-4o         62%    62%          3,716ms   8.8s    0     $2.50 / $10
#   gpt-4.1-mini   69%    55%          7,957ms  63.1s    3     $0.40 / $1.60
#   gpt-5-mini     52%    55%          7,681ms    —      —     $0.25 / $2
#
# gpt-4.1 is better than gpt-4o on quote quality, equal on exact-discourse, and
# 20% cheaper, for ~500ms of p50.
#
# gpt-4.1-mini grades quotes best of all but is UNSHIPPABLE here: 3 of 218 queries
# blew past API Gateway's 29s integration timeout (worst 63s = 3 client retries x
# the 20s bound), and exact-discourse fell to 55%. Those are failed requests in
# production, not slow ones.
#
# gpt-5-mini is a reasoning model with a multi-second floor even at
# reasoning_effort="minimal" — ~2x slower on BOTH planner and judge, worse on
# every quality metric.
#
# Re-measure with eval_ragas.py --baseline before changing either of these.
PLAN_MODEL = os.getenv("PLAN_MODEL", "gpt-4o-mini")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gpt-4.1")
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "minimal")
# gpt-4o-family models reject `reasoning_effort` and require `temperature`
# instead. Detected rather than configured so a rollback is a single env var.
PLAN_IS_REASONING = PLAN_MODEL.startswith("gpt-5")
JUDGE_IS_REASONING = JUDGE_MODEL.startswith("gpt-5")
GRADE_MIN_RELEVANCE = 0.5
HYBRID_ALPHA = 0.5

# Grading is ~74% of end-to-end latency, and it scales with how much JSON the
# judge has to serialize — one call over 15 passages emits ~15 verdicts with
# quotes, in sequence. Sharding into concurrent batches cuts the serialized
# output per call without changing the model, the prompt, or the token count.
# Independent of JUDGE_MODEL: keep this even if the model swap is reverted.
GRADE_BATCH_SIZE = 5
GRADE_MAX_WORKERS = 3

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

# Server-side result cache for repeated questions. ON: an audit of 3,549 logged
# questions found 56% of all traffic is a repeat of a question already asked, and
# the single most common question is 19% of traffic on its own — so this
# short-circuits the whole plan→retrieve→rerank→grade pipeline for more than half
# of requests. The frontend cache only covers a single session; this one spans
# users. Keyed on the normalized question + context; invalidated by process
# restart, which is also what a corpus re-index requires.
# Env-overridable: an eval or A/B run needs the cache OFF, or the first result
# for a question is replayed for every subsequent measurement and flakiness
# becomes invisible.
SEMANTIC_CACHE_ENABLED = os.getenv("SEMANTIC_CACHE_ENABLED", "1") not in ("0", "false", "False")
SEMANTIC_CACHE_SIZE = 512

# Listing route: how many chapters to return for "list the chapters of X" with no
# explicit count, and the hard cap for "all chapters of X".
LISTING_DEFAULT = 10
LISTING_MAX = 50

# NOTE: PLAN_TEMPERATURE / JUDGE_TEMPERATURE are RETAINED ONLY for a rollback to
# the gpt-4o family. gpt-5-mini rejects the parameter, so neither is sent while
# PLAN_MODEL/JUDGE_MODEL point at it. Worth recording why the determinism
# argument they were added for did not survive contact with reality: measured on
# gpt-4o-mini at temperature 0.0, plan_queries returned collection=None on one
# run of "return the first 5 chapters from Prema Vahini" and "Prema Vahini" on
# the next two. Temperature 0 is not determinism.
#
# LLM temperatures. Adversarial probing showed the unpinned default (1.0) made
# plan_queries interpret the SAME question differently run-to-run (e.g. "value of
# Truth" sometimes kept the aspect, sometimes collapsed to bare "truth") — users
# experienced this as flaky search. The planner keeps a little freedom for
# glossing/decomposition; the grader is a pure judgment call and gets none.
# 0.0 after the fresh probe showed 0.2 still let the router flip intents run-to-run
# on short/ambiguous inputs ("How do I?" -> scenario one run, meta the next). The
# router is a classifier; it should be deterministic.
PLAN_TEMPERATURE = 0.0
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
# Verification threshold on the RERANKER's relevance score (0-1). Candidates are
# no longer verified with an LLM grade: doing so cost 6 judge calls per question —
# ~68% of all per-question spend — to produce three suggestion chips. The reranker
# already scores query↔passage relevance well enough to decide "is this question
# answerable from the corpus", which is all verification needs to establish.
# Calibrated on rerank-2.5-lite against the real corpus, not guessed. Measured top
# scores: answerable questions ("how do I control my anger", "what is devotion")
# landed 0.781-0.879; unanswerable ones ("how do I eat berries", "when will i get
# job", "what Indian political party would Swami align with") landed 0.295-0.605.
# 0.70 sits in that gap with margin on both sides. An earlier 0.5 would have
# admitted the political-speculation and fortune-telling questions as valid
# follow-ups.
# NOTE: this is on Voyage's scale — re-measure if RERANK_PROVIDER or the model changes.
FOLLOWUP_MIN_RERANK_SCORE = 0.70
FOLLOWUP_MAX_WORKERS = 5     # bounded concurrency for per-candidate verification
