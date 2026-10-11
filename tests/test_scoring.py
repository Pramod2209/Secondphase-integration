"""Score bounds, edge cases, education scoring, explanations, and job validation."""
from tests import helpers  # noqa: F401

import itertools
import math
import unittest

from ats.api import JobValidationError, build_job_profile
from ats.schema import ResumeResult
from ats.scorer import compute_scores, education_score, generate_explanation


def job(**kw):
    base = dict(title="Backend Engineer", description="Python APIs", required_skills="python,sql",
                preferred_skills="redis", min_experience_years=3, required_education="bachelor")
    base.update(kw)
    return build_job_profile(**base)


class ScoreBoundsTests(unittest.TestCase):
    def test_always_bounded_and_finite(self):
        jobs = [job(), job(required_skills="", preferred_skills=""), job(min_experience_years=0),
                job(required_education="any"), job(description="")]
        cosines = [None, float("nan"), float("inf"), -1.0, 0.0, 0.5, 1.0, 7.0]
        for j, cos, years, edu, sem in itertools.product(jobs, cosines, [-5, 0, 2, 3, 100, float("nan")],
                                                           ["unknown", "school", "bachelor", "phd", "garbage"],
                                                           [True, False]):
            s = compute_scores(j.required_skills[:1], j.required_skills[1:], 4.0, years, cos, edu, j, sem)
            for value in (s.skills, s.experience, s.semantic, s.education, s.final):
                self.assertTrue(math.isfinite(value) and 0.0 <= value <= 100.0, (value, cos, years, edu))

    def test_all_none_partial_skills(self):
        j = job()
        self.assertEqual(compute_scores(["python", "sql"], [], 0, 3, None, "bachelor", j, False).skills, 100.0)
        self.assertEqual(compute_scores([], ["python", "sql"], 0, 3, None, "bachelor", j, False).skills, 0.0)
        self.assertEqual(compute_scores(["python"], ["sql"], 0, 3, None, "bachelor", j, False).skills, 50.0)
        self.assertEqual(compute_scores(["python", "sql"], [], 10, 3, None, "bachelor", j, False).skills, 100.0)

    def test_zero_requirements_are_neutral(self):
        j = job(required_skills="", preferred_skills="", min_experience_years=0, required_education="any")
        s = compute_scores([], [], 0, 0, None, "unknown", j, False)
        self.assertEqual((s.skills, s.experience, s.education, s.final), (100.0, 100.0, 100.0, 100.0))

    def test_experience_scaling(self):
        j = job()
        self.assertEqual(compute_scores(["python"], ["sql"], 0, 1.5, None, "bachelor", j, False).experience, 50.0)
        self.assertEqual(compute_scores(["python"], ["sql"], 0, 30, None, "bachelor", j, False).experience, 100.0)

    def test_regression_unrelated_experience_not_credited(self):
        # A resume matching none of the job's skills must not earn experience credit
        # (previously a cook with 10 years outranked a partial match with 2 years).
        j = job()
        unrelated = compute_scores([], ["python", "sql"], 0, 10, None, "bachelor", j, False)
        partial = compute_scores(["python"], ["sql"], 0, 2, None, "bachelor", j, False)
        self.assertEqual(unrelated.experience, 0.0)
        self.assertGreater(partial.final, unrelated.final)
        preferred_only = compute_scores([], ["python", "sql"], 2.0, 10, None, "bachelor", j, False)
        self.assertEqual(preferred_only.experience, 100.0)
        no_skills_job = job(required_skills="", preferred_skills="")
        self.assertEqual(compute_scores([], [], 0, 10, None, "bachelor", no_skills_job, False).experience, 100.0)

    def test_semantic_unavailable_renormalizes(self):
        j = job()
        with_sem = compute_scores(["python", "sql"], [], 0, 3, 1.0, "bachelor", j, True)
        no_sem = compute_scores(["python", "sql"], [], 0, 3, None, "bachelor", j, True)
        self.assertEqual(with_sem.final, 100.0)
        self.assertEqual(no_sem.final, 100.0)
        self.assertEqual(no_sem.semantic, 0.0)


class EducationScoreTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(education_score("master", "bachelor"), 100.0)
        self.assertEqual(education_score("bachelor", "bachelor"), 100.0)
        self.assertEqual(education_score("diploma", "bachelor"), 70.0)
        self.assertEqual(education_score("school", "bachelor"), 40.0)
        self.assertEqual(education_score("bachelor", "phd"), 40.0)
        self.assertEqual(education_score("unknown", "bachelor"), 0.0)  # no evidence -> no credit
        self.assertEqual(education_score("unknown", "any"), 100.0)

    def test_requirement_spellings(self):
        # Regression: "bachelor's" used to fall through to level 1, giving everyone full marks.
        self.assertEqual(build_job_profile("t", "", "", "", 0, "Bachelor's").required_education, "bachelor")
        self.assertEqual(build_job_profile("t", "", "", "", 0, "Masters").required_education, "master")
        self.assertEqual(education_score("school", "bachelor's"), 40.0)


class ExplanationTests(unittest.TestCase):
    def make(self, edu, final):
        from ats.schema import ScoreBreakdown

        return ResumeResult(candidate_id="x", file_path="a.pdf", name="A", parsed_text="", extracted_skills=[],
                            experience_years=4.0, education_level=edu, embedding=None,
                            scores=ScoreBreakdown(100, 100, 50, education_score(edu, "bachelor"), final),
                            missing_skills=[], matched_skills=["python", "sql"], explanation="",
                            status="processed", stage_timings={})

    def test_regression_no_education_gap_when_requirement_exceeded(self):
        text = generate_explanation(self.make("master", 90), job())
        self.assertNotIn("Education:", text)
        self.assertIn("Strong shortlist", text)

    def test_gap_when_education_missing(self):
        text = generate_explanation(self.make("unknown", 50), job())
        self.assertIn("Education: not found in resume (required: bachelor)", text)

    def test_semantic_disabled_wording(self):
        text = generate_explanation(self.make("master", 90), job(), semantic_enabled=False)
        self.assertIn("not computed", text)


class JobValidationTests(unittest.TestCase):
    def test_invalid_inputs(self):
        with self.assertRaises(JobValidationError):
            job(min_experience_years=-1)
        with self.assertRaises(JobValidationError):
            job(min_experience_years="abc")
        with self.assertRaises(JobValidationError):
            job(min_experience_years=float("nan"))
        with self.assertRaises(JobValidationError):
            job(required_education="kindergarten")
        with self.assertRaises(JobValidationError):
            build_job_profile("", "", "", "", 0, "any")

    def test_skill_normalization_and_dedupe(self):
        j = job(required_skills="JS, javascript, K8s,, ", preferred_skills="kubernetes, Redis")
        self.assertEqual(j.required_skills, ["javascript", "kubernetes"])
        self.assertEqual(j.preferred_skills, ["redis"])  # duplicates of required removed


if __name__ == "__main__":
    unittest.main()
