"""
database.py  ->  SQLite se related saara kaam (saari SQL queries yahin hain).

4 tables hain:
  products          -> final saaf catalogue
  jobs              -> har upload ek job hai (progress yahan)
  job_items         -> job ke andar har product ki line (pending / done / failed)
  enrichment_cache  -> pehle se enrich ho chuka content (dobara LLM nahi bulana)

Har function apna alag connection kholta hai, isliye kai threads ek saath
safely use kar sakte hain (SQLite WAL mode + timeout).
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def now_text():
    """Abhi ka time, jaise 2026-10-01T09:30:00 (UTC)"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


TABLES = """
CREATE TABLE IF NOT EXISTS products (
    sku             TEXT PRIMARY KEY,
    raw_title       TEXT NOT NULL,
    raw_description TEXT,
    clean_title     TEXT,
    category        TEXT,
    brand           TEXT,
    tags            TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    total       INTEGER NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    cache_hits  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS job_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL,
    sku             TEXT NOT NULL,
    raw_title       TEXT NOT NULL,
    raw_description TEXT,
    state           TEXT NOT NULL DEFAULT 'pending',
    cache_hit       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_job_items_job ON job_items(job_id, state);

CREATE TABLE IF NOT EXISTS enrichment_cache (
    content_key TEXT PRIMARY KEY,
    result_json TEXT NOT NULL
);
"""

JOB_FIELDS = "id, status, total, done, failed, cache_hits, created_at, started_at, finished_at"


def product_to_dict(row):
    """Database row ko API wale product object me badlo."""
    return {
        "sku": row["sku"],
        "raw_title": row["raw_title"],
        "raw_description": row["raw_description"],
        "clean_title": row["clean_title"],
        "category": row["category"],
        "brand": row["brand"],
        "tags": json.loads(row["tags"] or "[]"),
        "status": row["status"],
        "error": row["error"],
    }


def escape_like(text):
    """Search me % aur _ ko normal character banane ke liye."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Database:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def use(self):
        """with db.use() as conn:  ...  -> kaam ke baad commit, error par rollback."""
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_tables(self):
        with self.use() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(TABLES)

    # ------------------------------------------------------------ jobs
    def add_job(self, job_id, products):
        """Nayi job + uske saare items ek saath save karo (ek transaction me)."""
        with self.use() as conn:
            conn.execute(
                "INSERT INTO jobs (id, status, total, created_at) VALUES (?, 'queued', ?, ?)",
                (job_id, len(products), now_text()),
            )
            conn.executemany(
                "INSERT INTO job_items (job_id, sku, raw_title, raw_description) VALUES (?, ?, ?, ?)",
                [(job_id, p["sku"], p["raw_title"], p["raw_description"]) for p in products],
            )

    def get_job(self, job_id):
        with self.use() as conn:
            row = conn.execute(f"SELECT {JOB_FIELDS} FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def get_item_ids(self, job_id):
        with self.use() as conn:
            rows = conn.execute("SELECT id FROM job_items WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
        return [row["id"] for row in rows]

    def get_item(self, item_id):
        with self.use() as conn:
            row = conn.execute("SELECT * FROM job_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def get_unfinished_items(self):
        """Server restart ke baad: kaun kaun se items abhi tak pending hain?
        Return: [(job_id, item_id), ...]"""
        with self.use() as conn:
            rows = conn.execute(
                "SELECT job_items.job_id, job_items.id FROM job_items "
                "JOIN jobs ON jobs.id = job_items.job_id "
                "WHERE job_items.state = 'pending' AND jobs.status != 'completed' "
                "ORDER BY jobs.created_at, job_items.id"
            ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def mark_job_running(self, job_id):
        with self.use() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'",
                (now_text(), job_id),
            )

    def finish_item(self, job_id, item, result, error, cache_hit):
        """Ek item ka kaam khatam. Ye 3 kaam ek saath (ek transaction me) karta hai:
           1) item ko done/failed mark karo
           2) product save karo
           3) job ke counters badhao (aur sab ho gaya to job completed karo)
        Ek saath hone ki wajah se crash ho jaye to bhi data adha-adhura nahi rehta."""
        state = "failed" if error else "done"
        with self.use() as conn:
            changed = conn.execute(
                "UPDATE job_items SET state = ?, cache_hit = ? WHERE id = ? AND state = 'pending'",
                (state, int(cache_hit), item["id"]),
            ).rowcount
            if changed == 0:
                return  # ye item pehle hi ho chuka tha, dobara mat ginna

            if error:
                values = (None, None, None, "[]", "failed", error)
            else:
                values = (result["clean_title"], result["category"], result["brand"],
                          json.dumps(result["tags"]), "enriched", None)
            conn.execute(
                "INSERT INTO products (sku, raw_title, raw_description, clean_title, category, brand, tags, status, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(sku) DO UPDATE SET raw_title=excluded.raw_title, raw_description=excluded.raw_description, "
                "clean_title=excluded.clean_title, category=excluded.category, brand=excluded.brand, "
                "tags=excluded.tags, status=excluded.status, error=excluded.error",
                (item["sku"], item["raw_title"], item["raw_description"]) + values,
            )
            conn.execute(
                "UPDATE jobs SET done = done + 1, failed = failed + ?, cache_hits = cache_hits + ? WHERE id = ?",
                (1 if error else 0, 1 if cache_hit else 0, job_id),
            )
            conn.execute(
                "UPDATE jobs SET status = 'completed', finished_at = ? "
                "WHERE id = ? AND done >= total AND status != 'completed'",
                (now_text(), job_id),
            )

    # ------------------------------------------------------------ cache
    def get_cached(self, content_key):
        with self.use() as conn:
            row = conn.execute(
                "SELECT result_json FROM enrichment_cache WHERE content_key = ?", (content_key,)
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def save_cached(self, content_key, result):
        with self.use() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO enrichment_cache (content_key, result_json) VALUES (?, ?)",
                (content_key, json.dumps(result)),
            )

    # ------------------------------------------------------------ products
    def get_product(self, sku):
        with self.use() as conn:
            row = conn.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
        return product_to_dict(row) if row else None

    def list_products(self, category, q, page, page_size):
        conditions, params = [], []
        if category:
            conditions.append("category = ?")
            params.append(category)
        if q:
            pattern = "%" + escape_like(q.lower()) + "%"
            conditions.append("(LOWER(clean_title) LIKE ? ESCAPE '\\' OR LOWER(raw_title) LIKE ? ESCAPE '\\')")
            params += [pattern, pattern]
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with self.use() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM products{where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM products{where} ORDER BY sku LIMIT ? OFFSET ?",
                params + [page_size, (page - 1) * page_size],
            ).fetchall()
        return [product_to_dict(row) for row in rows], total

    def update_product(self, sku, changes):
        """changes me clean_title / category / tags me se jo bhi ho. Status 'approved' ho jata hai."""
        parts, params = [], []
        for field in ("clean_title", "category"):
            if field in changes:
                parts.append(f"{field} = ?")
                params.append(changes[field])
        if "tags" in changes:
            parts.append("tags = ?")
            params.append(json.dumps(changes["tags"]))
        parts.append("status = 'approved'")
        parts.append("error = NULL")
        with self.use() as conn:
            conn.execute(f"UPDATE products SET {', '.join(parts)} WHERE sku = ?", params + [sku])
        return self.get_product(sku)
