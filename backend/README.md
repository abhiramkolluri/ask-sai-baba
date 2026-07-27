# Ask Sai Vidya — Backend

Flask backend for an AI-powered search and Q&A service over a corpus of Sai Baba discourses. It combines **Weaviate hybrid search** (semantic + keyword) with **OpenAI** for answer generation. All application data — users, chats, saved discourses, feedback, and the discourse corpus itself — is stored in **Weaviate collections**.

> Deployed as **AWS API Gateway → Elastic Beanstalk**. There are stale Docker/Railway/MongoDB references elsewhere in the history; the live system uses neither.

## Tech stack

- **Web:** Flask + Flask-CORS, served via WSGI on Elastic Beanstalk
- **Search & storage:** Weaviate Cloud (vectorizer `text-embedding-3-large`)
- **Reranking:** Voyage AI `rerank-2.5-lite`
- **LLM:** OpenAI — `gpt-4o-mini` (router) and `gpt-4.1` (extractive grader), both set in `search/config.py` and env-overridable. The legacy `/query` chat path still uses a fine-tuned `gpt-3.5-turbo` (id from `fine_tuned_model.txt`)
- **Auth:** app-signed JWT (bcrypt passwords) + Google OAuth
- **Infra-as-code:** AWS CDK for the API Gateway (`infra/cdk/`)

## Setup

Requires Python 3.9 and the project virtualenv.

```bash
source venv/bin/activate
pip install -r requirements.txt
python validate-env.py     # verify required env vars
python app.py              # serves on http://localhost:8000
```

### Environment

Set in `.env` (git-ignored):

| Variable | Purpose |
|----------|---------|
| `WEAVIATE_URL`, `WEAVIATE_API_KEY` | Weaviate Cloud connection |
| `OPENAI_API_KEY` | Embeddings + chat completions |
| `VOYAGE_API_KEY` | **Reranking (`rerank-2.5-lite`).** Required in every environment — without it reranking silently degrades to hybrid-score order and follow-ups return empty |
| `JWT_SECRET_KEY` | Signs manual-auth JWTs |
| `GOOGLE_CLIENT_ID` | Google OAuth verification (bypassed in dev if unset) |
| `FRONTEND_URL` | Comma-separated CORS allowlist; first entry is the default redirect target |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER` | Password-reset email |

`init_schema()` runs at startup and idempotently creates any missing Weaviate collections, so no manual DB migration is needed for a fresh environment.

## Architecture

```
Frontend ──► API Gateway ──► Elastic Beanstalk (Flask: app.py)
                                   │
                                   ├─ search/             (the search pipeline, one module per stage)
                                   └─ weaviate_client.py  (Weaviate client + schema)
                                          │
                            Weaviate Cloud  +  OpenAI  +  Voyage
