"""PDF plugin: text layer extraction, loud failure without one."""
import io

import pytest

from selflearn.acquisition import AcquireContext, AcquisitionError
from selflearn.acquisition.plugins import PdfPlugin
from selflearn.contracts import SourceRef

pypdf = pytest.importorskip("pypdf")
from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)


def make_pdf(text: str = "") -> bytes:
    """A real one-page PDF; with text, it has an extractable text layer."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text:
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(content)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): writer._add_object(font)})})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_text_layer_extraction(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(make_pdf("Lifespan replaces on_event handlers."))
    ctx = AcquireContext(workdir=tmp_path / "w")
    docs = PdfPlugin().acquire(SourceRef(uri=f"file://{pdf}"), ctx)
    assert "Lifespan replaces on_event" in docs[0].blocks[0]
    assert docs[0].provenance.locator.startswith("p1-")
    assert docs[0].provenance.plugin == "pdf"


def test_pdf_without_text_layer_is_loud(tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(make_pdf())     # blank page: no text layer
    ctx = AcquireContext(workdir=tmp_path / "w")
    with pytest.raises(AcquisitionError, match="no text layer"):
        PdfPlugin().acquire(SourceRef(uri=f"file://{pdf}"), ctx)
