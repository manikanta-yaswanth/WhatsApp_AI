# WhatsApp Conversation Intelligence

Local CLI + Playwright ingestion + Pydantic + PostgreSQL/psycopg2 + LangGraph specialist agents + OpenAI + LLM-as-Judge.

Data flows directly from WhatsApp Web scraping through validated records into PostgreSQL, then to the agents.
No ingestion server, webhook token or application listening port is needed.
Scraped contact details and the latest three messages are stored together in **one table: `whatsapp_contacts`**.

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

## Single-table storage

Each contact has one row in `whatsapp_contacts`, uniquely identified by `whatsapp_id`.

| Column | Stored information |
|---|---|
| `id` | Stable database UUID, used by CLI/agent commands |
| `contact_name` | Name, when WhatsApp provides it |
| `whatsapp_id` | WhatsApp contact/chat identifier, including privacy/LID identifiers |
| `phone_number` | Validated phone number, or NULL when unavailable |
| `is_group` | Whether the chat is a group |
| `recent_messages` | JSONB array of the newest messages, newest first; default retention is 3 |
| `unread_count`, `last_message_at`, `last_scraped_at` | Conversation metadata |
| `category`, `action`, `category_confidence`, `category_reason`, `classified_at` | Agent classification |
| `created_at`, `updated_at` | Contact timestamps |

Each message in `recent_messages` includes its stable WhatsApp message ID, database UUID, sender type/name,
message type/text, message timestamp and scrape timestamp. Contacts with no messages have an empty array (`[]`).
No phone number is invented for a contact whose number is unavailable.

`contact_records`, `conversation_records` and `message_records` are SQL **views**, not additional storage tables.
They project this same row/JSON for the existing parameterized agent tools and CLI.
`scrape_runs`, `agent_runs`, `evaluation_runs` and `evaluations` remain separate operational/AI-run tables.
All new scraped contacts, messages and conversation classifications are written only to `whatsapp_contacts`.

## Local PostgreSQL setup in VS Code (Windows / PowerShell)

