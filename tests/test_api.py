"""HTTP API: every active endpoint, validation, safety (traversal, CSRF header, folder roots), limits."""
from tests import helpers

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("ATS_USE_SEMANTIC_BULK", "0")  # keep API tests fast and model-independent

from fastapi.testclient import TestClient

from ats import config
from ats.server import app, safe_display_name
from tests.test_bulk import IRRELEVANT, PARTIAL, PERFECT, STRONG

HEADERS = {"X-ATS-Client": "tests"}
JOB = {"title": "Senior Backend Engineer", "description": "Python backend REST APIs on Kubernetes",
       "required_skills": "python,sql,docker,kubernetes,fastapi", "preferred_skills": "redis,linux",
       "min_experience_years": "3", "required_education": "bachelor", "top_n": "3"}


def upload(name, text):
    return ("resumes", (name, text.encode("utf-8"), "text/plain"))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def post(self, url, data=None, files=None, headers=HEADERS):
        return self.client.post(url, data=data, files=files, headers=headers)

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        self.assertIn("ocr_available", r.json())

    def test_bulk_happy_path_and_response_shape(self):
        r = self.post("/api/bulk", JOB, [upload("a.txt", PERFECT), upload("b.txt", STRONG), upload("c.txt", PARTIAL),
                                         upload("d.txt", IRRELEVANT)])
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["total_submitted"], body["total_processed"], body["total_filtered"]), (4, 3, 1))
        self.assertEqual([c["file_name"] for c in body["top_candidates"]], ["a.txt", "b.txt", "c.txt"])
        self.assertEqual([c["rank"] for c in body["top_candidates"]], [1, 2, 3])
        for row in body["all_results"]:
            self.assertNotIn("parsed_text", row)
            self.assertNotIn("embedding", row)
        self.assertNotIn("FastAPI microservices", r.text)  # no resume text in responses
        self.assertIn("execution_summary", body)

    def test_regression_duplicate_upload_names_all_processed(self):
        r = self.post("/api/bulk", JOB, [upload("resume.txt", PERFECT), upload("resume.txt", STRONG),
                                         upload("resume.txt", PARTIAL)])
        body = r.json()
        self.assertEqual(body["total_submitted"], 3)
        self.assertEqual(sorted(x["file_path"] for x in body["all_results"]),
                         ["resume.txt", "resume.txt [2]", "resume.txt [3]"])

    def test_regression_path_traversal_filenames_are_neutralized(self):
        outside = Path(tempfile.gettempdir()) / "ats_traversal_probe.txt"
        outside.unlink(missing_ok=True)
        r = self.post("/api/bulk", JOB, [upload("../../ats_traversal_probe.txt", PERFECT),
                                         upload("C:\\Windows\\ats_probe.txt", STRONG)])
        self.assertEqual(r.status_code, 200)
        self.assertFalse(outside.exists())
        self.assertEqual(sorted(x["file_name"] for x in r.json()["all_results"]),
                         ["ats_probe.txt", "ats_traversal_probe.txt"])
        self.assertEqual(safe_display_name("..\\..\\x/../evil.pdf"), "evil.pdf")
        self.assertEqual(safe_display_name(None), "unnamed")

    def test_unsupported_and_malformed_files_reported(self):
        r = self.post("/api/bulk", JOB, [upload("a.txt", PERFECT), ("resumes", ("x.png", b"\x89PNG", "image/png")),
                                         ("resumes", ("bad.pdf", b"%PDF-1.4 junk", "application/pdf"))])
        body = r.json()
        self.assertEqual(body["total_failed"], 2)
        self.assertEqual(body["summary_stats"]["failure_reasons"], {"unsupported_file_type": 1, "corrupt_file": 1})

    def test_validation_errors(self):
        files = [upload("a.txt", PERFECT)]
        for field, value in (("min_experience_years", "-2"), ("min_experience_years", "lots"),
                             ("required_education", "kindergarten"), ("top_n", "0"), ("top_n", "x")):
            r = self.post("/api/bulk", {**JOB, field: value}, files)
            self.assertEqual(r.status_code, 422, (field, value, r.text))
        empty_job = {k: "" for k in ("title", "description", "required_skills", "preferred_skills")}
        self.assertEqual(self.post("/api/bulk", {**JOB, **empty_job}, files).status_code, 422)
        self.assertEqual(self.post("/api/bulk", JOB, None).status_code, 422)  # no files

    def test_file_size_limit(self):
        with mock.patch.object(config, "MAX_FILE_SIZE_MB", 0):
            r = self.post("/api/bulk", JOB, [upload("a.txt", PERFECT)])
        body = r.json()
        self.assertEqual(body["total_failed"], 1)
        self.assertTrue(body["all_results"][0]["error_message"].startswith("file_too_large"))

    def test_csrf_header_required(self):
        r = self.post("/api/bulk", JOB, [upload("a.txt", PERFECT)], headers={})
        self.assertEqual(r.status_code, 403)

    def test_single(self):
        r = self.post("/api/single", {k: v for k, v in JOB.items() if k != "top_n"},
                      [("resume", ("cv.txt", PERFECT.encode(), "text/plain"))])
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "processed")
        self.assertEqual(body["name"], "Arjun Mehta")
        self.assertTrue(0 <= body["scores"]["final"] <= 100)
        self.assertIsNone(body["text_preview"])  # no resume text unless debug is requested
        self.assertNotIn("FastAPI microservices", r.text)
        dbg = self.post("/api/single", {**{k: v for k, v in JOB.items() if k != "top_n"}, "debug": "true"},
                        [("resume", ("cv.txt", PERFECT.encode(), "text/plain"))])
        self.assertTrue(dbg.json()["text_preview"])

    def test_file_count_limit(self):
        with mock.patch.object(config, "MAX_FILES_PER_REQUEST", 2):
            r = self.post("/api/bulk", JOB, [upload(f"{i}.txt", PERFECT) for i in range(3)])
        self.assertEqual(r.status_code, 413)
        self.assertIn("Too many files", r.json()["detail"])

    def test_request_size_limit(self):
        with mock.patch.object(config, "MAX_REQUEST_SIZE_MB", 0):
            r = self.post("/api/bulk", JOB, [upload("a.txt", PERFECT)])
        self.assertEqual(r.status_code, 413)

    def test_errors_do_not_leak_resume_text(self):
        r = self.post("/api/bulk", JOB, [("resumes", ("bad.pdf", b"%PDF-1.4 SECRET-CANDIDATE-TEXT", "application/pdf"))])
        self.assertNotIn("SECRET-CANDIDATE-TEXT", r.text)

    def test_single_unsupported(self):
        r = self.post("/api/single", JOB, [("resume", ("cv.png", b"\x89PNG", "image/png"))])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "failed")

    def test_regression_removed_crashing_endpoint(self):
        r = self.post("/api/bulk-parallel", JOB, [upload("a.txt", PERFECT)])
        self.assertIn(r.status_code, (404, 405))

    def test_folder_endpoint_disabled_by_default(self):
        with mock.patch.object(config, "ALLOWED_FOLDER_ROOTS", []):
            r = self.post("/api/bulk-parallel-folder", {**JOB, "folder_path": str(Path.home())})
        self.assertEqual(r.status_code, 403)

    def test_folder_endpoint_restricted_to_roots(self):
        root = Path(tempfile.mkdtemp(prefix="ats_root_"))
        (root / "jobs").mkdir()
        (root / "jobs" / "a.txt").write_text(PERFECT, encoding="utf-8")
        other = Path(tempfile.mkdtemp(prefix="ats_other_"))
        with mock.patch.object(config, "ALLOWED_FOLDER_ROOTS", [str(root)]):
            ok = self.post("/api/bulk-parallel-folder", {**JOB, "folder_path": str(root / "jobs")})
            outside = self.post("/api/bulk-parallel-folder", {**JOB, "folder_path": str(other)})
            sneaky = self.post("/api/bulk-parallel-folder", {**JOB, "folder_path": str(root / "jobs" / ".." / "..")})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["total_processed"], 1)
        self.assertEqual(outside.status_code, 403)
        self.assertEqual(sneaky.status_code, 403)

    def test_progress_endpoint(self):
        self.post("/api/bulk", {**JOB, "request_id": "req-123"}, [upload("a.txt", PERFECT)])
        r = self.client.get("/api/progress/req-123")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["stage"], "done")
        self.assertEqual(self.client.get("/api/progress/nope").status_code, 404)


