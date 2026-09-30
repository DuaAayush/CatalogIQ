"""
Tests ke liye common cheezein: nakli LLM (FakeLLM), aur pipeline banane ka aasan tarika.
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from config import Settings
from database import Database
from pipeline import Pipeline

GOOD_REPLY = json.dumps({"clean_title": "Test Product", "category": "Groceries", "brand": None, "tags": ["test"]})


class FakeLLM:
    """Test wala LLM. Ye khud ginta hai ki kitni calls hui aur ek saath kitni chal rahi thi.

    delay       : har call kitni der lega (seconds)
    replies     : list. Pehli call ko pehla jawab, doosri ko doosra... (Exception ho to wo fail hoga)
    bad_word    : title me ye word ho to hamesha fail
    """

    def __init__(self, delay=0.0, replies=None, bad_word=None):
        self.delay = delay
        self.replies = replies
        self.bad_word = bad_word
        self.lock = threading.Lock()
        self.calls = 0
        self.running = 0
        self.max_running = 0

    def enrich(self, raw_title, raw_description):
        with self.lock:
            self.calls += 1
            call_number = self.calls
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            time.sleep(self.delay)
            if self.bad_word and self.bad_word in raw_title:
                raise RuntimeError("always failing for " + raw_title)
            reply = GOOD_REPLY
            if self.replies is not None and call_number <= len(self.replies):
                reply = self.replies[call_number - 1]
            if isinstance(reply, Exception):
                raise reply
            return reply
        finally:
            with self.lock:
                self.running -= 1


def make_products(count, prefix="Item"):
    """count jitne alag-alag products."""
    return [{"sku": "S%03d" % i, "raw_title": "%s number %d" % (prefix, i), "raw_description": None}
            for i in range(count)]


class PipelineTestCase(unittest.TestCase):
    """Har test ko naya temporary database milta hai."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.pipelines = []

    def tearDown(self):
        for pipeline in self.pipelines:
            pipeline.stop()
        shutil.rmtree(self.folder, ignore_errors=True)

    def make_pipeline(self, llm, concurrency=5, retries=3, backoff_ms=1):
        settings = Settings(llm_concurrency=concurrency, retries=retries, backoff_ms=backoff_ms,
                            db_path=os.path.join(self.folder, "test.db"))
        database = Database(settings.db_path)
        database.create_tables()
        pipeline = Pipeline(settings, database, llm=llm)
        self.pipelines.append(pipeline)
        return pipeline

    def wait_for_job(self, pipeline, job_id, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            job = pipeline.db.get_job(job_id)
            if job["status"] == "completed":
                return job
            time.sleep(0.02)
        self.fail("job poori nahi hui: %s" % pipeline.db.get_job(job_id))
