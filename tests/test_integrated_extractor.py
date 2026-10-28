"""Unit and integration tests for the Resume Data Extractor module."""
import io
import os
import tempfile
import unittest
import zipfile
from fastapi.testclient import TestClient

from ats.server import app
from ats.data_extractor import (
    process_all,
    process_file,
    parse_text,
    build_excel_bytes,
    get_files,
    FINAL_HEADERS,
)


class DataExtractorModuleTests(unittest.TestCase):
    def test_headers_structure(self):
        self.assertEqual(len(FINAL_HEADERS), 12)
        self.assertIn("File Name", FINAL_HEADERS)
        self.assertIn("Candidate Name", FINAL_HEADERS)
        self.assertIn("Candidate Contact Number", FINAL_HEADERS)
        self.assertIn("Email", FINAL_HEADERS)
        self.assertIn("Total Years of Experience", FINAL_HEADERS)
        self.assertIn("Skills", FINAL_HEADERS)

    def test_text_parsing_clean(self):
        raw_text = (
            "Sunita Rao\n"
            "Email: sunita.rao@example.com\n"
            "Mobile: +91 9876543210\n"
            "Experience: 5 years of experience in backend development\n"
            "Skills: Python, FastAPI, Docker, SQL, Redis\n"
            "Work Experience: Software Engineer at Infosys from 2019 to Present\n"
            "Education: B.Tech Computer Science\n"
            "Languages: English, Hindi\n"
        )
        res = parse_text(raw_text, [], "Sunita_Rao_Resume.pdf")
        self.assertEqual(res["Candidate Name"], "Sunita Rao")
        self.assertEqual(res["Email"], "sunita.rao@example.com")
        self.assertIn("9876543210", res["Candidate Contact Number"])
        self.assertEqual(res["Total Years of Experience"], "5 Years")
        self.assertIn("Python", res["Skills"])
        self.assertIn("Docker", res["Skills"])
        self.assertEqual(res["Current Company"], "Infosys")

    def test_date_range_without_datetime_error(self):
        raw_text = (
            "Vikram Singh\n"
            "vikram@example.com\n"
            "9988776655\n"
            "Work Experience\n"
            "Lead Developer at TCS\n"
            "2018 - 2023\n"
            "Skills: Java, Spring Boot, MySQL\n"
            "Education: Master of Computer Applications\n"
        )
        # Must not raise NameError: name 'datetime' is not defined
        res = parse_text(raw_text, [], "Vikram_Singh.pdf")
        self.assertEqual(res["Candidate Name"], "Vikram Singh")
        self.assertEqual(res["Email"], "vikram@example.com")
        self.assertIn("Years", res["Total Years of Experience"])

    def test_build_excel_bytes(self):
        sample = [{
            "File Name": "candidate1.pdf",
            "Candidate Name": "Neha Gupta",
            "Candidate Contact Number": "+91 9123456780",
            "Email": "neha@example.com",
            "Total Years of Experience": "4 Years",
            "Skills": "React, JavaScript, CSS",
            "Current Company": "Wipro",
            "Previous Companies": "Not Provided",
            "Current Location": "Bangalore",
            "Education (UG / PG Degree)": "B.Sc",
            "Languages Known": "English",
            "Years Worked in Current Company": "2",
        }]
        b = build_excel_bytes(sample)
        self.assertIsInstance(b, bytes)
        self.assertGreater(len(b), 1000)

    def test_api_extract_zip_archive(self):
        client = TestClient(app)
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr(
                "candidate_a.txt",
                "Aman Verma\nPhone: +91 9876500001\nEmail: aman@example.com\n"
                "4 years experience in Python, AWS\nEducation: B.Tech"
            )
            zf.writestr(
                "candidate_b.txt",
                "Pooja Joshi\nPhone: +91 9876500002\nEmail: pooja@example.com\n"
                "3 years experience in Docker, Kubernetes\nEducation: MCA"
            )

        zip_buf.seek(0)
        files = [("files", ("candidates.zip", zip_buf.getvalue(), "application/zip"))]
        headers = {"X-ATS-Client": "webui"}

        res = client.post("/api/extract", files=files, headers=headers)
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["total_extracted"], 2)
        names = {r["Candidate Name"] for r in data["records"]}
        self.assertIn("Aman Verma", names)
        self.assertIn("Pooja Joshi", names)

    def test_api_extract_excel_download(self):
        client = TestClient(app)
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr(
                "test_cv.txt",
                "Deepak Kumar\ndeepak@example.com\n9811223344\nPython, SQL, Linux\n3 years experience\nB.E."
            )

        zip_buf.seek(0)
        files = [("files", ("batch.zip", zip_buf.getvalue(), "application/zip"))]
        headers = {"X-ATS-Client": "webui"}

        res = client.post("/api/extract/excel", files=files, headers=headers)
        self.assertEqual(res.status_code, 200)
        self.assertIn("application/vnd.openxmlformats", res.headers.get("content-type", ""))
        self.assertGreater(len(res.content), 2000)


if __name__ == "__main__":
    unittest.main()
