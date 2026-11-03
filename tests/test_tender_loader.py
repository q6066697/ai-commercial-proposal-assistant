"""
Tender loader on synthetic files built in tmp_path: the real tender documents
are third-party and git-ignored, so tests never depend on data/tenders/raw/.
"""
import sys
from pathlib import Path

import pytest
from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tender.loader import UnsupportedDocumentError, load_document, load_tender


def make_docx(path: Path) -> Path:
    doc = Document()
    doc.add_paragraph("Перечень объектов закупки")
    table = doc.add_table(rows=2, cols=3)
    header = table.rows[0].cells
    header[0].text = "Объект"
    merged = header[1].merge(header[2])
    merged.text = "Площадь, кв. м."
    row = table.rows[1].cells
    row[0].text = "Строение 1"
    row[1].text = "1171,2"
    row[2].text = "По согласованию"
    doc.add_paragraph("Итого: 1171,2")
    doc.save(path)
    return path


def make_pdf(path: Path, text: str | None) -> Path:
    """Minimal one-page PDF; text=None gives a page without a text layer."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def test_docx_keeps_table_in_document_order_and_merged_cells_once(tmp_path):
    text = load_document(make_docx(tmp_path / "tz.docx"))
    lines = text.splitlines()
    assert lines == [
        "Перечень объектов закупки",
        "Объект | Площадь, кв. м.",
        "Строение 1 | 1171,2 | По согласованию",
        "Итого: 1171,2",
    ]


def test_pdf_with_text_layer(tmp_path):
    assert "Facade area 10740 m2" in load_document(make_pdf(tmp_path / "a.pdf", "Facade area 10740 m2"))


def test_scanned_pdf_is_a_clear_error(tmp_path):
    with pytest.raises(UnsupportedDocumentError, match="OCR"):
        load_document(make_pdf(tmp_path / "scan.pdf", None))


def test_tender_folder_loads_every_file_by_name(tmp_path):
    make_docx(tmp_path / "tz.docx")
    (tmp_path / "contract.txt").write_text("площадью _________ кв. м.", encoding="utf-8")
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")  # not a document
    docs = load_tender(tmp_path)
    assert [d.name for d in docs] == ["contract.txt", "tz.docx"]


def test_converted_doc_is_skipped_but_unconverted_doc_is_an_error(tmp_path):
    make_docx(tmp_path / "tz.docx")
    (tmp_path / "tz.doc").write_bytes(b"legacy")
    assert [d.name for d in load_tender(tmp_path)] == ["tz.docx"]

    (tmp_path / "contract.doc").write_bytes(b"legacy")
    with pytest.raises(UnsupportedDocumentError, match="convert_doc.py"):
        load_tender(tmp_path)
