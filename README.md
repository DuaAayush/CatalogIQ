# CatalogIQ

Sellers upload product listings like `AMUL butter 500G pck of 2`. CatalogIQ uses an LLM to turn each one into a clean catalogue entry: a tidy title, a category, a brand and a few tags. You can then search, filter, review and approve the results in a web page.

The interesting part is not the LLM call itself. It is everything around it: running many items at once without overloading the LLM, retrying when a call fails, not paying twice for the same product, and surviving a server restart.

It uses only the Python standard library (`http.server`, `threading`, `sqlite3`) and a plain HTML/CSS/JS page, so there is nothing to install.

## Quick start

You need Python 3.9 or newer.

```bash
git clone https://github.com/DuaAayush/CatalogIQ.git
cd catalogiq
python3 server.py
```

On Windows use `python` instead of `python3`.

Open http://localhost:8000, pick `data/sample_products.csv` and press Upload. The default mode is "mock", which uses a fake LLM, so it works without internet or any key.

## Running with a real LLM

I used **Groq (free tier)** with the model `openai/gpt-oss-20b`. Ollama also works.

Groq:

1. Make a free account at https://console.groq.com and create an API key.
2. Copy `.env.example` to a new file called `.env`.
3. In `.env`, set `LLM_PROVIDER=groq` and `GROQ_API_KEY=` followed by your key.
4. Run `python3 server.py`. The first line printed should say `LLM_PROVIDER=groq`.

The key only lives in your `.env` file (or an environment variable). `.env` is in `.gitignore`, so it is never committed.

Ollama (runs on your own computer, no key):

1. Install Ollama and run `ollama pull llama3.2:3b`.
2. Run `LLM_PROVIDER=ollama python3 server.py`.

Two problems I ran into that might happen to you:

- **HTTP 404 from Groq.** The model name is probably retired. Groq retires models from time to time. Check their model list and set `GROQ_MODEL` in `.env`.
- **CERTIFICATE_VERIFY_FAILED on a Mac.** Python from python.org does not ship with certificates. Run `python3 -m pip install certifi`, then run `python3 -c "import certifi; print(certifi.where())"` and put the path it prints in `.env` as `SSL_CERT_FILE=<that path>`.

## Settings

Set these as environment variables or in `.env`.

| Name | Default | What it does |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock`, `groq` or `ollama` |
| `LLM_CONCURRENCY` | `5` | Most LLM calls allowed at the same time, across all jobs |
| `MOCK_LATENCY_MS` | `200` | How long a mock call takes |
| `MOCK_FAILURE_RATE` | `0.1` | Chance that a mock call fails |
| `BACKOFF_MS` | `200` | Wait before the first retry. It doubles each time |
| `DB_PATH` | `catalogiq.db` | The SQLite file. Data survives restarts |
| `PORT` | `8000` | Server port |

Example: `LLM_CONCURRENCY=2 MOCK_LATENCY_MS=500 python3 server.py`

## Running the tests

```bash
python3 -m unittest discover tests -v
```

There are 30 tests. They use a fake LLM, so they need no internet. They check the concurrency limit (inside one job and across jobs), retries and backoff timing, validation, duplicate handling (including duplicates that arrive at the same moment), crash recovery, and the API (status codes, error format, paging, search, PATCH).

## Where things are

```
server.py      starts the web server and serves the page
api.py         the API routes and input checks
pipeline.py    the core: concurrency limit, retries, validation, cache, dedupe, metrics
llm.py         the mock, Groq and Ollama providers, plus the prompt
database.py    SQLite tables and all SQL
config.py      reads settings
static/        the web page (index.html, style.css, script.js)
data/          sample_products.csv (210 listings, with duplicates)
tests/         unit tests
DESIGN.md      design write-up with a diagram
```

If you only read one file, read `pipeline.py`.

## API

| Method | Path | What it does |
|---|---|---|
| GET | `/api/health` | Status, provider and concurrency limit |
| GET | `/api/metrics` | `llm_calls_total`, `llm_errors_total`, `max_concurrent_llm_calls` |
| POST | `/api/jobs` | Start a job with `{"products": [...]}`. Returns 202 straight away |
| GET | `/api/jobs/<id>` | Live progress of a job |
| GET | `/api/products` | List products. Options: `page`, `page_size` (max 100), `category`, `q` |
| GET | `/api/products/<sku>` | One product |
| PATCH | `/api/products/<sku>` | Edit `clean_title`, `category` or `tags`. Marks it `approved` |

Every error looks like `{"error": "message"}`.

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

The reply is checked by `check_llm_output()` in `pipeline.py`. If it is not valid JSON, or the category is not in the list, that attempt counts as failed and is retried.

## Assumptions I made

- A job's `done` count includes failed products. That way the progress bar reaches 100% and the job is `completed` when `done` equals `total`. `failed` says how many of those failed.
- If the same SKU appears twice in one upload, the last one wins. Uploading a SKU that already exists overwrites it, even if it was approved.
- A product shows up in the catalogue only after it has been processed.
- A job moves from `queued` to `running` when its first item starts.
- If the LLM returns more than 5 tags or uppercase tags, I fix them (cut to 5, lowercase) instead of failing the item.
- If several identical products are processed together and the LLM fails 4 times, they all get that same error.
- Metrics are kept in memory, so they reset when the server restarts.
- Times are in UTC.

## What is not finished

- The cache key does not include the provider name. So an answer from the mock LLM will be reused later when running with Groq. If you switch providers, delete `catalogiq.db` first.
- No login or authentication.
- Search uses `LIKE`, which is fine for thousands of products but not millions (see DESIGN.md, question 4).
- No batching of several products in one prompt, and no shared rate limiter (see DESIGN.md, question 3).
- On a 429 (rate limit) response I just retry with backoff. I do not read the `Retry-After` header.
- No automatic "needs review" flag (see DESIGN.md, question 5).

## How I used AI tools

I used Claude to help write and debug this project. I ran the app and the tests myself, tested it with Groq, and went through `pipeline.py` so I can explain the concurrency and caching code.
