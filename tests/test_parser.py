"""Parser: PDF / DOCX / TXT / DOC, empty and malformed files, OCR fallback for scanned PDFs."""
from tests import helpers

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ats import parser
from ats.parser import normalize_text, parse_document


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ats_parser_"))

    def test_txt_utf8_and_unicode_folding(self):
        p = self.tmp / "a.txt"
        p.write_text("Jane Smith\nSoftware Engineer – Jan 2020 – Present\nSkills: Python•SQL", encoding="utf-8")
        r = parse_document(str(p))
        self.assertIsNone(r.error)
        self.assertIn("jan 2020 - present", r.text)
        self.assertEqual(r.display_text.splitlines()[0], "Jane Smith")  # case preserved for names

    def test_text_pdf(self):
        p = helpers.make_text_pdf(self.tmp / "a.pdf", ["Ravi Kumar", "Python developer, 5 years of experience"])
        r = parse_document(str(p))
        self.assertIsNone(r.error)
        self.assertIn("python developer", r.text)
        self.assertFalse(r.ocr_used)

    def test_docx_paragraphs_and_textboxes(self):
        p = helpers.make_docx(self.tmp / "a.docx", ["Anita Rao"], textbox_lines=["Skills: Kubernetes, Terraform"])
        r = parse_document(str(p))
        self.assertIsNone(r.error)
        self.assertIn("kubernetes", r.text)  # regression: text boxes were invisible to python-docx

    def test_empty_file(self):
        p = self.tmp / "empty.pdf"
        p.write_bytes(b"")
        self.assertTrue(parse_document(str(p)).error.startswith("empty_file"))

    def test_corrupt_files_never_raise(self):
        for name, data in (("bad.pdf", b"%PDF-1.4 garbage"), ("bad.docx", b"not a zip"), ("bad.pdf2", b"x")):
            p = self.tmp / name
            p.write_bytes(data)
            r = parse_document(str(p))
            self.assertTrue(r.error, name)
            self.assertEqual(r.text, "")

    def test_unsupported_extension_reported(self):
        p = self.tmp / "photo.png"
        p.write_bytes(b"\x89PNG")
        self.assertTrue(parse_document(str(p)).error.startswith("unsupported_file_type"))

    def test_doc_without_antiword_is_reported_not_dropped(self):
        p = self.tmp / "old.doc"
        p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 100)
        with mock.patch.object(parser, "_antiword_path", return_value=None):
            r = parse_document(str(p))
        self.assertTrue(r.error.startswith("unsupported_file_type"))

    @unittest.skipUnless(parser._antiword_path(), "antiword not installed")
    def test_doc_with_antiword_garbage_fails_cleanly(self):
        p = self.tmp / "broken.doc"
        p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 100)
        self.assertTrue(parse_document(str(p)).error)

    @unittest.skipUnless(parser.ocr_available(), "Tesseract not available")
    def test_image_only_pdf_uses_ocr(self):
        p = helpers.make_image_pdf(self.tmp / "scan.pdf", ["Meena Iyer", "Accountant with Tally and Excel",
                                                            "Experience 2018 to 2022"])
        r = parse_document(str(p))
        self.assertIsNone(r.error, r.error)
        self.assertTrue(r.ocr_used)
        self.assertIn("tally", r.text)

    @unittest.skipUnless(parser.ocr_available(), "Tesseract not available")
    def test_regression_image_only_docx_uses_ocr(self):
        # Real resume: a scanned page pasted into Word as a picture -> previously "empty_text".
        import io
        import zipfile

        scan = helpers.make_image_pdf(self.tmp / "s.pdf", ["Pradeep Nair", "Quality Engineer with Excel and SAP"])
        from ats.parser import _pdf_page_images

        img = next(_pdf_page_images(str(scan), 1))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        docx = helpers.make_docx(self.tmp / "picture.docx", [])
        with zipfile.ZipFile(docx, "a") as zf:
            zf.writestr("word/media/image1.png", buf.getvalue())
        r = parse_document(str(docx))
        self.assertIsNone(r.error, r.error)
        self.assertTrue(r.ocr_used)
        self.assertIn("quality engineer", r.text)

    def test_image_only_pdf_without_ocr_is_reported(self):
        p = helpers.make_image_pdf(self.tmp / "scan2.pdf", ["Some text"])
        with mock.patch.object(parser, "ocr_available", return_value=False):
            r = parse_document(str(p))
        self.assertTrue(r.error.startswith("no_text_layer"))

    def test_regression_pdf_with_rootless_incremental_trailer(self):
        # Real resumes: last trailer lacks /Root, so pdfminer saw 0 pages -> reported as image-only.
        p = helpers.make_rootless_update_pdf(self.tmp / "watermarked.pdf", ["Kiran Rao", "Warehouse operative"])
        r = parse_document(str(p))
        self.assertIsNone(r.error, r.error)
        self.assertIn("warehouse operative", r.text)
        self.assertFalse(r.ocr_used)

    def test_vector_outline_pdf_has_precise_reason(self):
        p = helpers.make_vector_text_pdf(self.tmp / "outlines.pdf")
        r = parse_document(str(p))
        self.assertTrue(r.error.startswith("vector_text_pdf"), r.error)

    def test_repeated_header_lines_removed(self):
        text, _ = normalize_text("Page header\nA\nPage header\nB\nPage header\nC")
        self.assertNotIn("page header", text)


if __name__ == "__main__":
    unittest.main()
