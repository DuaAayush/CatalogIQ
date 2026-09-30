from helpers import FakeLLM, PipelineTestCase, make_products


class ConcurrencyTests(PipelineTestCase):
    def test_never_more_than_limit_in_one_job(self):
        llm = FakeLLM(delay=0.05)
        pipeline = self.make_pipeline(llm, concurrency=3)
        job = pipeline.submit_job(make_products(30))
        self.wait_for_job(pipeline, job["id"])

        self.assertLessEqual(llm.max_running, 3)                    # limit tuti nahi
        self.assertGreaterEqual(llm.max_running, 2)                 # aur parallel bhi chala
        metrics = pipeline.get_metrics()
        self.assertLessEqual(metrics["max_concurrent_llm_calls"], 3)
        self.assertEqual(metrics["llm_calls_total"], 30)

    def test_limit_is_shared_across_jobs(self):
        llm = FakeLLM(delay=0.05)
        pipeline = self.make_pipeline(llm, concurrency=3)
        job_a = pipeline.submit_job(make_products(15, "Alpha"))
        job_b = pipeline.submit_job(make_products(15, "Beta"))      # dono ek saath
        self.wait_for_job(pipeline, job_a["id"])
        self.wait_for_job(pipeline, job_b["id"])

        self.assertLessEqual(llm.max_running, 3)
        self.assertLessEqual(pipeline.get_metrics()["max_concurrent_llm_calls"], 3)

    def test_limit_of_one_means_one_at_a_time(self):
        llm = FakeLLM(delay=0.02)
        pipeline = self.make_pipeline(llm, concurrency=1)
        job = pipeline.submit_job(make_products(8))
        self.wait_for_job(pipeline, job["id"])
        self.assertEqual(llm.max_running, 1)

    def test_submit_returns_quickly(self):
        import time
        pipeline = self.make_pipeline(FakeLLM(delay=0.1), concurrency=1)
        start = time.time()
        job = pipeline.submit_job(make_products(10))                # 10 x 0.1s = 1 sec ka kaam
        self.assertLess(time.time() - start, 0.5)                   # par submit turant wapas aa gaya
        self.assertIn(job["status"], ("queued", "running"))
        self.wait_for_job(pipeline, job["id"])
