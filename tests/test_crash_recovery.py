from helpers import FakeLLM, PipelineTestCase, make_products


class CrashRecoveryTests(PipelineTestCase):
    def test_unfinished_job_resumes_and_finished_work_is_not_repeated(self):
        # Pehla "server": job banata hai, 3 items karta hai, phir "crash".
        first_llm = FakeLLM()
        first = self.make_pipeline(first_llm)
        first.db.add_job("j_crash", make_products(6))
        item_ids = first.db.get_item_ids("j_crash")
        for item_id in item_ids[:3]:
            first.process_item("j_crash", item_id)
        self.assertEqual(first_llm.calls, 3)
        first.stop()

        # Dusra "server" (restart): usi database ko dekhta hai.
        second_llm = FakeLLM()
        second = self.make_pipeline(second_llm)
        resumed = second.start_unfinished_jobs()
        job = self.wait_for_job(second, "j_crash")

        self.assertEqual(resumed, 3)                  # sirf bache hue 3
        self.assertEqual(second_llm.calls, 3)         # pehle wale 3 dobara nahi
        self.assertEqual(job["done"], 6)
        self.assertEqual(job["total"], 6)

    def test_completed_jobs_are_left_alone(self):
        llm = FakeLLM()
        pipeline = self.make_pipeline(llm)
        job = pipeline.submit_job(make_products(3))
        self.wait_for_job(pipeline, job["id"])
        self.assertEqual(pipeline.start_unfinished_jobs(), 0)
