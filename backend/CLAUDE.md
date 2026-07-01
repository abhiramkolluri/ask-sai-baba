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

Required env (from `.env`): `WEAVIATE_URL`, `WEAVIATE_API_KEY`, `OPENAI_API_KEY`, plus `MONGO_URI`, `JWT_SECRET_KEY`, `GOOGLE_CLIENT_ID`, and `MAIL_*` for password-reset email. `FRONTEND_URL` is a comma-separated CORS allowlist (first entry is the default redirect target).

## Testing

The `test_*.py` files are **integration scripts that hit a running server**, not pytest unit tests. They require a live backend (local or deployed):

```bash
BASE_URL=http://localhost:8000 python test_endpoints.py   # configurable via env
python test_backend.py                                    # has hardcoded EB/Gateway URLs
python test_weaviate_connection.py                        # checks Weaviate connectivity
```

## Deployment — use the scripts, never deploy manually

Manual deploys have caused production outages. Always use:

```bash
./package_eb.sh                 # tag=staging, env from .elasticbeanstalk/config.yml
./package_eb.sh prod            # build the prod zip
eb deploy <env>                 # asv-dev (staging) or asv-prod (production)

./infra/sync_gateway.sh         # regenerate OpenAPI from routes + CDK deploy the gateway
```

`package_eb.sh` flattens `backend/` to the zip root (so `app.py` is at the archive root, which EB expects) and excludes venv/logs/.env. It warns if `app.py` is newer than `infra/openapi.json`.

**Critical coupling: routes and the API Gateway must stay in sync.** Whenever you add, remove, or change a `@app.route` path, you must regenerate the OpenAPI spec and redeploy the gateway, or the new route will 404 through API Gateway even though EB serves it. `infra/generate_openapi.py` derives `infra/openapi.json` from the Flask routes; `sync_gateway.sh` diffs it against `openapi.json.prev`, runs CDK (`infra/cdk/`), then promotes the spec to `.prev`. Watch for sibling path-variable conflicts in API Gateway (e.g. `/chats/<user_email>` vs `/chats/<thread_id>` — same position, different names collide; recent commits normalized these).

## Architecture

**Request flow:** Frontend → API Gateway → EB/Flask (`app.py`) → `utils.py` (search + LLM orchestration) → `weaviate_client.py` (Weaviate) + OpenAI.

- **`app.py`** (~1150 lines) — single-file Flask app holding every route and all auth. Routes group into: health (`/`), auth (`/auth/google/*`, `/register`, `/login`, `/password/reset/*`), search/query (`/search`, `/query`, `/summarize-question`, `/blog/<id>`), saved discourses, chats, conversation memory, and feedback.
- **`utils.py`** — search and answer generation: `handle_user_query` (main RAG entrypoint), `search_browse`/`search_exact`/`search` (Weaviate hybrid queries, alpha≈0.75 vector-weighted), `classify_query`, conversation persistence, and OpenAI calls.
- **`weaviate_client.py`** — singleton Weaviate Cloud client (`get_client`) and `init_schema`, which defines all collections: `Article` (the discourse corpus, vectorized with `text-embedding-3-large`), `ChatThread`, `Conversation`, `UserQuery`, `Feedback`, `SavedDiscourse`, `UserAccount`, `PasswordResetToken`. `init_schema` runs at import time in `app.py` and is idempotent (creates only missing collections); lightweight property migrations are done inline in `app.py` startup.

**Auth model:** Two token types, both checked in `get_verified_identity`:
1. **Manual** — bcrypt-hashed passwords in `UserAccount`, app-signed HS256 JWT (`JWT_SECRET_KEY`), must carry `token_type: 'manual'`.
2. **Google OAuth** — verified via `google.oauth2.id_token`. If `GOOGLE_CLIENT_ID` is unset, signature verification is bypassed (dev fallback only).

Protect routes with the `@require_auth` decorator; read the caller's email with `get_user_email_from_request()`.

**Model:** LLM calls use a fine-tuned model id read at call time from `fine_tuned_model.txt` via `fine_tuning.load_fine_tuned_model_id_from_file()` (currently a fine-tuned `gpt-3.5-turbo`). Embeddings use `text-embedding-3-large`.

**Proxy awareness:** `ProxyFix` is applied so `request.host_url` reflects the real domain behind the AWS load balancer — relevant for OAuth redirect URIs and password-reset links (`get_base_url`, `get_google_redirect_uri`).

## Environments

- **Staging:** account 522814696973, EB `asv-dev`, gateway `https://dxhp0j33db.execute-api.us-east-1.amazonaws.com/dev`
- **Production:** account 503561422699, EB `asv-prod`, gateway `https://bbqdh9uxll.execute-api.us-east-1.amazonaws.com/production/`, fronts https://asksaividya.com

AWS session tokens here contain `//` and `+` that break CLI shell parsing — prefer boto3 (via Python) over `export` + `aws` CLI for scripted AWS calls.
