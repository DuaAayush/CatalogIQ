import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from api import Api
from config import Settings
from database import Database
from helpers import FakeLLM
from pipeline import Pipeline
from server import make_server


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        settings = Settings(db_path=os.path.join(cls.folder, "api.db"), backoff_ms=1)
        database = Database(settings.db_path)
        database.create_tables()
        cls.pipeline = Pipeline(settings, database, llm=FakeLLM(bad_word="BROKEN"))
        cls.server = make_server(Api(cls.pipeline, database, settings), "127.0.0.1", 0)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.pipeline.stop()
        shutil.rmtree(cls.folder, ignore_errors=True)

    def call(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode())

    def upload_and_wait(self, products):
        status, job = self.call("POST", "/api/jobs", {"products": products})
        self.assertEqual(status, 202)
        for _ in range(300):
            _, job = self.call("GET", "/api/jobs/" + job["id"])
            if job["status"] == "completed":
                return job
            time.sleep(0.02)
        self.fail("job poori nahi hui")

    # ------------------------------------------------------------ chhote endpoints
    def test_health(self):
        status, body = self.call("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok", "llm_provider": "mock", "llm_concurrency": 5})

    def test_metrics_are_integers(self):
        status, body = self.call("GET", "/api/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"llm_calls_total", "llm_errors_total", "max_concurrent_llm_calls"})
        self.assertTrue(all(isinstance(value, int) for value in body.values()))

    def test_home_page_is_served(self):
        with urllib.request.urlopen(self.base + "/") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers["Content-Type"])

    def test_unknown_route_gives_json_error(self):
        status, body = self.call("GET", "/api/nothing")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    # ------------------------------------------------------------ jobs
    def test_job_validation_errors(self):
        self.assertEqual(self.call("POST", "/api/jobs", {"products": []})[0], 400)
        self.assertEqual(self.call("POST", "/api/jobs", {"products": [{"raw_title": "x"}]})[0], 400)
        self.assertEqual(self.call("POST", "/api/jobs", {"products": [{"sku": "1"}]})[0], 400)
        self.assertEqual(self.call("POST", "/api/jobs", {"products": [{"sku": "1", "raw_title": "  "}]})[0], 400)
        self.assertEqual(self.call("POST", "/api/jobs", raw=b"not json")[0], 400)
        status, body = self.call("POST", "/api/jobs", {"products": []})
        self.assertIn("error", body)

    def test_unknown_job_is_404(self):
        status, body = self.call("GET", "/api/jobs/j_nope")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_job_flow_and_job_object_shape(self):
        status, job = self.call("POST", "/api/jobs", {"products": [
            {"sku": "J1", "raw_title": " AMUL butter 500G", "raw_description": "pck of 2"},
            {"sku": "J2", "raw_title": "amul BUTTER 500g", "raw_description": "PCK of 2"},
        ]})
        self.assertEqual(status, 202)
        self.assertEqual(set(job), {"id", "status", "total", "done", "failed", "cache_hits",
                                    "created_at", "started_at", "finished_at"})
        self.assertEqual(job["total"], 2)
        for _ in range(300):
            _, job = self.call("GET", "/api/jobs/" + job["id"])
            if job["status"] == "completed":
                break
            time.sleep(0.02)
        self.assertEqual(job["done"], 2)
        self.assertIsNotNone(job["finished_at"])

        _, product = self.call("GET", "/api/products/J1")
        self.assertEqual(set(product), {"sku", "raw_title", "raw_description", "clean_title", "category",
                                        "brand", "tags", "status", "error"})
        self.assertEqual(product["raw_title"], " AMUL butter 500G")     # raw waisa hi rehta hai
        self.assertEqual(product["status"], "enriched")

    def test_failed_product_has_error_message(self):
        self.upload_and_wait([{"sku": "F1", "raw_title": "BROKEN thing"}])
        _, product = self.call("GET", "/api/products/F1")
        self.assertEqual(product["status"], "failed")
        self.assertTrue(product["error"])

    def test_same_sku_updates_product(self):
        self.upload_and_wait([{"sku": "U1", "raw_title": "first title"}])
        self.upload_and_wait([{"sku": "U1", "raw_title": "second title"}])
        _, product = self.call("GET", "/api/products/U1")
        self.assertEqual(product["raw_title"], "second title")

    # ------------------------------------------------------------ products
    def test_list_pagination_sorting_search_and_filter(self):
        self.upload_and_wait([{"sku": "P%02d" % i, "raw_title": "Pagination Thing %d" % i} for i in range(25)])

        status, body = self.call("GET", "/api/products?q=pagination+thing&page_size=10")
        self.assertEqual(status, 200)
        self.assertEqual((body["page"], body["page_size"], body["total"]), (1, 10, 25))
        skus = [item["sku"] for item in body["items"]]
        self.assertEqual(skus, sorted(skus))

        _, page3 = self.call("GET", "/api/products?q=PAGINATION&page=3&page_size=10")
        self.assertEqual(len(page3["items"]), 5)                       # case-insensitive search

        _, none = self.call("GET", "/api/products?q=pagination&category=Fashion")
        self.assertEqual(none["total"], 0)
        _, some = self.call("GET", "/api/products?q=pagination&category=Groceries")
        self.assertEqual(some["total"], 25)

        _, big = self.call("GET", "/api/products?page_size=1000")
        self.assertEqual(big["page_size"], 100)                        # maximum 100

    def test_bad_page_numbers_give_400(self):
        self.assertEqual(self.call("GET", "/api/products?page=abc")[0], 400)
        self.assertEqual(self.call("GET", "/api/products?page_size=xyz")[0], 400)

    def test_unknown_product_is_404(self):
        self.assertEqual(self.call("GET", "/api/products/NOPE")[0], 404)
        self.assertEqual(self.call("PATCH", "/api/products/NOPE", {"clean_title": "x"})[0], 404)

    def test_patch_approves_product(self):
        self.upload_and_wait([{"sku": "A1", "raw_title": "patch me"}])
        status, product = self.call("PATCH", "/api/products/A1",
                                    {"clean_title": "  Better Title ", "category": "Fashion", "tags": ["One", "two"]})
        self.assertEqual(status, 200)
        self.assertEqual(product["status"], "approved")
        self.assertEqual(product["clean_title"], "Better Title")
        self.assertEqual(product["category"], "Fashion")
        self.assertEqual(product["tags"], ["one", "two"])

    def test_patch_validation(self):
        self.upload_and_wait([{"sku": "A2", "raw_title": "patch me too"}])
        self.assertEqual(self.call("PATCH", "/api/products/A2", {"category": "Music"})[0], 400)
        self.assertEqual(self.call("PATCH", "/api/products/A2", {"clean_title": "   "})[0], 400)
        self.assertEqual(self.call("PATCH", "/api/products/A2", {})[0], 400)
        status, body = self.call("PATCH", "/api/products/A2", {"category": "Music"})
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
