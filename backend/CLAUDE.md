# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Flask backend for "Ask Sai Vidya" — an AI search/Q&A service over a corpus of Sai Baba discourses. It combines Weaviate hybrid search (semantic + keyword) with OpenAI for answer generation, and persists users, chats, saved discourses, and feedback in **Weaviate collections** (not MongoDB — despite README mentions, the live storage layer is Weaviate; see `weaviate_client.py::init_schema`).

The deployed topology is **AWS API Gateway → Elastic Beanstalk (Flask/WSGI)**. The README's Docker/Railway sections are legacy and not how this is shipped — ignore them for deployment.

## Setup & running

Always activate the venv before any Python command in this directory:

```bash
source venv/bin/activate
python app.py            # serves on :8000 (FLASK_RUN_PORT)
python validate-env.py   # check required env vars before running
```

**Restarting: kill by port, and verify the new process owns it.** The process shows up as `.../MacOS/Python app.py` — capital `P` — so `pkill -f "python app.py"` matches nothing and exits 0. The old server keeps :8000, the new one dies with "Address already in use" into a log nobody reads, and every subsequent request silently hits the *old code with a warm cache*. This has now invalidated a full 230-question eval run and a round of manual verification:

```bash
lsof -ti :8000 | xargs -r kill -9 ; sleep 3
nohup venv/bin/python app.py > /tmp/backend.log 2>&1 &
NEW=$!; sleep 10; [ "$(lsof -ti :8000)" = "$NEW" ] || echo "STALE SERVER — results are worthless"
```

A p50 latency in the single-digit milliseconds means you measured the cache, not the pipeline. Run evals with `SEMANTIC_CACHE_ENABLED=0`.

**Check the run for API degradation before believing its scorecard.** `eval_ragas.py` is sequential, but each question fans out internally (per-facet retrieval + 3 grading workers), so OpenAI rate-limiting degrades *results*, not just latency — retries stretch a query to minutes, and a `RERANK_DEGRADED` query is ranked by hybrid score alone. Both silently move `exact_discourse_first`. One measured run had a 16.8-minute query, 6 over 60s, and one degraded rerank; two of its three "regressions" then failed to reproduce when the same questions were asked standalone. After any run:

```python
# max ms, count over 60s, and RERANK_DEGRADED across eval_results.json rows
rows = json.load(open("eval_results.json"))["rows"]
```

A clean run has max ms in the low tens of seconds and no `RERANK_DEGRADED`. If it doesn't, re-run before concluding anything — and when a metric moves, re-ask those specific questions standalone to confirm it reproduces.

Required env (from `.env`): `WEAVIATE_URL`, `WEAVIATE_API_KEY`, `OPENAI_API_KEY`, **`VOYAGE_API_KEY`** (reranking), plus `MONGO_URI`, `JWT_SECRET_KEY`, `GOOGLE_CLIENT_ID`, and `MAIL_*` for password-reset email. `FRONTEND_URL` is a comma-separated CORS allowlist (first entry is the default redirect target).

**`VOYAGE_API_KEY` must be set in every environment, including EB.** Without it reranking silently falls back to hybrid-score order and follow-ups return empty. It degrades rather than crashes, so the only signal is `rerank_degraded` on the trace and an ERROR in the log — this is exactly how a rate-limited key went unnoticed across 294 failures. Optional overrides: `RERANK_PROVIDER` (`voyage`|`cohere`), `PLAN_MODEL`, `JUDGE_MODEL`.

## Testing

The `test_*.py` files are **integration scripts that hit a running server**, not pytest unit tests. They require a live backend (local or deployed):

```bash
BASE_URL=http://localhost:8000 python evals/test_endpoints.py   # configurable via env
python evals/test_backend.py                                    # has hardcoded EB/Gateway URLs
python evals/test_weaviate_connection.py                        # checks Weaviate connectivity
```

### Evaluating search changes — do not skip this

`eval_ragas.py` runs a 218-question golden set (sampled from real traffic, weighted by the measured question taxonomy) against a running backend and gates on three metrics. **No search change ships without it:**

```bash
python app.py &                                     # harness needs a live server
python evals/eval_ragas.py --baseline evals/eval_baseline.json  # PASS/FAIL vs the shipped config
```

- `quote_answers_rate` — does the top quote actually answer the question (LLM-judged, so ±2-3 points is noise)
- `abstention_correctness` — abstain-labelled questions return nothing, others return something
- `exact_discourse_first` / `_only` — naming a specific discourse returns **that** discourse first, ideally alone

