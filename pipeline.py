"""
pipeline.py  ->  ASLI DIMAAG. Concurrency limit, retry, validation, cache, dedupe sab yahin hai.

Ek item ka safar (process_item):

   1. Content ki "key" banao (lowercase + extra space hatao)
   2. get_result():
        - Kya isi content ka kaam abhi chal raha hai?  -> haan: uska result ka wait karo (cache hit)
        - Kya database cache me pehle se hai?           -> haan: wahi do (cache hit)
        - Nahi to main "leader" hu: LLM bulao (retry ke saath), result cache me save karo
   3. Product + job ka progress database me save karo

Concurrency ka funda:
   - Bahut saare worker threads hain (config.worker_threads), jo items uthate hain.
   - Lekin LLM ke darwaze par ek Semaphore(N) khada hai. Andar sirf N log ek saath ja sakte hain.
   - Semaphore ek hi hai, saari jobs ke liye  ->  limit "across all jobs" lagti hai.
"""
import hashlib
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from config import CATEGORIES
from llm import get_llm

log = logging.getLogger("catalogiq")


def make_content_key(raw_title, raw_description):
    """Same content = same key. Lowercase karo, extra spaces hatao, phir hash."""
    text = raw_title + " " + (raw_description or "")
    text = " ".join(text.lower().split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_llm_output(text):
    """LLM ka jawab theek hai ya nahi? Galat ho to error (ValueError) uthao.
    Theek ho to saaf dictionary wapas do."""
    data = json.loads(text)                       # JSON nahi hai -> yahin error
    if not isinstance(data, dict):
        raise ValueError("LLM ne JSON object nahi diya")

    title = data.get("clean_title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("clean_title missing ya khaali hai")

    if data.get("category") not in CATEGORIES:
        raise ValueError("category galat hai: " + repr(data.get("category")))

    brand = data.get("brand")
    if brand is not None and not isinstance(brand, str):
        raise ValueError("brand string ya null hona chahiye")
    brand = brand.strip() if brand else None

    tags = data.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("tags strings ki list honi chahiye")
    clean_tags = []
    for tag in tags:
        tag = tag.strip().lower()
        if tag and tag not in clean_tags:
            clean_tags.append(tag)

    return {
        "clean_title": title.strip(),
        "category": data["category"],
        "brand": brand or None,
        "tags": clean_tags[:5],          # maximum 5 tags
    }


class WaitingRoom:
    """Jab ek content par LLM ka kaam chal raha ho, baaki log yahan intezaar karte hain."""

    def __init__(self):
        self.done_event = threading.Event()
        self.result = None
        self.error = None


class Pipeline:
    def __init__(self, settings, database, llm=None):
        self.settings = settings
        self.db = database
        self.llm = llm or get_llm(settings)

        # LLM ka darwaza: ek time par sirf llm_concurrency log andar.
        self.gate = threading.Semaphore(settings.llm_concurrency)

        # Kaam karne wale threads (saari jobs ke liye ek hi pool)
        self.pool = ThreadPoolExecutor(max_workers=settings.worker_threads, thread_name_prefix="worker")

        # Metrics (counters). Multiple threads badhate hain, isliye lock.
        self.metrics_lock = threading.Lock()
        self.calls_total = 0
        self.errors_total = 0
        self.running_now = 0
        self.max_running = 0

        # "Ye content abhi process ho raha hai" ki list
        self.flight_lock = threading.Lock()
        self.in_flight = {}

        # Kaun si jobs 'running' mark ho chuki hain (baar baar DB na chhune ke liye)
        self.started_jobs = set()
        self.started_lock = threading.Lock()

    # ------------------------------------------------------------ bahar se use hone wale functions
    def submit_job(self, products):
        """Job banao, items queue me daalo, TURANT wapas aao. (Kaam background me hota hai)"""
        unique = {}
        for product in products:
            unique[product["sku"]] = product          # same SKU do baar ho to aakhri wala rakho
        job_id = "j_" + uuid.uuid4().hex[:8]
        self.db.add_job(job_id, list(unique.values()))
        for item_id in self.db.get_item_ids(job_id):
            self.pool.submit(self.run_item_safely, job_id, item_id)
        return self.db.get_job(job_id)

    def start_unfinished_jobs(self):
        """Server start hote hi: adhoori jobs ke pending items dobara queue me daalo.
        Jo items pehle ho chuke hain wo dobara nahi honge (unki state 'done' hai)."""
        pending = self.db.get_unfinished_items()
        for job_id, item_id in pending:
            self.pool.submit(self.run_item_safely, job_id, item_id)
        return len(pending)

    def get_metrics(self):
        with self.metrics_lock:
            return {
                "llm_calls_total": self.calls_total,
                "llm_errors_total": self.errors_total,
                "max_concurrent_llm_calls": self.max_running,
            }

    def stop(self):
        self.pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ ek item ka kaam
    def run_item_safely(self, job_id, item_id):
        """Thread ke andar koi bhi error aaye to thread mar na jaye, bas log ho jaye."""
        try:
            self.process_item(job_id, item_id)
        except Exception:
            log.exception("item %s (job %s) process nahi ho paya", item_id, job_id)

    def process_item(self, job_id, item_id):
        item = self.db.get_item(item_id)
        if item is None or item["state"] != "pending":
            return
        self.mark_job_running(job_id)

        try:
            key = make_content_key(item["raw_title"], item["raw_description"])
            result, cache_hit = self.get_result(item["raw_title"], item["raw_description"], key)
            error = None
        except Exception as err:                       # 4 attempts ke baad bhi fail
            result, cache_hit, error = None, False, str(err)

        self.db.finish_item(job_id, item, result, error, cache_hit)

    def mark_job_running(self, job_id):
        with self.started_lock:
            if job_id in self.started_jobs:
                return
            self.started_jobs.add(job_id)
        self.db.mark_job_running(job_id)

    # ------------------------------------------------------------ cache + dedupe
    def get_result(self, raw_title, raw_description, key):
        """Return: (result, cache_hit)"""
        with self.flight_lock:
            waiting = self.in_flight.get(key)
            if waiting is None:
                cached = self.db.get_cached(key)
                if cached is not None:
                    return cached, True                # database cache hit
                waiting = WaitingRoom()                # main leader hu
                self.in_flight[key] = waiting
                i_am_leader = True
            else:
                i_am_leader = False

        if not i_am_leader:
            waiting.done_event.wait()                  # leader ka intezaar
            if waiting.error is not None:
                raise waiting.error
            return waiting.result, True                # cache hit

        try:
            result = self.enrich_with_retry(raw_title, raw_description)
            self.db.save_cached(key, result)           # PEHLE cache me save
            waiting.result = result
            return result, False
        except Exception as err:
            waiting.error = err
            raise
        finally:
            with self.flight_lock:                     # PHIR "in flight" list se hatao
                self.in_flight.pop(key, None)
            waiting.done_event.set()                   # sab waiting walon ko jagao

    # ------------------------------------------------------------ retry + validation
    def enrich_with_retry(self, raw_title, raw_description):
        total_attempts = 1 + self.settings.retries     # 1 + 3 = 4
        last_error = None
        for attempt in range(total_attempts):
            try:
                text = self.call_llm_once(raw_title, raw_description)
                return check_llm_output(text)          # galat output = fail attempt
            except Exception as err:
                last_error = err
                with self.metrics_lock:
                    self.errors_total += 1
                if attempt < total_attempts - 1:
                    # 200ms, 400ms, 800ms ... (semaphore ke BAHAR sote hain, taaki jagah na ghere)
                    time.sleep(self.settings.backoff_ms / 1000 * (2 ** attempt))
        raise RuntimeError("failed after %d attempts: %s" % (total_attempts, last_error))

    def call_llm_once(self, raw_title, raw_description):
        """LLM ko EK baar bulao. Yahi wo jagah hai jahan concurrency limit lagti hai."""
        with self.gate:                                # <- sirf N log andar
            with self.metrics_lock:
                self.calls_total += 1
                self.running_now += 1
                self.max_running = max(self.max_running, self.running_now)
            try:
                return self.llm.enrich(raw_title, raw_description)
            finally:
                with self.metrics_lock:
                    self.running_now -= 1