class FailureLogPrivacyTests(unittest.TestCase):
    """With ATS_SAVE_RESULTS=0 nothing on disk names the candidate; the API still reports every failure."""

    NAMES = ("priya", "sharma", "rahul", "verma", "anita", "rao_cv")

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def screen(self, save: bool):
        tmp = Path(tempfile.mkdtemp())
        files = [upload("Zoe_Good_Resume.txt", PERFECT),
                 ("resumes", ("Priya_Sharma_Resume.pdf", b"%PDF-1.4 junk", "application/pdf")),
                 ("resumes", ("Rahul Verma CV.docx", b"", "application/octet-stream")),
                 ("resumes", ("Anita_Rao_CV.png", b"\x89PNG", "image/png"))]
        with mock.patch.object(config, "SAVE_RESULTS_JSON", save), mock.patch.object(config, "RUNS_DIR", tmp / "runs"), \
                mock.patch.object(config, "OUTPUT_DIR", tmp / "output"):
            (tmp / "runs").mkdir()
            r = self.client.post("/api/bulk", data=JOB, files=files, headers=HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        logs = list((tmp / "runs").glob("failed_files_*.jsonl"))
        self.assertEqual(len(logs), 1)
        return r.json(), [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()], tmp

    def test_saving_disabled_keeps_names_out_of_failure_log(self):
        body, rows, tmp = self.screen(save=False)
        failed = [x for x in body["all_results"] if x["status"] == "failed"]
        # the API / UI still report each failure by its file name and reason
        self.assertEqual(sorted(x["file_name"] for x in failed),
                         ["Anita_Rao_CV.png", "Priya_Sharma_Resume.pdf", "Rahul Verma CV.docx"])
        self.assertTrue(all(x["error_message"] for x in failed))
        # the persisted log: no names, but reason, timings, file type and an id matching the API row
        raw = json.dumps(rows).lower()
        for name in self.NAMES:
            self.assertNotIn(name, raw)
        self.assertNotIn("file_path", rows[0])
        self.assertEqual(sorted(r["candidate_id"] for r in rows), sorted(x["candidate_id"] for x in failed))
        by_id = {x["candidate_id"]: x for x in failed}
        for row in rows:
            self.assertEqual(row["error_message"], by_id[row["candidate_id"]]["error_message"])
            self.assertIn("parse", row["stage_timings"])
        self.assertEqual(sorted(r["file_type"] for r in rows), [".docx", ".pdf", ".png"])
        self.assertFalse((tmp / "output").exists() and any((tmp / "output").iterdir()))  # no results file

    def test_saving_enabled_keeps_file_names(self):
        _, rows, tmp = self.screen(save=True)
        self.assertEqual(sorted(r["file_path"] for r in rows),
                         ["Anita_Rao_CV.png", "Priya_Sharma_Resume.pdf", "Rahul Verma CV.docx"])
        self.assertEqual(len(list((tmp / "output").glob("results_*.json"))), 1)

    def test_file_name_is_removed_from_error_text(self):
        from ats.api import _without_file_name
        msg = "parse_error: FileNotFoundError: No such file: 'C:/in/Priya_Sharma_Resume.pdf' (priya_sharma_resume)"
        cleaned = _without_file_name(msg, "Priya_Sharma_Resume.pdf")
        self.assertNotIn("priya", cleaned.lower())
        self.assertTrue(cleaned.startswith("parse_error: FileNotFoundError"))


if __name__ == "__main__":
    unittest.main()
