from helpers import FakeLLM, PipelineTestCase, make_products
from pipeline import make_content_key


class DedupeTests(PipelineTestCase):
    def test_same_content_gives_same_key(self):
        a = make_content_key("AMUL butter 500G", "pck of 2")
        b = make_content_key("  amul   BUTTER 500g ", " PCK  of   2  ")
        self.assertEqual(a, b)
        self.assertNotEqual(a, make_content_key("Amul butter 100g", "pck of 2"))
        self.assertEqual(make_content_key("Milk", None), make_content_key("milk", ""))

    def test_duplicates_at_the_same_moment_call_llm_once(self):
        llm = FakeLLM(delay=0.3)      # lamba call, taaki baaki sab uske dauran aa jayein
        pipeline = self.make_pipeline(llm)
        variants = ["AMUL butter 500G", " amul  butter 500g", "Amul BUTTER   500g "]
        products = [{"sku": "D%02d" % i, "raw_title": variants[i % 3], "raw_description": "pck of 2"}
                    for i in range(10)]
        job = pipeline.submit_job(products)
        finished = self.wait_for_job(pipeline, job["id"])

        self.assertEqual(llm.calls, 1)                    # LLM sirf ek baar
        self.assertEqual(finished["cache_hits"], 9)       # baaki 9 cache hit
        self.assertEqual(finished["failed"], 0)
        titles = {pipeline.db.get_product("D%02d" % i)["clean_title"] for i in range(10)}
        self.assertEqual(len(titles), 1)

    def test_cache_survives_across_jobs(self):
        llm = FakeLLM()
        pipeline = self.make_pipeline(llm)
        first = pipeline.submit_job(make_products(5))
        self.wait_for_job(pipeline, first["id"])
        self.assertEqual(llm.calls, 5)

        second = pipeline.submit_job(make_products(5))           # wahi content, naya job
        finished = self.wait_for_job(pipeline, second["id"])
        self.assertEqual(llm.calls, 5)                            # LLM dobara nahi bulaya
        self.assertEqual(finished["cache_hits"], 5)

    def test_different_content_is_not_mixed_up(self):
        llm = FakeLLM()
        pipeline = self.make_pipeline(llm)
        job = pipeline.submit_job(make_products(6))
        self.wait_for_job(pipeline, job["id"])
        self.assertEqual(llm.calls, 6)

    def test_waiters_share_the_failure_of_the_leader(self):
        llm = FakeLLM(delay=0.05, bad_word="BAD")
        pipeline = self.make_pipeline(llm)
        products = [{"sku": "B%d" % i, "raw_title": "BAD thing", "raw_description": None} for i in range(5)]
        job = pipeline.submit_job(products)
        finished = self.wait_for_job(pipeline, job["id"])
        self.assertEqual(finished["failed"], 5)
        self.assertEqual(llm.calls, 4)                            # 4 attempts, 5 items ke liye bhi nahi badhe