`eval_baseline.json` is the shipped configuration's run; regenerate it when you intentionally move a metric. Supporting scripts: `build_golden_set.py` (sample + LLM-draft labels), `promote_golden_set.py` (apply reviewed fixes, add exact-discourse cases), `replay_real_questions.py` (bulk replay over harvested traffic), `test_knowledge_guard.py` (Entity-route guards, network mocked).

### Collections API

`GET /collections` returns the grouped index (vahinis / sathya_sai_speaks / series / chinna_katha); `GET /collections/chapters?book=…&volume=…&year=…` returns one collection's chapters. Both are public, change only on re-ingestion, and are cached in-process for `CATALOG_TTL_SECONDS` (6h). Backed by `search/collections_index.py` and `search/catalog.py`.

**These are routes, so they 404 *through API Gateway* until the spec is regenerated** — `python infra/generate_openapi.py` then `./infra/sync_gateway.sh`. The frontend's Collections tab renders "Collections are temporarily unavailable" for any non-200, which is what a missing route looks like from the client.

Corpus ingestion/backfill lives in `ingest_sss.py`, `ingest_vahinis.py`, `backfill_metadata.py` and `metadata_norm.py`. These write the `book` / `volume` / `year` / `chapter_index` fields that the listing route, the metadata filters and the Collections index all read — none of that works on a corpus that has not been backfilled.

### Routing changes — `eval_router.py`

`eval_ragas.py` measures end-to-end outcomes, so a misrouted question and a retrieval miss look identical in it. `eval_router.py` isolates the router: it calls `plan_queries` directly, so it needs **no server** and finishes in seconds.

```bash
venv/bin/python evals/eval_router.py              # 75 cases from real traffic + past bugs
venv/bin/python evals/eval_router.py --repeat 3   # + stability; use this one
venv/bin/python evals/eval_router.py --only unanswerable
```

**Always use `--repeat 3`.** A single pass hides the failures that matter: "the best time to wake up" (asked 145× in real traffic) passed 1×, then flipped to `unanswerable` on 2 of 3 runs. Instability is reported per *route*, not per intent — `conceptual`/`scenario`/`aspect` all dispatch to the same semantic path, so a flip among them changes nothing a user sees and would otherwise drown out the flips that do.

Cases (`router_cases.json`) come from real traffic and real bugs, never from what the router currently does. `{"not_intent": "unanswerable"}` cases are over-trigger guards — **refusing an answerable question is worse than the bug the refusal feature was built for.** When a case fails, decide which answer is correct before touching the prompt.

Two model-choice traps the harness has already caught, both recorded in `config.py`:
- **Reasoning models carry a latency floor.** gpt-5-mini measured ~2× slower on both planner and judge even at `reasoning_effort="minimal"`, and worse on every quality metric.
- **Cheap models can blow the gateway budget.** gpt-4.1-mini grades quotes best of anything tested, but 3 of 218 queries exceeded API Gateway's 29s timeout (worst 63s — three client retries against the 20s bound). Check the *worst case*, not just p50.

## Deployment — use the scripts, never deploy manually

Manual deploys have caused production outages. Always use:

```bash
./package_eb.sh                 # tag=staging, env from .elasticbeanstalk/config.yml
./package_eb.sh prod            # build the prod zip
eb deploy <env>                 # asv-dev (staging) or asv-prod (production)

./infra/sync_gateway.sh         # regenerate OpenAPI from routes + CDK deploy the gateway
```

`package_eb.sh` flattens `backend/` to the zip root (so `app.py` is at the archive root, which EB expects) and excludes venv/logs/.env. It warns if `app.py` is newer than `infra/openapi.json`.

**Critical coupling: routes and the API Gateway must stay in sync.** Whenever you add, remove, or change a `@app.route` path, you must regenerate the OpenAPI spec and redeploy the gateway, or the new route will 404 through API Gateway even though EB serves it. `infra/generate_openapi.py` derives `infra/openapi.json` **and** `infra/openapi.yaml` from the Flask routes (never hand-edit either — regenerate); `sync_gateway.sh` diffs it against `openapi.json.prev`, runs CDK (`infra/cdk/`), then promotes the spec to `.prev`. Watch for sibling path-variable conflicts in API Gateway (e.g. `/chats/<user_email>` vs `/chats/<thread_id>` — same position, different names collide; recent commits normalized these).

## Repository layout

Only what the running app imports lives at the backend root. Everything else is
dev tooling, grouped by what it does:

