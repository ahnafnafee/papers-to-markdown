"""Read native text from a verified, complete physical PDF page tree."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .model import atomic_json, digest


def open_native_pdf(source: Path):
    import pdfplumber
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(source, strict=False)
    if reader.is_encrypted:
        reader.decrypt("")
    expected = len(reader.pages)
    pdf = pdfplumber.open(source)
    if len(pdf.pages) == expected:
        return pdf
    found = len(pdf.pages)
    pdf.close()
    identity = digest(source)
    root = Path(tempfile.gettempdir()) / "PapersToMarkdown" / "native-inputs"
    root.mkdir(parents=True, exist_ok=True)
    compatible = root / (identity + ".pdf")
    proof = compatible.with_suffix(".json")
    if not compatible.is_file() or not proof.is_file():
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        metadata = reader.metadata or {}
        writer.add_metadata({key: value for key in ("/Title", "/Author", "/Subject", "/Keywords")
                             if isinstance(value := metadata.get(key), str)})
        with tempfile.NamedTemporaryFile(suffix=".pdf", dir=root, delete=False) as handle:
            temporary = Path(handle.name)
            writer.write(handle)
        try:
            _verify_pixels(source, temporary, expected)
            with pdfplumber.open(temporary) as repaired:
                if len(repaired.pages) != expected:
                    raise ValueError("The native PDF reader still skips physical pages after repair.")
            os.replace(temporary, compatible)
            atomic_json(proof, {"original_sha256": identity, "copy_sha256": digest(compatible),
                "pages": expected, "original_native_pages": found, "all_page_pixels_identical": True})
        finally:
            temporary.unlink(missing_ok=True)
    return pdfplumber.open(compatible)


def _verify_pixels(original: Path, copy: Path, expected: int):
    import pypdfium2 as pdfium
    from PIL import ImageChops

    with pdfium.PdfDocument(original) as a, pdfium.PdfDocument(copy) as b:
        if len(a) != expected or len(b) != expected:
            raise ValueError("PDF repair changed the physical page count.")
        for number in range(expected):
            pa, pb = a[number], b[number]
            try:
                ia = pa.render(scale=.5).to_pil().convert("RGB")
                ib = pb.render(scale=.5).to_pil().convert("RGB")
                if ia.size != ib.size or ImageChops.difference(ia, ib).getbbox():
                    raise ValueError(f"PDF repair changed visible pixels on page {number + 1}.")
            finally:
                pa.close()
                pb.close()
