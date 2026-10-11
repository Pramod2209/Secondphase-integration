"""Shared test setup: isolated data dir + synthetic fixture generators (no real resumes needed).

Import this module before anything from `ats` so runtime artefacts go to a temp directory."""
from __future__ import annotations

import io
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, List

_DATA = tempfile.mkdtemp(prefix="ats_test_data_")
os.environ.setdefault("ATS_DATA_DIR", _DATA)
os.environ.setdefault("ATS_PARSE_WORKERS", "2")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

FIXTURES = Path(__file__).parent / "fixtures"


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_pdf(path: Path, objects: List[bytes]) -> Path:
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(out.getvalue())
    return path


def make_text_pdf(path: Path, lines: Iterable[str]) -> Path:
    """Single-page PDF with a real text layer (Helvetica)."""
    body = "BT /F1 11 Tf 50 800 Td 14 TL " + " ".join(f"({_pdf_escape(l)}) Tj T*" for l in lines) + " ET"
    stream = body.encode("latin-1")
    return _write_pdf(path, [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ])


def make_rootless_update_pdf(path: Path, lines: Iterable[str]) -> Path:
    """Valid text PDF followed by an incremental update whose trailer has no /Root
    (structure seen in real resumes carrying a job-portal watermark)."""
    make_text_pdf(path, lines)
    data = path.read_bytes()
    obj_off = len(data)
    extra = b"6 0 obj\n<< /Watermark (portal) >>\nendobj\n"
    xref_off = obj_off + len(extra)
    tail = (f"xref\n0 1\n0000000000 65535 f \n6 1\n{obj_off:010d} 00000 n \n"
            f"trailer\n<< /Size 7 >>\nstartxref\n{xref_off}\n%%EOF\n").encode()
    path.write_bytes(data + extra + tail)
    return path


def make_vector_text_pdf(path: Path) -> Path:
    """PDF whose 'text' is only vector path segments: no text operators, no images."""
    ops = " ".join(f"{i % 500} {i % 700} m {i % 500 + 3} {i % 700 + 5} l" for i in range(3000)) + " S"
    stream = ops.encode()
    return _write_pdf(path, [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ])


def make_image_pdf(path: Path, lines: Iterable[str]) -> Path:
    """Scanned-style PDF: one JPEG page image, no text layer."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("L", (1700, 2200), 255)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=44)
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    y = 120
    for line in lines:
        draw.text((120, y), line, fill=0, font=font)
        y += 70
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    jpeg = buf.getvalue()
    content = b"q 612 0 0 792 0 0 cm /Im1 Do Q"
    return _write_pdf(path, [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /XObject << /Im1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 1700 /Height 2200 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Filter /DCTDecode /Length " + str(len(jpeg)).encode() + b" >>\nstream\n"
        + jpeg + b"\nendstream",
    ])


def make_docx(path: Path, paragraphs: Iterable[str], textbox_lines: Iterable[str] = ()) -> Path:
    """DOCX with body paragraphs and, optionally, text placed only inside a text box."""
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(path))
    textbox_lines = list(textbox_lines)
    if textbox_lines:
        runs = "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in textbox_lines)
        box = ("<w:p><w:r><w:pict><v:shape xmlns:v=\"urn:schemas-microsoft-com:vml\"><v:textbox>"
               f"<w:txbxContent>{runs}</w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>")
        with zipfile.ZipFile(path) as zin:
            items = {n: zin.read(n) for n in zin.namelist()}
        xml = items["word/document.xml"].decode("utf-8")
        items["word/document.xml"] = xml.replace("<w:sectPr", box + "<w:sectPr", 1).encode("utf-8")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
            for n, data in items.items():
                zout.writestr(n, data)
    return path


def resume_text(name: str, skills: str, jobs: List[str], education: str, summary: str = "") -> str:
    parts = [name, "Software Professional", f"{name.lower().replace(' ', '.')}@example.com | +91 90000 00000", ""]
    if summary:
        parts += ["Summary", summary, ""]
    parts += ["Technical Skills", skills, "", "Work Experience", *jobs, "", "Education", education]
    return "\n".join(parts)
