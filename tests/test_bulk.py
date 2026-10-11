"""Bulk pipeline: sizes 1/10/100, duplicates, malformed and unsupported files, top-N, ranking quality."""
from tests import helpers

import asyncio
import tempfile
import unittest
from pathlib import Path

from ats.api import build_job_profile, list_resume_files, screen_bulk, screen_files
from ats.orchestrator import get_progress, run_bulk

JOB = build_job_profile(
    title="Senior Backend Engineer",
    description="Backend engineer building Python REST APIs, containerized services on Kubernetes and SQL databases.",
    required_skills="python,sql,docker,kubernetes,fastapi",
    preferred_skills="redis,linux,terraform,kafka",
    min_experience_years=3,
    required_education="bachelor",
)

PERFECT = helpers.resume_text(
    "Arjun Mehta", "Python, SQL, Docker, Kubernetes, FastAPI, Redis, Linux, Terraform",
    ["Senior Backend Engineer, Acme Technologies Pvt Ltd  Jan 2018 - Present",
     "Built FastAPI microservices on Kubernetes with PostgreSQL and Redis"],
    "B.Tech Computer Science, Anna University, 2017",
    summary="Backend engineer with 8 years of experience building Python APIs.")
STRONG = helpers.resume_text(
    "Divya Nair", "Python, SQL, Docker, Linux",
    ["Software Engineer, Beta Solutions Ltd  Jan 2020 - Present"],
    "B.E. Electronics, VTU, 2019")
PARTIAL = helpers.resume_text(
    "Karthik Raj", "Python, Excel",
    ["Junior Developer, Gamma Ltd  Jun 2024 - Present"],
    "Diploma in Computer Engineering, 2023")
IRRELEVANT = helpers.resume_text(
    "Lakshmi Devi", "Tally ERP, Excel, GST filing",
    ["Accountant, Delta Traders Pvt Ltd  2015 - 2023"],
    "B.Com, Madras University, 2014")
MISSING_INFO = "Sanjay\nPython SQL Docker\n"


def run(coro):
    return asyncio.run(coro)


class BulkTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ats_bulk_"))

    def write(self, name, text):
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_single_file(self):
        self.write("a.txt", PERFECT)
        out = run(screen_bulk(str(self.dir), JOB, top_n=5))
        self.assertEqual((out.total_submitted, out.total_processed, out.total_failed), (1, 1, 0))
        self.assertEqual(out.top_candidates[0].name, "Arjun Mehta")
        self.assertEqual(out.top_candidates[0].rank, 1)

    def test_ranking_quality_controlled_candidates(self):
        for name, text in (("perfect.txt", PERFECT), ("strong.txt", STRONG), ("partial.txt", PARTIAL),
                           ("irrelevant.txt", IRRELEVANT), ("missing.txt", MISSING_INFO)):
            self.write(name, text)
        out = run(screen_bulk(str(self.dir), JOB, top_n=10))
        order = [r.file_name for r in out.top_candidates]
        self.assertEqual(order[:3], ["perfect.txt", "strong.txt", "partial.txt"], order)
        statuses = {r.file_name: r.status for r in out.all_results}
        self.assertEqual(statuses["irrelevant.txt"], "filtered")  # no required/preferred skill at all
        self.assertEqual(statuses["missing.txt"], "processed")
        missing = next(r for r in out.all_results if r.file_name == "missing.txt")
        self.assertLess(missing.scores.final, next(r for r in out.all_results if r.file_name == "strong.txt").scores.final)
        perfect = out.top_candidates[0]
        self.assertEqual(perfect.matched_skills, JOB.required_skills)
        self.assertEqual(perfect.education_level, "bachelor")

    def test_hundred_files_with_failures_and_unsupported(self):
        texts = [PERFECT, STRONG, PARTIAL, MISSING_INFO]
        for i in range(96):
            self.write(f"r{i:03d}.txt", texts[i % 4].replace("Arjun Mehta", f"Arjun Mehta {i}"))
        (self.dir / "broken.pdf").write_bytes(b"%PDF-1.4 not really")
        (self.dir / "empty.docx").write_bytes(b"")
        (self.dir / "photo.png").write_bytes(b"\x89PNG....")
        helpers.make_text_pdf(self.dir / "real.pdf", ["Asha", "Python SQL Docker developer"])
        out = run(screen_bulk(str(self.dir), JOB, top_n=10))
        self.assertEqual(out.total_submitted, 100)  # nothing silently dropped
        self.assertEqual(out.total_failed, 3)
        self.assertEqual(out.total_processed + out.total_filtered + out.total_failed, 100)
        reasons = {r.file_name: r.error_message.split(":")[0] for r in out.all_results if r.status == "failed"}
        self.assertEqual(reasons, {"broken.pdf": "corrupt_file", "empty.docx": "empty_file",
                                   "photo.png": "unsupported_file_type"})
        self.assertEqual(len(out.top_candidates), 10)
        scores = [r.scores.final for r in out.top_candidates]
        self.assertEqual(scores, sorted(scores, reverse=True))
        best_overall = max(r.scores.final for r in out.all_results if r.status == "processed")
        self.assertEqual(out.top_candidates[0].scores.final, best_overall)

    def test_duplicate_filenames_in_subfolders_all_survive(self):
        self.write("batch1/resume.txt", PERFECT)
        self.write("batch2/resume.txt", STRONG)
        self.write("batch3/resume.txt", PERFECT)  # identical content -> flagged as duplicate
        out = run(screen_bulk(str(self.dir), JOB, top_n=10))
        self.assertEqual(out.total_submitted, 3)
        paths = sorted(r.file_path for r in out.all_results)
        self.assertEqual(paths, ["batch1/resume.txt", "batch2/resume.txt", "batch3/resume.txt"])
        dup = next(r for r in out.all_results if r.file_path == "batch3/resume.txt")
        self.assertEqual(dup.duplicate_of, "batch1/resume.txt")
        self.assertEqual(out.summary_stats["duplicate_files"], 1)

    def test_os_junk_files_ignored(self):
        self.write("a.txt", PERFECT)
        (self.dir / "Thumbs.db").write_bytes(b"x")
        (self.dir / "~$lock.docx").write_bytes(b"x")
        self.assertEqual([d for _, d in list_resume_files(str(self.dir))], ["a.txt"])

    def test_no_resume_text_in_results(self):
        self.write("a.txt", PERFECT)
        out = run(screen_bulk(str(self.dir), JOB, top_n=5))
        r = out.all_results[0]
        self.assertFalse(hasattr(r, "parsed_text"))
        self.assertFalse(hasattr(r, "embedding"))
        saved = Path(out.summary_stats["results_path"]).read_text(encoding="utf-8")
        self.assertNotIn("FastAPI microservices on Kubernetes", saved)

    def test_progress_tracking(self):
        self.write("a.txt", PERFECT)
        self.write("b.txt", STRONG)
        run(screen_files(list_resume_files(str(self.dir)), JOB, top_n=5, progress_key="t-progress"))
        p = get_progress("t-progress")
        self.assertEqual((p["total"], p["analyzed"], p["stage"]), (2, 2, "done"))

    def test_semantic_disabled_and_enabled_paths(self):
        self.write("a.txt", PERFECT)
        self.write("b.txt", PARTIAL)
        off = run(run_bulk([str(p) for p in sorted(self.dir.iterdir())], JOB, top_n=5, semantic=False))
        self.assertFalse(off.summary_stats["semantic_enabled"])
        self.assertTrue(all(r.scores.semantic == 0 for r in off.all_results))
        on = run(run_bulk([str(p) for p in sorted(self.dir.iterdir())], JOB, top_n=5, semantic=True))
        if on.summary_stats["semantic_enabled"]:  # model available locally
            sem = {Path(r.file_path).name: r.scores.semantic for r in on.all_results}
            self.assertGreater(sem["a.txt"], 0)
            self.assertEqual([Path(r.file_path).name for r in on.top_candidates], ["a.txt", "b.txt"])
        else:
            self.assertTrue(on.summary_stats["warnings"])

    def test_empty_folder(self):
        out = run(screen_bulk(str(self.dir), JOB, top_n=5))
        self.assertEqual((out.total_submitted, out.total_processed), (0, 0))


if __name__ == "__main__":
    unittest.main()