```
backend/
  app.py                 Flask app — MUST stay at the root (EB expects it at the archive root)
  weaviate_client.py     client + init_schema          } imported by app.py
  chat.py  persistence.py  fine_tuning.py              } and/or search/
  metadata_norm.py       date/metadata normalization — imported by search/listing.py
  search/                the pipeline, one module per stage
  infra/                 OpenAPI generation + CDK for the API Gateway
  evals/                 eval_ragas, eval_router, eval_transliteration, test_* +
                         their committed fixtures (golden_questions.json,
                         router_cases.json, eval_baseline.json)
  ingestion/             ingest_*, backfill_*, chunk_articles, reembed_corpus,
                         contextualize_corpus + entity_baseline_curated.json
  tools/                 harvest_*, build/promote_golden_set, replay_real_questions,
                         analyze_real_replay, coverage_gaps, probe_traces
  artifacts/             generated run outputs — GITIGNORED
```

Scripts under `evals/`, `ingestion/` and `tools/` put the backend root on
`sys.path` themselves and resolve data files relative to their own location, so
run them from anywhere: `python evals/eval_router.py`, not `cd evals && …`.
Committed fixtures sit beside the script that reads them; generated output goes
to `artifacts/` via the `_artifact()` helper, which is why the root no longer
collects run debris.

**Do not move the root modules into a package.** `app.py` must be at the archive
root for EB, and `package_eb.sh` flattens `backend/` into the zip — moving
`weaviate_client` or `metadata_norm` would break `search/*` imports and require
a redeploy to verify.

## Architecture

**Request flow:** Frontend → API Gateway → EB/Flask (`app.py`) → the `search/` package → `weaviate_client.py` (Weaviate) + OpenAI/Voyage.

> `utils.py` no longer exists — search was split into the `search/` package. Older
> docs and commit messages referring to it mean `search/`.

- **`app.py`** (~1,370 lines) — single-file Flask app holding every route and all auth. Routes group into: health (`/`), auth (`/auth/google/*`, `/register`, `/login`, `/password/reset/*`), search (`/search`, `/search/verify`, `/followups`, `/query`, `/summarize-question`, `/blog/<id>`), collections, saved discourses, chats, conversation memory, and feedback.
- **`search/`** — the pipeline, one module per stage. `config.py` is the single source of truth for every model, threshold and flag, and carries the measurements behind each choice; read it before changing a constant.
  - `query_planning.py::plan_queries` (`PLAN_MODEL`, gpt-4o-mini) — multi-turn resolution, scenario distillation, romanized-term glossing, adaptive decomposition into 1–N facets, **intent classification, and metadata filter extraction** (`book`/`volume`/`chapter_start|end`/`year_start|end`/`location`/`occasion`, plus `limit`/`sort`/`list_order`). One call does all of it; the prompt is grounded in the corpus's real book list via `catalog.py::format_catalog_for_prompt`, so the model emits "Geeta Vahini" for "the Gita Vahini". Filters are validated by `_validate_filters` before anything reaches Weaviate. Intent taxonomy (`conceptual`/`scenario`/`aspect`/`factual`/`named_text`/`occasion`/`comparative`/`org_doctrine`/`meta`/`out_of_domain`/`listing`).
  - **The refusal boundary is enforced in code, not in the prompt.** `_refusal_is_warranted` (`query_planning.py`) can only ever *downgrade* an `unanswerable` verdict to the semantic route — never create one — because declining a question the discourses do answer is the costlier error. It exists because the boundary would not hold in prose: three prompt rewrites were measured against `eval_router.py --repeat 3` and each made things worse (2 failures → 4 → 5). The more prompt spent defining `unanswerable`, the more the model reached for it. **Do not "clarify" that section of the prompt** — add the case to `router_cases.json` and adjust the guard. The signal is the OBJECT, never the superlative: *the best kind of yoga* is a practice (answerable), *the best discourse on meditation* is a text (not).
  - **Three more router rules are code, for the same reason.** `_is_code_or_injection` forces `out_of_domain` for code/GraphQL/SQL input. A `Geeta Vahini` book filter is dropped unless the user actually wrote "Vahini" — *the Gita* is the scripture Swami comments on, not his book about it, and filtering there hides the rest of the corpus. An `occasion`/`location` filter is dropped when the question asks what the thing MEANS rather than for discourses delivered at it. A refusal that arrives *with* a concrete locator (chapter/volume/year) becomes a `listing` — refusing "show me discourse 10 of Sathya Sai Speaks volume 14" is incoherent.
  - `pipeline.py::search_browse` dispatches on that intent: `meta`/`out_of_domain` short-circuit to guidance; `listing` enumerates by metadata — book/chapter range/year/location/occasion — in chapter or date order (`listing.py::list_discourses`), with a loose-spelling fallback on a miss; `factual`/`named_text`/`org_doctrine` try the Entity KB (`knowledge.py`, returning hit/gap/miss — a **gap abstains honestly** rather than guessing); everything else takes the semantic route.
  - Semantic route: per-facet `retrieval.py::search_passages` (Weaviate hybrid BM25+vector, `HYBRID_ALPHA=0.5`), **narrowed by the router's metadata filters when it extracted any** — "what does the Geeta Vahini say about karma" keeps its topical intent and constrains the candidate set (`build_passage_filter`); collection exclusions are ANDed in and always survive → `ranking.py::rerank_passages` (**Voyage `rerank-2.5-lite`** via a provider-agnostic `_rerank` adapter; `RERANK_PROVIDER=cohere` rolls back) → `_rrf_merge` (keeps `source_query` provenance) → `grade_and_quote_passages` (`JUDGE_MODEL`, **gpt-4.1**, extractive — judges/quotes each passage **against its own facet**, sharded into concurrent batches) → `aggregate_to_discourses`. Output is citations-only.
  - `transparency.py` builds the opt-in trace (`include_trace: true`) that powers the frontend's "How I searched" panel and refinement guidance.
  - `cache.py` — normalized-exact result cache, **on** (56% of real traffic is a repeat question). Failures are deliberately never cached.
  - `resilience.py` — bounded retries; a Weaviate outage raises `PipelineServiceError` so an infra failure is never shown as "no results".