```

- **`app.py`** — single-file Flask app: every route and all auth logic.
- **`search/`** — the pipeline (see **Search pipeline** below). `config.py` holds every model, threshold and flag, with the measurement behind each choice recorded beside it.
- **`weaviate_client.py`** — singleton client (`get_client`) and `init_schema`, which defines all collections: `Article` and `Passage` (the discourse corpus, chunked for search), `Entity` (canonical facts for the structured route), `ChatThread`, `Conversation`, `UserQuery`, `Feedback`, `SavedDiscourse`, `UserAccount`, `PasswordResetToken`.

> `utils.py` was split into `search/`; older references mean that package.

### Search pipeline (`/search` → `search_browse`)

**Routing first.** `plan_queries` (`PLAN_MODEL`, gpt-4o-mini) resolves multi-turn references, distills long scenarios, glosses romanized Sanskrit/Telugu, adaptively decomposes multi-concept messages into 1–N facets, **classifies intent, and extracts metadata filters** (book / volume / chapter range / year range / location / occasion). One call does all of it, with the prompt grounded in the corpus's real book list so "the Gita Vahini" comes back as the corpus's "Geeta Vahini". `search_browse` then dispatches:

| Intent | Route | Behaviour |
|---|---|---|
| `meta`, `out_of_domain`, `unanswerable` | guidance | Returns nothing plus a reason — the corpus can't answer it, so we don't pretend. The client is offered questions it *can* answer instead |
| `listing` | listing | Enumerates by metadata — book, chapter range, year, location, occasion — in chapter or date order |
| `factual`, `named_text`, `org_doctrine` | structured | Entity KB lookup: **hit** → canonical discourse, **gap** → honest abstention, **miss** → falls through to semantic |
| everything else | semantic | The passage pipeline below |

**Semantic route:**

1. **Per-facet retrieval** — each facet runs `search_passages` (Weaviate **hybrid** BM25+vector, `HYBRID_ALPHA = 0.5`) → **Voyage `rerank-2.5-lite`**, reranked against *its own* facet. Passages are tagged with the `source_query` that surfaced them. Facets fan out concurrently.
2. **`_rrf_merge`** — Reciprocal Rank Fusion across facets (dedupe by passage, keep best-rank provenance). The merge cap scales with facet count so no facet is starved.
3. **`grade_and_quote_passages`** (`JUDGE_MODEL`, gpt-4.1) — judges each passage **against its own facet** and extracts a verbatim 1–3 sentence `best_sentence`. Sharded into concurrent batches. Passages that don't answer, or whose quote can't be located verbatim, are dropped.
4. **`aggregate_to_discourses`** — one result per discourse (best passage wins). Output is **citations-only** (discourse + verbatim quote); no AI-synthesized answer.

**Two-phase response.** Grading dominates latency, so `/search` accepts `defer_grading: true` and returns reranked-but-ungraded candidates for immediate render (~3× faster first paint); the client POSTs the returned passage ids to `/search/verify` for verified quotes. Phase 2 is stateless — passages are re-read from Weaviate by id. Deferred results have **no quotes** and `quality: "pending"`; they must not be shown as answers.

**Transparency.** Sending `include_trace: true` returns `{results, trace}` describing how the search ran — planned facets, per-facet retrieval counts and which reranker actually ran, grader verdicts, timings, and machine-readable reason codes. `rerank_degraded` is set when reranking fell back to hybrid-score order, so degraded ranking is visible rather than silent.

**Caching.** Repeat questions short-circuit the whole pipeline (56% of real traffic is a repeat). Failures — service errors, failed listings — are never cached.

Harnesses: `eval_ragas.py` (218-question golden set, gates every search change), **`eval_router.py`** (router-only: 75 cases covering intent AND filter extraction, no server needed, seconds to run — use `--repeat 3`, since a single pass hides the intermittent misroutes), `eval_transliteration.py` (romanized robustness + `HYBRID_ALPHA`; α=0.5 confirmed optimal), `test_knowledge_guard.py` (Entity-route precision guards, network mocked), `test_router_unit.py` (filter validation + Weaviate filter composition — pure, no network).

### Auth

Two token types, both resolved by `get_verified_identity`:

1. **Manual** — bcrypt-hashed passwords in `UserAccount`; HS256 JWT signed with `JWT_SECRET_KEY`, carrying `token_type: "manual"`.
2. **Google OAuth** — verified via `google.oauth2.id_token` (signature check skipped if `GOOGLE_CLIENT_ID` is unset — dev only).

Protect a route with the `@require_auth` decorator; read the caller's email via `get_user_email_from_request()`. `ProxyFix` is applied so `request.host_url` reflects the real domain behind the AWS load balancer (matters for OAuth redirect URIs and reset links).

## API endpoints

| Method | Path | Notes |
|--------|------|-------|
| GET | `/` | Health check (includes `vector_store_healthy`) |
| POST | `/search` | Discourse search (router → hybrid+rerank → RRF → grade+quote); body `{query, history?, include_trace?, defer_grading?}`, returns discourses with a verbatim `best_sentence` quote |
| POST | `/search/verify` | Phase 2 of a `defer_grading` search: body `{query, passages:[{passage_id, facet}]}` → verified quotes, same shape as `/search` |
| POST | `/followups` | Verified follow-up questions grounded in the citations just returned |
| POST | `/query` | RAG answer with citations |
| POST | `/summarize-question` | Summarize/clean a user question |
| GET | `/blog/<id>` | Full article by id |
| GET | `/collections` | Grouped collection index (Vahinis, Sathya Sai Speaks, series, Chinna Katha) |
| GET | `/collections/chapters` | One collection's chapters; `?book=&volume=&year=` |
| POST | `/register`, `/login` | Manual auth |
| GET | `/auth/google/authorize`, `/auth/google/callback`, POST `/auth/google/login` | Google OAuth |
| POST | `/password/reset/request`, `/password/reset/verify`, `/password/reset/confirm` | Password reset |
| GET/POST/PUT/DELETE | `/saved-discourses/<user_email>[/<discourse_id>]` | Saved discourses (auth) |
| GET/POST/PUT/DELETE | `/chats/<user_email>` and `/chats/<thread_id>` | Chat threads (auth) |
| POST | `/chats/<user_email>/<thread_id>/messages` | Append message (auth) |
| POST | `/conversation/clear`, GET `/conversation/history` | Conversation memory |
| POST | `/api/feedback` | User feedback |

The full request/response contract lives in `infra/openapi.json` (generated from the routes — see below).

## Deployment

> **Always use the scripts. Manual deploys have caused production outages.**

```bash
# 1. Build the EB bundle (flattens backend/ to the zip root; excludes venv/.env/logs)
./package_eb.sh          # staging
./package_eb.sh prod     # production

