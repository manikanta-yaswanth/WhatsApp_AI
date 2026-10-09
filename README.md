# WhatsApp Conversation Intelligence

Local CLI + Playwright ingestion + Pydantic + PostgreSQL/psycopg2 + LangGraph specialist agents + OpenAI + LLM-as-Judge.

Data flows directly from WhatsApp Web scraping through validated records into PostgreSQL, then to the agents.
No ingestion server, webhook token or application listening port is needed.

This project follows [AI_DATA_AGENTS](https://github.com/manikanta-yaswanth/AI_DATA_AGENTS)' role-based layout.
**There is no FastAPI server, SQLAlchemy ORM or Alembic dependency in this version.**

## Project structure

```text
Models/
  schema.py                  # validated ingestion, outputs and agent state
  records.py                 # typed database records (Pydantic, not ORM)
agents/
  whatsapp_agent.py          # router, specialist subgraphs, judge, retry
  contact_agent.py
  message_agent.py
  classification_agent.py
  search_agent.py
  analytics_agent.py
  data_quality_agent.py
  judge_agent.py
utils/
  database.py                # psycopg2 DatabaseUtil, scoped transactions
  repositories.py            # fixed parameterized SQL queries
  feed_db.py                 # atomic ingestion, deduplication, retention
  llm_pick.py                # agent / judge model selection
  tools.py                   # approved LangGraph tools
  agent_runner.py            # agent execution and persistence
  react.py                   # shared bounded specialist graph builder
  graph_export.py            # offline PNG export
  settings.py, prompts.py, structured.py, phone.py, hashing.py, time.py, logging.py
scraper/                     # persistent browser, login and extraction
data/schema.sql              # non-destructive SQL schema initializer
evaluation/                  # dataset, LLM judging and aggregate metrics
scripts/                     # login, scrape, seed and evaluation wrappers
tests/
main.py                      # CLI entrypoint
```

## Local setup in VS Code (Windows / PowerShell)

Prerequisites: [uv](https://docs.astral.sh/uv/getting-started/installation/), Python 3.12+ and a local PostgreSQL 14+ server.
Open a terminal in the repository folder:

```powershell
uv sync
uv run playwright install chromium
Copy-Item .env.example .env
```

On Linux/macOS, use `cp .env.example .env`. Fresh Linux machines may need
`uv run playwright install --with-deps chromium`.
Select the project's `.venv` interpreter in VS Code.

Create a dedicated database using pgAdmin's Query Tool or psql:

```sql
CREATE ROLE whatsapp_app LOGIN PASSWORD 'choose-a-local-password';
CREATE DATABASE whatsapp_ai OWNER whatsapp_app;
```

Or use an existing local PostgreSQL role and create `whatsapp_ai` with that owner.
Edit your **local, gitignored** `.env`:

```dotenv
database=whatsapp_ai
host=localhost
port=5432
user=whatsapp_app
password=choose-a-local-password
OPENAI_API_KEY=
```

Fill in `OPENAI_API_KEY` only when running AI commands. Login, scraping, demo data, inspection, quality, metrics
and graph export do not need it. Do not commit real credentials.

An optional `DATABASE_URL=postgresql://...` overrides all separate database fields.
Existing `postgresql+asyncpg://...` URLs are also accepted and converted for psycopg2.
If you switch to separate keys, remove or comment out the old `DATABASE_URL`.
OS environment variables override file values; use `DB_USER` to override the file's `user`.
The OS's `USER` variable is deliberately ignored.

```powershell
uv run python main.py init-db
uv run python main.py health
```

`init-db` creates missing tables/indexes using plain SQL. It does **not** create the database itself.
`health` prints the connected database name and `"connected": 1`.
No Uvicorn process or application socket is needed.

### Upgrading from the original FastAPI/Alembic version

1. Back up your database and keep your existing `.env` and WhatsApp browser profile.
2. Switch to this version, run `uv sync`, then `uv run python main.py init-db`.
3. Keep the same database connection. Existing contact IDs, messages, classifications, runs and evaluations remain.
4. Replace `uv run alembic upgrade head` with `uv run python main.py init-db`.
5. Replace Uvicorn/API requests with the CLI commands below.

The SQL schema matches the original `0001` migration. Initializing it again uses `IF NOT EXISTS`, does not drop
or truncate any tables, and leaves an existing `alembic_version` table untouched.
It is a bootstrap for this schema, not a general migration engine for arbitrary older/custom schemas.

## Demo data (no WhatsApp login)

Use a separate development database for fake data, not one containing your real chat history:

```powershell
uv run python main.py seed-demo
uv run python main.py contacts
uv run python main.py messages
uv run python main.py data-quality
uv run python main.py query "How many contacts are missing a phone number?"
```

The seed is idempotent. It writes four fake contacts and recent messages.
The last command needs an OpenAI key and incurs API usage.

## WhatsApp login and extraction

```powershell
uv run python main.py login
# Scan WhatsApp > Settings > Linked devices > Link a device in the opened browser.
uv run python main.py scrape
```

Login stores Chromium state in `playwright/.auth/whatsapp_profile`, including IndexedDB.
Do not share, commit or delete that profile if you want to preserve the login.
Close other processes using this project's profile before login/scraping.

Extraction tries WhatsApp Web's internal model store and falls back to DOM parsing.
These are unofficial interfaces and may change; **real logged-in extraction has not yet been validated**.
Groups are excluded by default; set `INCLUDE_GROUPS=true` to include them.
The extractor excludes archived chats, broadcasts and channels.

Contacts upsert by WhatsApp ID; messages deduplicate by message ID. Each ingestion transaction retains only
the newest `MAX_MESSAGES_PER_CONTACT` messages (default 3). This pruning is intentional: the database is a recent-message
store, not a complete chat archive. Scrape failures are recorded without persisting a partial ingestion batch.

## CLI reference

Every command runs from the repository root. Data commands print JSON to stdout and logs go to stderr.
Failures return a nonzero exit code.

| Command | Purpose |
|---|---|
| `init-db` | Add missing tables/indexes |
| `health` | Check PostgreSQL |
| `login --timeout 300` | Manual QR login in visible Chromium |
| `scrape` | Extract and persist one run; print its statistics |
| `seed-demo` | Load fake contacts/messages |
| `contacts --name John --limit 20` | Find contacts and recent-message counts |
| `contacts --phone-prefix +91` | Phone prefix filter |
| `contacts --missing-phone` | Find missing numbers (including privacy/LID contacts) |
| `messages --contact <UUID>` | Contact's recent messages |
| `messages --query meeting` | Keyword search |
| `query "Who asked for a meeting?"` | Router → specialist → tools → judge |
| `query "How many contacts?" --no-judge` | Skip judging |
| `classify --limit 50 --only-unclassified` | Persist conversation categories and actions |
| `classify --contact <UUID>` | Classify selected contact (flag can be repeated) |
| `summarize <UUID>` | Summarize a contact's stored messages |
| `data-quality` | Deterministic quality report |
| `evaluate --dataset evaluation/questions.json` | Run dataset and persist judge metrics |
| `metrics` | Aggregate evaluation, agent/token/latency and scrape metrics |
| `graph --output-dir data/graphs` | Export router + six specialist PNGs locally, without keys or network calls |

Use `uv run python main.py --help` or `uv run python main.py <command> --help`.
Existing `scripts/login.py`, `scripts/scrape.py`, `scripts/seed_demo.py` and `scripts/evaluate.py` commands remain usable.

"Training agents on PostgreSQL" here means giving LangGraph agents approved database tools and prompt context,
as in AI_DATA_AGENTS, not fine-tuning an LLM on private conversations.

## Agents and safe database tools

The structured-output router picks one of six specialist LangGraph subgraphs; each has its own tool node
and bounded ReAct loop. `utils/llm_pick.py` chooses `OPENAI_MODEL` and `OPENAI_JUDGE_MODEL` (default: agent model).

| Specialist | Approved tools |
|---|---|
| Contact | search_contacts, get_contact, count_contacts, get_recent_messages |
| Message | search_contacts, get_contact, get_recent_messages, get_recent_conversations, search_messages |
| Classification | classify_recent_conversations, search_contacts, get_recent_conversations, get_recent_messages |
| Search | search_messages, search_contacts, get_recent_messages, get_recent_conversations |
| Analytics | get_message_stats, count_contacts, search_contacts, data_quality_report |
| Data quality | data_quality_report, count_contacts, search_contacts |

The LLM never generates SQL or Python for execution. Tools use fixed queries with psycopg2-bound parameters.
Each database transaction opens and closes its own connection; agent tool threads never share connections.
LLM calls and browser operations run outside database transactions.

The judge scores correctness, relevance, groundedness, completeness and hallucination:
`final_score = .30 C + .20 R + .25 G + .15 Comp + .10 (1 − H)`.
A score below `JUDGE_THRESHOLD` triggers feedback and at most `JUDGE_MAX_RETRIES` retries.
Agent tools, iterations, tokens, latency and scores are persisted.
Retained-message changes invalidate classifications, including backfilled messages. If ingestion changes a
message snapshot while an LLM is classifying it, that result is skipped rather than saved; rerun classification.
`--only-unclassified` excludes contacts whose stored classification is still current.

## Evaluation

```powershell
uv run python main.py evaluate
uv run python main.py metrics
```

Dataset questions can include `expected`, `expected_contact` and `expected_tools`.
Metrics include judge accuracy, groundedness, hallucination rate, schema compliance, latency and tool accuracy.
Tool accuracy is expected-tool recall: the fraction of expected tools used, not a measure of unnecessary calls.
Scores are LLM judgments, not independent proof of correctness.

## Docker (optional)

If you already use local PostgreSQL, Docker is not required.

```powershell
docker compose up -d postgres
docker compose run --rm cli init-db
docker compose run --rm cli seed-demo
docker compose run --rm cli contacts
```

The compose PostgreSQL password is **only a local development default**, not a production credential.
The CLI container has no HTTP port. For QR login, use the native desktop commands above;
the container is intended for headless operations with an already authenticated profile.

## Tests

Tests use fake LLMs (no OpenAI calls) and a disposable PostgreSQL database. Each test creates and drops
only its own uniquely named schema, not real tables or rows.

```powershell
docker run -d --name wa-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=whatsapp_ai_test -p 55432:5432 postgres:16
$env:TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:55432/whatsapp_ai_test"
uv run pytest -q
uv run ruff check .
uv run mypy .
```

On Linux/macOS use `export TEST_DATABASE_URL=...`. The database name must contain `test`;
never point tests at a real-data database.

## Privacy

- `.env`, browser authentication state and generated diagrams/exports are gitignored.
- Message/query/answer content is redacted from application logs. It is still stored in PostgreSQL where needed.
- AI commands send selected contact/message data and questions to OpenAI. Use only with data you are authorized to process.
- Only recent messages are retained; use a dedicated, restricted PostgreSQL role and protect backups.
