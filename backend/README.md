# Ask Sai Vidya — Backend

Flask backend for an AI-powered search and Q&A service over a corpus of Sai Baba discourses. It combines **Weaviate hybrid search** (semantic + keyword) with **OpenAI** for answer generation. All application data — users, chats, saved discourses, feedback, and the discourse corpus itself — is stored in **Weaviate collections**.

> Deployed as **AWS API Gateway → Elastic Beanstalk**. There are stale Docker/Railway/MongoDB references elsewhere in the history; the live system uses neither.

## Tech stack

- **Web:** Flask + Flask-CORS, served via WSGI on Elastic Beanstalk
- **Search & storage:** Weaviate Cloud (vectorizer `text-embedding-3-large`)
- **LLM:** OpenAI — a fine-tuned `gpt-3.5-turbo` (id read from `fine_tuned_model.txt`)
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
| `JWT_SECRET_KEY` | Signs manual-auth JWTs |
| `GOOGLE_CLIENT_ID` | Google OAuth verification (bypassed in dev if unset) |
| `FRONTEND_URL` | Comma-separated CORS allowlist; first entry is the default redirect target |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER` | Password-reset email |

`init_schema()` runs at startup and idempotently creates any missing Weaviate collections, so no manual DB migration is needed for a fresh environment.

## Architecture

```
Frontend ──► API Gateway ──► Elastic Beanstalk (Flask: app.py)
                                   │
                                   ├─ utils.py            (search + RAG + LLM orchestration)
                                   └─ weaviate_client.py  (Weaviate client + schema)
                                          │
                                   Weaviate Cloud  +  OpenAI
```

- **`app.py`** — single-file Flask app: every route and all auth logic.
- **`utils.py`** — the search + answer logic (see **Search pipeline** below): the `plan_queries → per-facet hybrid+rerank → RRF → grade+quote → aggregate` discourse search used by `/search`, plus the legacy `handle_user_query` RAG entrypoint (`/query`) and conversation persistence.
- **`weaviate_client.py`** — singleton client (`get_client`) and `init_schema`, which defines all collections: `Article` and `Passage` (the discourse corpus, chunked for search), `ChatThread`, `Conversation`, `UserQuery`, `Feedback`, `SavedDiscourse`, `UserAccount`, `PasswordResetToken`.

### Search pipeline (`/search` → `search_browse`)

1. **`plan_queries(message, history)`** (gpt-4o-mini) — turns the user message into **1–N standalone search queries**: resolves multi-turn references against recent `history`, distills long "chatbot-style" scenarios to their core spiritual concept(s), glosses romanized Sanskrit/Telugu terms (corpus spelling + variants + English meaning), and **adaptively** decomposes only genuinely multi-concept messages (defaults to one query). Supersedes the old `expand_short_query`.
2. **Per-facet retrieval** — each sub-query runs through `search_passages` (Weaviate **hybrid** BM25+vector, `HYBRID_ALPHA = 0.5`) → **Cohere rerank** (`rerank-v3.5`), reranked against *its own* facet. Each passage is tagged with the `source_query` that surfaced it.
3. **`_rrf_merge`** — fuses the per-facet lists with Reciprocal Rank Fusion (dedupe by passage, keep best-rank provenance).
4. **`grade_and_quote_passages`** (gpt-4o) — one batched call judges each passage **against its own facet** and extracts a verbatim 1–3 sentence `best_sentence`; passages that don't answer their facet (or have no locatable quote) are dropped.
5. **`aggregate_to_discourses`** — collapses to one result per discourse (best passage wins). Output is **citations-only** (discourse + verbatim quote); no AI-synthesized answer.

`eval_transliteration.py` is the romanized-robustness + `HYBRID_ALPHA` tuning harness (α=0.5 confirmed optimal).

### Auth

Two token types, both resolved by `get_verified_identity`:

1. **Manual** — bcrypt-hashed passwords in `UserAccount`; HS256 JWT signed with `JWT_SECRET_KEY`, carrying `token_type: "manual"`.
2. **Google OAuth** — verified via `google.oauth2.id_token` (signature check skipped if `GOOGLE_CLIENT_ID` is unset — dev only).

Protect a route with the `@require_auth` decorator; read the caller's email via `get_user_email_from_request()`. `ProxyFix` is applied so `request.host_url` reflects the real domain behind the AWS load balancer (matters for OAuth redirect URIs and reset links).

## API endpoints

| Method | Path | Notes |
|--------|------|-------|
| GET | `/` | Health check (includes `vector_store_healthy`) |
| POST | `/search` | Discourse search (planner → hybrid+rerank → RRF → grade+quote); body `{query, history?}`, returns discourses with a verbatim `best_sentence` quote |
| POST | `/query` | RAG answer with citations |
| POST | `/summarize-question` | Summarize/clean a user question |
| GET | `/blog/<id>` | Full article by id |
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
