# CatalogIQ

CatalogIQ takes messy product listings (like `AMUL butter 500G pck of 2`) and uses an LLM to turn them into a clean, searchable catalogue: a tidy title, one category, a brand and up to 5 tags.

The focus is the pipeline around the LLM: background jobs, a global limit on parallel LLM calls, retries with backoff, validation, no repeated work (cache + in-flight dedupe), crash recovery, and a fast API.

**Stack:** Python standard library only (`http.server`, `threading`, `sqlite3`), plain HTML/CSS/JS frontend. No packages to install.

## Setup (clean machine)

You only need **Python 3.9 or newer**.

```bash
git clone <your-repo-url>
cd catalogiq
python --version        # on Mac/Linux you may need: python3 --version
```

That's it, there is no `pip install` step.

## Run in mock mode (default)

```bash
python server.py        # Mac/Linux: python3 server.py
```

Open http://localhost:8000, choose `data/sample_products.csv` and press **Upload**.

Settings come from environment variables (or a `.env` file, see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock`, `ollama` or `groq` |
| `LLM_CONCURRENCY` | `5` | max LLM calls in progress at once, across all jobs |
| `MOCK_LATENCY_MS` | `200` | how long each mock call takes |
| `MOCK_FAILURE_RATE` | `0.1` | chance that a mock call fails |
| `BACKOFF_MS` | `200` | first retry wait (doubles each retry) |
| `DB_PATH` | `catalogiq.db` | SQLite file (data survives restarts) |
| `PORT` | `8000` | server port |

Example: `LLM_CONCURRENCY=2 MOCK_LATENCY_MS=500 python server.py` (on Windows PowerShell: `$env:LLM_CONCURRENCY=2; python server.py`).

## Run with the real LLM

**Real LLM used: Groq (free tier).** Ollama is also supported.

Groq:
1. Create a free key at https://console.groq.com
2. Copy `.env.example` to `.env` and set `LLM_PROVIDER=groq` and `GROQ_API_KEY=your_key`
3. `python server.py`

(The default model is `openai/gpt-oss-20b`. Free tiers and model names change, so set `GROQ_MODEL` if needed.)

Ollama (runs on your laptop, no key):
1. Install Ollama, then `ollama pull llama3.2:3b`
2. `LLM_PROVIDER=ollama python server.py`

Keys are only read from environment variables / `.env`; `.env` is git-ignored.

## Run the tests

```bash
python -m unittest discover tests -v
```

The tests use a fake LLM, so no internet is needed. They cover the concurrency limit (inside one job and across jobs), retries and exponential backoff, validation, de-duplication (including duplicates processed at the same moment), crash recovery, and the API contract (status codes, error shape, pagination, search, PATCH).

## Project layout

```
server.py     start here: HTTP server + static files
api.py        all API routes and input checks
pipeline.py   the core: semaphore, retry, validation, cache, dedupe, metrics
llm.py        MockLLM, OllamaLLM, GroqLLM (same interface) + the prompt
database.py   SQLite tables and every SQL query
config.py     settings from environment variables
static/       index.html, style.css, script.js
data/         sample_products.csv (210 listings, with duplicates)
tests/        unit tests
DESIGN.md     system design and diagram
```

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | `{"status","llm_provider","llm_concurrency"}` |
| GET | `/api/metrics` | `llm_calls_total`, `llm_errors_total`, `max_concurrent_llm_calls` |
| POST | `/api/jobs` | `{"products":[...]}` returns `202` and the job object; `400` on bad input |
| GET | `/api/jobs/<id>` | live progress; `404` if unknown |
| GET | `/api/products` | `page`, `page_size` (default 20, max 100), `category`, `q`; sorted by SKU |
| GET | `/api/products/<sku>` | `404` if unknown |
| PATCH | `/api/products/<sku>` | `clean_title`, `category`, `tags`; sets status `approved` |

Every error is `{"error": "message"}`.

## The prompt

```
You clean up messy product listings for an online store.
Reply with ONLY one JSON object and nothing else. It must have exactly these keys:
  "clean_title": a tidy, human-readable product title (string)
  "category": exactly one of: Groceries, Beverages, Personal Care, Household, Electronics, Fashion, Home & Kitchen, Other
  "brand": the brand name as a string, or null if you are not sure
  "tags": a list of at most 5 short lowercase strings
Do not invent a brand. If the category is unclear, use Other.

Raw title: <raw_title>
Raw description: <raw_description>
```

Whatever the model returns is checked by `check_llm_output()` in `pipeline.py`. Invalid JSON or a category outside the list counts as a failed attempt and is retried.

## Assumptions

- `done` counts every finished item, including failed ones, so the progress bar reaches 100% and a job is `completed` when `done == total`. `failed` is how many of those failed.
- If the same SKU appears twice in one upload, the last one wins, and `total` counts unique SKUs. Uploading a SKU that already exists overwrites it (even if it was approved).
- A product only appears in the catalogue after it has been processed (`enriched` or `failed`).
- The job goes `queued` -> `running` when its first item is picked up, and `started_at` is set then.
- Metrics live in memory, so they reset when the server restarts.
- `page_size` above 100 is reduced to 100. A non-numeric `page`/`page_size` gives `400`.
- `PATCH` with more than 5 tags, an invalid category or an empty title gives `400`; tags are lowercased.
- If the LLM returns more than 5 tags, or uppercase tags, they are lowercased and cut to 5 instead of failing the item.
- If several products with the same content are processed together and the LLM fails 4 times, they all get that error (the LLM is not retried separately for each).
- Times are UTC.

## What is unfinished

- No authentication.
- Search uses `LIKE`, fine for thousands of products, not millions (see DESIGN.md, question 4).
- No batching of several products per prompt and no shared rate limiter (see DESIGN.md, question 3).
- The pipeline does not read `Retry-After` from a 429 response; it just retries with backoff.
- No automatic "needs review" flag (see DESIGN.md, question 5).
- Metrics are not stored in the database.

## How I used AI tools

I used an AI assistant (Claude) to help write and review this code. I read the code, ran the tests and the app, and can explain the concurrency and caching parts (`pipeline.py`) in the interview.
