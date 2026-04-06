from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from time import perf_counter
from typing import Callable, Tuple

from docx import Document
from pdfminer.converter import TextConverter
from pdfminer.layout import LAParams
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage

from . import config
from .logger import get_logger

logger = get_logger(__name__)


def _normalize_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""

    # Drop lines repeated many times to reduce headers/footers.
    freq = {}
    for line in lines:
        freq[line] = freq.get(line, 0) + 1
    filtered = [line for line in lines if freq.get(line, 0) < 3]

    cleaned = "\n".join(filtered)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    cleaned = re.sub(r"[^\x00-\x7F\-\/@+\.\s]", "", cleaned)
    return cleaned


def parse_txt(file_path: str) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return Path(file_path).read_text(encoding=encoding, errors="ignore")
        except Exception:
            continue
    return ""


def parse_docx(file_path: str) -> str:
    doc = Document(file_path)
    chunks = [p.text for p in doc.paragraphs if p.text]
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                chunks.append(" | ".join(cells))
    return "\n".join(chunks)


def _ocr_pdf(file_path: str) -> str:
    from pdf2image import convert_from_path
    import pytesseract

    pages = convert_from_path(file_path)
    ocr_text = []
    for page in pages:
        ocr_text.append(pytesseract.image_to_string(page))
    return "\n".join(ocr_text)


def parse_pdf(file_path: str) -> str:
    resource_manager = PDFResourceManager()
    buffer = StringIO()
    laparams = LAParams()
    converter = TextConverter(resource_manager, buffer, laparams=laparams)

    with open(file_path, "rb") as fp:
        interpreter = PDFPageInterpreter(resource_manager, converter)
        for page in PDFPage.get_pages(fp, check_extractable=True):
            interpreter.process_page(page)

    text = buffer.getvalue()
    converter.close()
    buffer.close()
    return text


EXT_ROUTER: dict[str, Callable[[str], str]] = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".txt": parse_txt,
}


def parse_file(file_path: str) -> Tuple[str, float]:
    start = perf_counter()
    try:
        suffix = Path(file_path).suffix.lower()
        parser_fn = EXT_ROUTER.get(suffix)
        if parser_fn is None:
            raise ValueError(f"Unsupported file extension: {suffix}")

        text = parser_fn(file_path)
        normalized = _normalize_text(text)

        if suffix == ".pdf" and config.OCR_ENABLED and len(normalized.strip()) < 150:
            normalized = _normalize_text(_ocr_pdf(file_path))

        elapsed = perf_counter() - start
        return normalized, elapsed
    except Exception as exc:
        elapsed = perf_counter() - start
        logger.exception("Failed parsing %s (%s): %s", file_path, type(exc).__name__, str(exc))
        return "", elapsed


def read_raw_fast(file_path: str, max_bytes: int = 300000) -> str:
    try:
        data = Path(file_path).read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="ignore").lower()
    except Exception:
        return ""
