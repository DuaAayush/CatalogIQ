import time

from helpers import FakeLLM, GOOD_REPLY, PipelineTestCase, make_products


class RetryTests(PipelineTestCase):
    def test_succeeds_after_three_failures(self):
        llm = FakeLLM(replies=[RuntimeError("x")] * 3 + [GOOD_REPLY])
        pipeline = self.make_pipeline(llm)
        job = pipeline.submit_job(make_products(1))
        self.wait_for_job(pipeline, job["id"])

        self.assertEqual(pipeline.db.get_product("S000")["status"], "enriched")
        metrics = pipeline.get_metrics()
        self.assertEqual(metrics["llm_calls_total"], 4)     # har attempt count hota hai
        self.assertEqual(metrics["llm_errors_total"], 3)

    def test_gives_up_after_four_attempts_and_job_continues(self):
        llm = FakeLLM(bad_word="BAD")
        pipeline = self.make_pipeline(llm)
        products = make_products(4) + [{"sku": "BAD1", "raw_title": "BAD product", "raw_description": None}]
        job = pipeline.submit_job(products)
        finished = self.wait_for_job(pipeline, job["id"])

        bad = pipeline.db.get_product("BAD1")
        self.assertEqual(bad["status"], "failed")
        self.assertIn("4 attempts", bad["error"])
        self.assertEqual(finished["failed"], 1)
        self.assertEqual(finished["done"], 5)                                    # baaki job chalti rahi
        self.assertEqual(pipeline.db.get_product("S000")["status"], "enriched")
        self.assertEqual(pipeline.get_metrics()["llm_errors_total"], 4)

    def test_backoff_is_exponential(self):
        llm = FakeLLM(replies=[RuntimeError("x")] * 3 + [GOOD_REPLY])
        pipeline = self.make_pipeline(llm, backoff_ms=100)                        # 100 + 200 + 400 = 700ms
        start = time.time()
        job = pipeline.submit_job(make_products(1))
        self.wait_for_job(pipeline, job["id"])
        self.assertGreaterEqual(time.time() - start, 0.65)

    def test_invalid_json_and_bad_category_count_as_failed_attempts(self):
        wrong_category = '{"clean_title": "X", "category": "Music", "brand": null, "tags": []}'
        llm = FakeLLM(replies=["this is not json", wrong_category, GOOD_REPLY])
        pipeline = self.make_pipeline(llm)
        job = pipeline.submit_job(make_products(1))
        self.wait_for_job(pipeline, job["id"])

        self.assertEqual(pipeline.db.get_product("S000")["status"], "enriched")
        metrics = pipeline.get_metrics()
        self.assertEqual(metrics["llm_calls_total"], 3)
        self.assertEqual(metrics["llm_errors_total"], 2)

    def test_tags_are_lowercased_and_limited_to_five(self):
        reply = '{"clean_title": "X", "category": "Other", "brand": null, "tags": ["A","B","c","d","e","f","g"]}'
        pipeline = self.make_pipeline(FakeLLM(replies=[reply]))
        job = pipeline.submit_job(make_products(1))
        self.wait_for_job(pipeline, job["id"])
        self.assertEqual(pipeline.db.get_product("S000")["tags"], ["a", "b", "c", "d", "e"])
