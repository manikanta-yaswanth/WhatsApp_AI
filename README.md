# WhatsApp Conversation Intelligence Platform

Browser-based ingestion → Pydantic validation → PostgreSQL → FastAPI → LangGraph ReAct agents → OpenAI structured output → LLM-as-Judge → measurable evaluation.

```
WhatsApp Web ─► Playwright (persistent profile) ─► Parser ─► Pydantic ─► Dedup/Upsert ─► PostgreSQL
                                                                                         │
FastAPI /api/v1 ◄────────────────────────────────────────────────────────────────────────┤
   └─► LangGraph: route ─► specialist agent ⇄ approved tools (parameterized SQL) ─► Judge ─► retry?
```

## Stack
Python 3.12, uv, Playwright, FastAPI, Pydantic v2, SQLAlchemy 2 (async, asyncpg), Alembic, LangGraph, langchain-openai, structlog, pytest.

## Local setup (VS Code)

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/), a PostgreSQL 14+ server.

```bash
git clone <repo> && cd whatsapp-ai-platform
uv sync                                    # creates .venv (select it as the VS Code interpreter)
uv run playwright install chromium         # add --with-deps on a fresh Linux box
cp .env.example .env
```

Create the database (psql as a superuser), or skip this and run `docker compose up -d postgres`:
```sql
CREATE ROLE whatsapp_app LOGIN PASSWORD 'change-me';
CREATE DATABASE whatsapp_ai OWNER whatsapp_app;
```

Edit `.env`:
```env
DATABASE_URL=postgresql+asyncpg://whatsapp_app:change-me@localhost:5432/whatsapp_ai
OPENAI_API_KEY=sk-...
```

Create the tables and check the connection:
```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --reload       # http://localhost:8000/api/v1/health  ->  "database": "ok"
```

Windows: use the same commands in PowerShell; `uv` installs with
`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.

Optional — try the API and agents without WhatsApp by loading fake demo data:
```bash
uv run python scripts/seed_demo.py
```

## 1. Log in to WhatsApp (once)

```bash
uv run python scripts/login.py
```
A Chromium window opens; scan the QR code from your phone (Settings → Linked devices). The session is kept in
`playwright/.auth/whatsapp_profile` (gitignored — it can impersonate your account; never commit or share it).

## 2. Scrape

```bash
uv run python scripts/scrape.py                     # CLI, synchronous
# or via API (asynchronous, returns run_id):
curl -X POST localhost:8000/api/v1/scrape/start
curl localhost:8000/api/v1/scrape/<run_id>
```
- Reads WhatsApp Web's in-page model store (stable) and falls back to DOM parsing if the store is unavailable.
- Contacts upserted by `whatsapp_id`; messages inserted once by `whatsapp_message_id`; only the newest
  `MAX_MESSAGES_PER_CONTACT` (default 3) are kept per contact. Re-running is idempotent.
- Groups are skipped unless `INCLUDE_GROUPS=true`. Archived chats, broadcasts and channels are skipped.

## 3. API

```bash
uv run uvicorn app.main:app --reload       # docs at http://localhost:8000/docs
```

| Method | Path | |
|---|---|---|
| GET | `/api/v1/health` | DB / LLM / session status |
| POST | `/api/v1/scrape/start` · GET `/scrape/{run_id}` · GET `/scrape/runs` | async scrape runs |
| GET | `/api/v1/contacts?name=&phone=&has_phone=&category=` · `/contacts/{id}` | contacts |
| GET | `/api/v1/contacts/{id}/messages` · `/messages?q=` | messages |
| POST | `/api/v1/agents/query` `{"query": "..."}` | ReAct agent + judge score |
| POST | `/api/v1/agents/classify` · `/agents/summarize` | conversation classification / summary |
| GET | `/api/v1/agents/data-quality` | deterministic data quality report |
| POST | `/api/v1/evaluation/run` · GET `/evaluation/{id}` | evaluation dataset run |
| GET | `/api/v1/metrics` | accuracy, groundedness, hallucination, tool accuracy, latency, tokens |

Set `API_KEY` in `.env` to require an `X-API-Key` header on everything except `/health`.

## Agents
A router (structured output, keyword fallback) picks one specialist; each gets only its own tools:

| Agent | Tools |
|---|---|
| Contact | search_contacts, get_contact, count_contacts, get_recent_messages |
| Message | + get_recent_conversations, search_messages |
| Classification / Follow-up | classify_recent_conversations (category + ACTION_REQUIRED/FOLLOW_UP/NO_ACTION, stored) |
| Search | search_messages, search_contacts, ... |
| Analytics | get_message_stats, count_contacts, data_quality_report |
| Data Quality | data_quality_report |

The LLM never writes SQL — tools are fixed, parameterized queries. After answering, the **Judge** scores
correctness, relevance, groundedness (vs. tool observations), completeness and hallucination;
`final_score = .30 C + .20 R + .25 G + .15 Comp + .10 (1 − H)`. Below `JUDGE_THRESHOLD` the agent gets the
judge's feedback and retries (`JUDGE_MAX_RETRIES`). Every run is stored in `agent_runs` (tools, iterations,
tokens, latency, score).

## Evaluation
```bash
uv run python scripts/evaluate.py          # uses evaluation/questions.json
```
Questions may include `expected`, `expected_contact` and `expected_tools` (→ tool accuracy).

## Tests
Tests use a throwaway Postgres whose DB name must contain `test`, and a scripted fake LLM (no OpenAI calls):
```bash
docker run -d --name wa-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=whatsapp_ai_test -p 55432:5432 postgres:16
uv run pytest
uv run ruff check . && uv run mypy app
```

## Privacy
- Message contents are never logged (structlog processor redacts content keys).
- Only the latest 3 messages per contact are stored.
- Use a restricted Postgres role; keep `.env` and `playwright/.auth/` out of git.
