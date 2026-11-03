"""
Loads the text of a tender: one folder = one tender = several files (ТЗ,
договор, извещение...). Facts are later collected across all files at once,
so the loader keeps each file separate and named -- the file name is what
ends up in evidence.doc.

Supported: .docx, .pdf, .txt. Legacy .doc is NOT read here (that would tie
the pipeline to Windows + Word); convert it once with scripts/convert_doc.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SUFFIXES = {".docx", ".pdf", ".txt"}


@dataclass(frozen=True)
class TenderDocument:
    name: str  # file name inside the tender folder, used as evidence.doc
    text: str


class UnsupportedDocumentError(ValueError):
    pass


def load_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".txt":
        return path.read_text(encoding="utf-8-sig")
    if suffix == ".doc":
        raise UnsupportedDocumentError(
            f"{path.name}: legacy .doc is not supported by the pipeline. "
            f"Convert it once: python scripts/convert_doc.py {path.parent}"
        )
    raise UnsupportedDocumentError(f"{path.name}: unsupported file type {suffix!r}")


def load_tender(folder: Path) -> list[TenderDocument]:
    """All supported files of a tender folder, sorted by name. A .doc that
    already has a converted .docx next to it is skipped silently; a .doc
    without one is an error, so a document is never dropped unnoticed."""
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"tender folder not found: {folder}")

    docs: list[TenderDocument] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name.startswith(("~$", ".")):
            continue
        suffix = path.suffix.lower()
        if suffix == ".doc" and path.with_suffix(".docx").exists():
            continue
        if suffix not in SUPPORTED_SUFFIXES and suffix != ".doc":
            continue
        docs.append(TenderDocument(path.name, load_document(path)))
    if not docs:
        raise FileNotFoundError(f"no .docx/.pdf/.txt documents in {folder}")
    return docs


def _read_docx(path: Path) -> str:
    """Body in document order: python-docx's doc.paragraphs / doc.tables
    would put every table after all paragraphs, detaching e.g. a volumes
    table from its heading. Merged cells are emitted once, not per grid
    column."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    lines: list[str] = []

    def walk(parent) -> None:
        for child in parent.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                lines.append(Paragraph(child, doc).text)
            elif tag == "tbl":
                for row in Table(child, doc).rows:
                    cells, seen = [], set()
                    for cell in row.cells:
                        if id(cell._tc) in seen:
                            continue
                        seen.add(id(cell._tc))
                        cells.append(" ".join(cell.text.split()))
                    lines.append(" | ".join(cells))
            elif tag == "sdt":
                content = child.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sdtContent")
                if content is not None:
                    walk(content)

    walk(doc.element.body)
    return "\n".join(lines)


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("Reading PDF requires pypdf: pip install pypdf") from e

    text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    if not text.strip():
        raise UnsupportedDocumentError(
            f"{path.name}: the PDF has no text layer (scanned?) -- OCR is not supported yet"
        )
    return text