**Two-phase search.** Grading is the latency floor, so `/search` accepts `defer_grading: true` and returns reranked-but-ungraded candidates (~3× faster first paint); the client then POSTs the passage ids to `/search/verify` for verified quotes. That second call is **stateless** — passages are re-read from Weaviate by id, not held in memory — so it survives multiple EB instances. Deferred results carry no quotes and are marked `quality: "pending"`; **never render them as answers.**

**Never let generated text into a quote.** `grade_and_quote_passages` extracts a span that must be located verbatim in the passage (`_locate_verbatim`), and a quote that can't be found is dropped. Anything that changes what lives in a passage's `content` field must preserve this — see the design note at the top of `contextualize_corpus.py` for why contextual retrieval writes to a *separate* field.
- **`weaviate_client.py`** — singleton Weaviate Cloud client (`get_client`) and `init_schema`, which defines all collections: `Article` and `Passage` (the discourse corpus — `Passage` holds chunked passages, both vectorized with `text-embedding-3-large`), `ChatThread`, `Conversation`, `UserQuery`, `Feedback` (now includes `discourse_title`/`discourse_id`/`discourse_source`), `SavedDiscourse`, `UserAccount`, `PasswordResetToken`. `init_schema` runs at import time in `app.py`, is idempotent (creates only missing collections), and adds newer properties to existing collections (e.g. the Feedback discourse fields).

**Auth model:** Two token types, both checked in `get_verified_identity`:
1. **Manual** — bcrypt-hashed passwords in `UserAccount`, app-signed HS256 JWT (`JWT_SECRET_KEY`), must carry `token_type: 'manual'`.
2. **Google OAuth** — verified via `google.oauth2.id_token`. If `GOOGLE_CLIENT_ID` is unset, signature verification is bypassed (dev fallback only).

Protect routes with the `@require_auth` decorator; read the caller's email with `get_user_email_from_request()`.

**Models:** the search pipeline's models live in `search/config.py` — `PLAN_MODEL` (gpt-4o-mini, router), `JUDGE_MODEL` (gpt-4.1, extractive grader), `RERANK_MODEL` (Voyage `rerank-2.5-lite`), `GRADE_MODEL` (gpt-4o-mini, cheap utility calls). Both LLM ids are env-overridable for rollback. Embeddings are `text-embedding-3-large`, applied server-side by Weaviate's `text2vec-openai` on the `Passage` collection. Separately, the legacy `/query` chat path still reads a fine-tuned model id from `fine_tuned_model.txt` via `fine_tuning.load_fine_tuned_model_id_from_file()`.

**Proxy awareness:** `ProxyFix` is applied so `request.host_url` reflects the real domain behind the AWS load balancer — relevant for OAuth redirect URIs and password-reset links (`get_base_url`, `get_google_redirect_uri`).

## Environments

- **Staging:** account 522814696973, EB `asv-dev`, gateway `https://dxhp0j33db.execute-api.us-east-1.amazonaws.com/dev`
- **Production:** account 503561422699, EB `asv-prod`, gateway `https://bbqdh9uxll.execute-api.us-east-1.amazonaws.com/production/`, fronts https://asksaividya.com

AWS session tokens here contain `//` and `+` that break CLI shell parsing — prefer boto3 (via Python) over `export` + `aws` CLI for scripted AWS calls.