# 2. Ship to Elastic Beanstalk
eb deploy asv-dev        # staging
eb deploy asv-prod       # production

# 3. If you added/changed/removed any @app.route, sync the gateway
./infra/sync_gateway.sh  # regenerates infra/openapi.json, then CDK-deploys
```

**Routes and the API Gateway are coupled.** A new or changed route will 404 *through API Gateway* (even though EB serves it) until you regenerate `infra/openapi.json` (`python infra/generate_openapi.py`) and redeploy via `sync_gateway.sh`. Beware sibling path-variable conflicts in API Gateway — e.g. `/chats/<user_email>` vs `/chats/<thread_id>` occupy the same path position and must be normalized.

### Environments

| | Staging | Production |
|--|---------|------------|
| AWS account | 522814696973 | 503561422699 |
| EB environment | `asv-dev` | `asv-prod` |
| API Gateway | `https://dxhp0j33db.execute-api.us-east-1.amazonaws.com/dev` | `https://bbqdh9uxll.execute-api.us-east-1.amazonaws.com/production/` |
| Public URL | — | https://asksaividya.com |

> AWS session tokens here contain `//` and `+` that break CLI shell parsing — prefer boto3 over `export` + `aws` CLI in scripts.

## Testing

The `test_*.py` files are **integration scripts that hit a running server**, not pytest unit tests:

```bash
BASE_URL=http://localhost:8000 python test_endpoints.py   # configurable via env
python test_backend.py                                    # hardcoded EB/Gateway URLs
python test_weaviate_connection.py                        # Weaviate connectivity
```

### Evaluating search changes

Search quality is gated by a 218-question golden set sampled from real traffic and weighted by the measured question taxonomy. **Run it before shipping any pipeline change:**

```bash
lsof -ti :8000 | xargs -r kill -9                   # kill by PORT — see the note below
SEMANTIC_CACHE_ENABLED=0 python app.py &            # the harness needs a live server
python eval_ragas.py --baseline eval_baseline.json  # prints a PASS/FAIL gate
```

> **Two ways this harness will lie to you.** The server runs as `.../MacOS/Python app.py`, so `pkill -f "python app.py"` matches nothing — the old process keeps :8000, the replacement dies on "Address already in use", and the whole run measures *the code you replaced*. And with the cache on, a repeat run replays stored results. **A p50 in single-digit milliseconds means you measured the cache, not the pipeline** — check it before trusting a scorecard.

Routing changes should be gated by `eval_router.py --repeat 3` first: it isolates the router, needs no server, and finishes in seconds.

| Metric | What it protects |
|---|---|
| `quote_answers_rate` | The top quote actually answers the question (LLM-judged — treat ±2-3 points as noise) |
| `abstention_correctness` | Unanswerable questions return **nothing** instead of a plausible-looking wrong answer |
| `exact_discourse_first` / `_only` | Naming a specific discourse returns *that* discourse first, ideally alone |

`eval_baseline.json` holds the shipped configuration's run; regenerate it when you intentionally move a metric. Related: `build_golden_set.py` (sample real traffic, LLM-draft labels for review), `promote_golden_set.py` (apply reviewed fixes + add exact-discourse cases), `replay_real_questions.py` (bulk replay), `backfill_entities.py` (populate the Entity KB — writes a review file, never to Weaviate).

Two traps this harness has already caught, both recorded in `search/config.py`:

- **Reasoning models carry a latency floor.** `gpt-5-mini` measured ~2× slower on both router and judge even at `reasoning_effort="minimal"`, and worse on every quality metric.
- **Check the worst case, not just p50.** `gpt-4.1-mini` grades quotes best of anything tested, but 3 of 218 queries exceeded API Gateway's 29s integration timeout (worst 63s — three client retries against the 20s per-call bound). Those are failed requests in production, not slow ones.