Prerequisites: [uv](https://docs.astral.sh/uv/getting-started/installation/), Python 3.12+ and a local PostgreSQL 14+ server.
Install PostgreSQL from [the official Windows download page](https://www.postgresql.org/download/windows/) if needed.
During installation, keep the local port (normally 5432) and remember your chosen password privately.
If PostgreSQL already works, do not reinstall it.

### 1. Open the project and install dependencies

Open VS Code's **Terminal → New Terminal**, select PowerShell, and open the folder containing `main.py`.
Replace this example folder with yours:

```powershell
Set-Location "C:\WhatsApp_Agents"
Test-Path .\main.py
uv --version
uv python install 3.12
uv sync
uv run playwright install chromium
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
```

`Test-Path` must print `True`. Install uv from its linked instructions if the command is unavailable.
The conditional copy preserves existing credentials instead of overwriting them.
On Linux/macOS, use `cp .env.example .env`. Fresh Linux machines may need
`uv run playwright install --with-deps chromium`.
In VS Code, use **Python: Select Interpreter** and select `.venv\Scripts\python.exe`.

### 2. Confirm PostgreSQL is running

```powershell
Get-Service *postgres*
Test-NetConnection localhost -Port 5432
```

Expect a running PostgreSQL service and `TcpTestSucceeded: True`.
Start a stopped service through Windows Services (`services.msc`).
If your PostgreSQL uses another port, use that port consistently here and in the configuration.
This listener check does not test credentials; `health` below does.
No tunnel, public IP or inbound firewall rule is needed.

### 3. Create separate demo and real databases

In **pgAdmin**, connect to your local server with your existing PostgreSQL role/password.
Select the existing `postgres` database, open **Tools → Query Tool**, and enable auto-commit.
Execute these statements **one at a time**, not together in a transaction:

```sql
CREATE DATABASE whatsapp_ai_demo OWNER postgres;
```

```sql
CREATE DATABASE whatsapp_ai OWNER postgres;
```

Replace `postgres` with your existing role if different. If a database exists, do not drop it;
reuse it only for its intended purpose or choose a new name.
Use `whatsapp_ai_demo` for fake data and `whatsapp_ai` for real chats.
For longer-term use, prefer a dedicated role that owns only these application databases.

### 4. Configure your private database connection

Open the **local, gitignored** configuration:

```powershell
code .env
```

Start with the demo database. Replace the password placeholder **only on your machine**:

```dotenv
database=whatsapp_ai_demo
host=localhost
port=5432
user=postgres
password="YOUR_LOCAL_POSTGRES_PASSWORD"
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
WHATSAPP_HEADLESS=false
MAX_MESSAGES_PER_CONTACT=3
MAX_CHATS_PER_SCRAPE=10
INCLUDE_GROUPS=false
```

Fill in `OPENAI_API_KEY` only when running AI commands. Login, scraping, demo data, inspection, quality, metrics
and graph export do not need it. AI commands incur API usage and send selected stored conversation data to OpenAI.
Do not commit or share passwords, keys, private messages or browser authentication files.

An optional `DATABASE_URL=postgresql://...` overrides all separate database fields.
Existing `postgresql+asyncpg://...` URLs are also accepted and converted for psycopg2.
If you switch to separate keys, remove or comment out the old `DATABASE_URL`.
OS environment variables override file values; use `DB_USER` to override the file's `user`.
The OS's `USER` variable is deliberately ignored.
If you previously set overrides for this project, clear stale ones without printing their contents:

```powershell
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:DATABASE -ErrorAction SilentlyContinue
Remove-Item Env:DB_USER -ErrorAction SilentlyContinue
```

Save the file as `.env`, not `.env.txt`, beside `main.py`.
An old `WEBHOOK_TOKEN` entry can be deleted; it is no longer used.

### 5. Initialize and verify the database

```powershell
uv run python main.py init-db
uv run python main.py health
```

`init-db` creates missing tables/indexes using plain SQL. It does **not** create the database itself.
`health` prints the connected database name and `"connected": 1`.
No Uvicorn process or application socket is needed.
In pgAdmin, refresh **whatsapp_ai_demo → Schemas → public → Tables** and find `whatsapp_contacts`.
The three record projections appear under **Views**.

## Updating an older local project

1. Back up your database with pgAdmin. Keep your private `.env` and `playwright/.auth/whatsapp_profile`.
   Store any configuration backup **outside the repository**, not in a new tracked file.
2. Stop old application/scraper processes so they do not continue writing to the legacy tables.
3. For a Git clone, run:

```powershell
git status --short
git pull origin main
uv sync
uv run playwright install chromium
uv run python main.py init-db
uv run python main.py health
```

If Git reports local changes/conflicts, **stop rather than force/reset the checkout**.
Updating removes obsolete tracked API/migration/webhook files without rewriting Git history.
Do not manually copy the new code over an old ZIP folder and leave obsolete files mixed in;
extract into a fresh folder instead, then restore only your private configuration and authentication profile.

`init-db` creates `whatsapp_contacts` and copies data from the original `contacts`, `conversations` and
`messages` tables when all three exist. It preserves contact/message UUIDs, saved messages and classifications.
Existing operational runs/evaluations and any `alembic_version` marker remain.
Rerunning initialization does not overwrite newer single-table rows with stale legacy data.
Legacy tables are retained as untouched snapshots; this version no longer writes to them.
Saved legacy messages are copied without truncation; the next scrape applies the configured retention limit to that contact.
Do not run old and new versions against this database simultaneously.

`init-db` does not drop/truncate tables and is not a general migration engine for arbitrary custom schemas.
Replace Alembic/Uvicorn/API calls with the CLI commands below.

## End-to-end checks

### 1. Demo persistence, before WhatsApp or OpenAI

Confirm `health` names `whatsapp_ai_demo`, not your real-chat database. Then:

```powershell
uv run python main.py seed-demo
uv run python main.py contacts
uv run python main.py messages
uv run python main.py data-quality
uv run python main.py metrics
uv run python main.py seed-demo
```

On a fresh demo database expect **4 contact rows and 7 messages inside their JSON arrays**.
Repeating the seed saves zero new contacts/messages. Two demo contacts have no validated phone number.
The seed is not for your real-chat database.

Open Query Tool on `whatsapp_ai_demo` and inspect the single table:

```sql
SELECT contact_name, whatsapp_id, phone_number,
       jsonb_array_length(recent_messages) AS stored_message_count,
       recent_messages -> 0 ->> 'message_text' AS latest_message,
       recent_messages
FROM whatsapp_contacts
ORDER BY last_message_at DESC NULLS LAST;

SELECT count(*) AS contacts,
       coalesce(sum(jsonb_array_length(recent_messages)), 0) AS messages
FROM whatsapp_contacts;
```

### 2. Agents on the demo database

Save your OpenAI key locally, then run:

```powershell
uv run python main.py query "How many contacts are stored?"
uv run python main.py query "Search my messages for an interview."
uv run python main.py classify --limit 10
uv run python main.py classify --limit 10 --only-unclassified
uv run python main.py contacts
uv run python main.py summarize "PASTE_CONTACT_UUID_FROM_CONTACTS_HERE"
uv run python main.py evaluate
uv run python main.py metrics
```

Use the database UUID, not a phone number, for `summarize`.
An empty `--only-unclassified` result is normal when all current conversations are already classified.
Compare count/search/summary answers against the stored demo records; evaluation scores are not independent proof.

### 3. Switch to the real database, log in and scrape

Change only `database=whatsapp_ai` in your private configuration (or the database in `DATABASE_URL` if used),
save it, and verify the switch before scraping. **Do not seed demo data into this database.**

```powershell
uv run python main.py init-db
uv run python main.py health
uv run python main.py login --timeout 300
# Scan WhatsApp > Settings > Linked devices > Link a device in the opened browser.
uv run python main.py scrape
uv run python main.py contacts
uv run python main.py messages --limit 10
uv run python main.py data-quality
```

Expect the scrape result to report `status: completed` with found/saved counts.
Zero newly saved records on a repeat is normal for already stored message IDs; zero extracted chats on the first
authenticated scrape needs investigation. Compare names/messages/timestamps against WhatsApp yourself.
Start with `MAX_CHATS_PER_SCRAPE=10`, then increase to 200 once the small scrape is correct.
Run `scrape` again whenever you want to refresh the stored messages; there is no automatic live subscription.

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

### 4. Check duplicates and retention, then query real data

In pgAdmin's Query Tool on `whatsapp_ai`, each query below should return no rows
(an upgraded contact may exceed 3 until its first scrape applies the new limit):

```sql
SELECT whatsapp_id, count(*)
FROM whatsapp_contacts GROUP BY whatsapp_id HAVING count(*) > 1;

SELECT whatsapp_message_id, count(*)
FROM message_records GROUP BY whatsapp_message_id HAVING count(*) > 1;

SELECT contact_name, jsonb_array_length(recent_messages) AS stored_message_count
FROM whatsapp_contacts WHERE jsonb_array_length(recent_messages) > 3;
```

Repeat the scrape and confirm existing message IDs do not duplicate. Then run a count or summary and compare
it with the retained PostgreSQL records, not your full WhatsApp history:

```powershell
uv run python main.py query "How many contacts are stored?"
uv run python main.py metrics
```

Your Windows/PostgreSQL setup and real-account extraction must be validated locally.

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

### With your local PostgreSQL (no Docker needed)

In pgAdmin, with auto-commit enabled, create a separate disposable database:

```sql
CREATE DATABASE whatsapp_ai_test OWNER postgres;
```

Replace the owner if you use another role. The following PowerShell command copies your local connection
settings but changes the database name to `whatsapp_ai_test`; the password is captured, not displayed:

```powershell
$env:TEST_DATABASE_URL = (uv run python -c "from utils.settings import get_settings; from psycopg2.extensions import make_dsn; c=dict(get_settings().db_config); c['dbname']='whatsapp_ai_test'; print(make_dsn(**c))").Trim()
uv run pytest -q
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

Do not print/share `TEST_DATABASE_URL`; it contains connection credentials.
The fixture accepts PostgreSQL DSNs as well as URLs. Do not use a real-data or demo database for tests.

### With a disposable Docker PostgreSQL

```powershell
docker run -d --name wa-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=whatsapp_ai_test -p 55432:5432 postgres:16
$env:TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:55432/whatsapp_ai_test"
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy .
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
```

On Linux/macOS use `export TEST_DATABASE_URL=...`. The database name must contain `test`;
never point tests at a real-data database.

## Troubleshooting

| Symptom | What to check |
|---|---|
| `uv` or `main.py` not found | Install uv/reopen VS Code; open the repository root, not the outer ZIP folder. |
| PostgreSQL operation failed | Check service, port, database existence, password/role permissions and configuration precedence; connect with the same values in pgAdmin. |
| Database does not exist | Create it first; `init-db` creates tables, not databases. |
| Database setting seems ignored | Remove an old `DATABASE_URL` or stale environment override and save the correct `.env`. |
| Tables not visible | Refresh Query Tool/Tables on the database named by `health`; record projections are under Views. |
| Old `contacts/messages/conversations` tables remain | They are retained migration snapshots; inspect `whatsapp_contacts` for current data. |
| Browser executable missing | Run `uv run playwright install chromium`. |
| Browser profile is locked | Close other processes using this project's profile; do not delete authentication state. |
| Login timeout or zero extracted chats | Rerun login, use `WHATSAPP_HEADLESS=false`, verify eligible chats are visible; provide only sanitized statistics/errors. |
| OpenAI authentication/quota error | Check your local key, API billing/model access and stale environment overrides; never send the key. |
| Empty `--only-unclassified` output | Normal when current conversations already have classifications. |
| Missing phone number | LID/privacy identifiers or invalid phone data may have no reliable number; the WhatsApp ID still identifies the row. |
| Old Uvicorn, Alembic or webhook command fails | These features were removed; use `init-db`, `login` and direct `scrape`. |

Check `$LASTEXITCODE` after a command: zero means it reported success, not that every extracted record or AI answer
is correct. Verify stored records against the source. Send only redacted errors, never configuration files/private chats.

## Privacy

- `.env`, browser authentication state and generated diagrams/exports are gitignored.
- Message/query/answer content is redacted from application logs. It is still stored in PostgreSQL where needed.
- AI commands send selected contact/message data and questions to OpenAI. Use only with data you are authorized to process.
- Only recent messages are retained; use a dedicated, restricted PostgreSQL role and protect backups.
