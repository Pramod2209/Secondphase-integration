"""ATS_REFERENCE_DATE: "present" in resumes means today unless explicitly configured."""
from tests import helpers  # noqa: F401

import os
import unittest
from datetime import datetime
from unittest import mock

from ats import config
from ats.extractor import extract_experience, reference_date, using_reference_date
from ats.parser import normalize_text


def exp(text):
    return extract_experience(normalize_text(text)[0])


OPEN_JOB = "Experience\nDeveloper, Acme Pvt Ltd  Jan 2020 - Present"
CLOSED_JOB = "Experience\nDeveloper, Acme Pvt Ltd  Jan 2015 - Jan 2018"


class ReferenceDateTests(unittest.TestCase):
    def test_default_is_today(self):
        with mock.patch.object(config, "REFERENCE_DATE", None):
            self.assertLess(abs((reference_date() - datetime.now()).total_seconds()), 5)
            expected = (datetime.now() - datetime(2020, 1, 1)).days / 365.25
            self.assertAlmostEqual(exp(OPEN_JOB), expected, places=1)

    def test_reference_date_changes_open_ended_jobs_only(self):
        with mock.patch.object(config, "REFERENCE_DATE", datetime(2023, 1, 1)):
            self.assertAlmostEqual(exp(OPEN_JOB), 3.0, places=1)
            self.assertAlmostEqual(exp(CLOSED_JOB), 3.0, places=1)
        with mock.patch.object(config, "REFERENCE_DATE", None):
            self.assertAlmostEqual(exp(CLOSED_JOB), 3.0, places=1)  # unaffected by the setting
            self.assertEqual(exp("5 years of experience in Java"), 5.0)

    def test_dates_after_reference_are_clipped(self):
        with mock.patch.object(config, "REFERENCE_DATE", datetime(2019, 1, 1)):
            self.assertEqual(exp(OPEN_JOB), 0.0)  # job starts after the reference date

    def test_per_run_date_overrides_setting_and_is_restored(self):
        with mock.patch.object(config, "REFERENCE_DATE", datetime(2019, 1, 1)):
            with using_reference_date(datetime(2025, 1, 1)):
                self.assertAlmostEqual(exp(OPEN_JOB), 5.0, places=1)
            self.assertEqual(exp(OPEN_JOB), 0.0)  # back to the configured date
        with using_reference_date(None):  # no per-run date: configured date / today
            self.assertLess(abs((reference_date() - datetime.now()).total_seconds()), 5)

    def test_env_parsing(self):
        with mock.patch.dict(os.environ, {"ATS_REFERENCE_DATE": "2024-06-30"}):
            self.assertEqual(config._reference_date(), datetime(2024, 6, 30))
        with mock.patch.dict(os.environ, {"ATS_REFERENCE_DATE": "not-a-date"}):
            self.assertIsNone(config._reference_date())  # invalid -> today
        with mock.patch.dict(os.environ, {"ATS_REFERENCE_DATE": ""}):
            self.assertIsNone(config._reference_date())


if __name__ == "__main__":
    unittest.main()
