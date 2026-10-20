"""Build complete reading bundles and publish a receipt only after validation."""

from __future__ import annotations

import json
import importlib.metadata
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from .epub import render_epub, render_html
from .extract import extract
from .fonts import font_files, cmap
from .model import Document, ReadingProfile, atomic_json, digest, fingerprint, document_assets
from .pdf import render_pdf
from .validate import audit_source, validate_epub, validate_pdf
from .source import open_native_pdf


def renderer_key() -> str:
    root = Path(__file__).parent
    return fingerprint({p.name: digest(p) for p in root.glob("*.py")
                        if p.name not in {"cli.py", "batch.py", "library.py"}})


def output_name(source: Path) -> str:
    stem = re.sub(r"[^\w .()-]+", "-", source.stem, flags=re.UNICODE).strip(" .-")
    return (stem[:90].rstrip(" .-") or "paper")


def receipt_matches(folder: Path, request: dict) -> bool:
    try:
        report = json.loads((folder / "conversion-report.json").read_text(encoding="utf-8"))
        if report["request"] != request or not report.get("complete"):
            return False
        for item in report["outputs"].values():
            path = folder / item["filename"]
            if not path.is_file() or digest(path) != item["sha256"]:
                return False
        assets = json.loads((folder / "assets-manifest.json").read_text(encoding="utf-8"))
        if any(not (folder / name).is_file() or digest(folder / name) != expected
               for name, expected in assets.items()):
            return False
        return bool(report["outputs"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def preserve_source_glyphs(document: Document, source: Path, cache: Path, fonts: dict) -> None:
    """Keep source outlines when a passage cannot be represented in reading fonts.

    This handles broken PDF character maps as well as unsupported script letters.
    It never substitutes a guessed character or silently removes source content.
    """
    supported = set().union(*(cmap(path) for path in fonts.values()))
    affected = []
    for block in document.blocks:
        visible = block.caption if block.asset else block.text
        for span in sorted((s for s in block.spans if s.get("asset")), key=lambda s: s["start"], reverse=True):
            visible = visible[:span["start"]] + " " + visible[span["end"]:]
        def invalid_xml(c):
            n = ord(c)
            return (n < 32 and c not in "\t\n\r") or 0xD800 <= n <= 0xDFFF or n in {0xFFFE, 0xFFFF}
        bad = sorted({ord(c) for c in visible if invalid_xml(c) or (not c.isspace() and
                      (ord(c) not in supported or ord(c) < 32 or c == "\ufffd"))})
        # Image descriptions must also be valid XML when an inline symbol in
        # the visible caption has already been replaced by its original crop.
        bad = sorted(set(bad) | {ord(c) for c in block.caption if invalid_xml(c)})
        if bad or re.search(r"\(cid:\d+\)", visible):
            affected.append((block, bad))
    if not affected:
        return
    import pdfplumber
    from .extract import _crop
    with open_native_pdf(source) as pdf:
        for block, bad in affected:
            if not block.regions:
                raise ValueError(f"Unsupported glyph without a source location: {block.id}")
            text_crop = not block.asset
            if block.asset:
                block.regions.extend(block.caption_regions)
            _crop(block, pdf, cache)
            if text_crop:
                block.asset_role = "source_text"
                sizes = [c["size"] for region in block.regions
                         for c in pdf.pages[region.page - 1].crop(tuple(region.bbox)).chars if c["size"] > 0]
                block.asset_text_size = sorted(sizes)[len(sizes) // 2] if sizes else 10
            block.caption, block.spans = "", []
            for note in document.blocks:
                note.backlinks = [link for link in note.backlinks if link["block"] != block.id]
            document.review = [r for r in document.review if not
                               (r.get("block") == block.id and r["reason"].startswith("Unresolved source glyph"))]
            document.review.append({"block": block.id, "page": block.regions[0].page,
                "reason": "Original source rendering retained for unsupported glyphs",
                "glyphs": [f"U+{c:04X}" for c in bad]})


def convert(source: Path, output_root: Path, work: Path, profile: ReadingProfile,
            *, formats: tuple[str, ...] = ("pdf", "epub"), force: bool = False,
            reextract: bool = False, ocr: str = "auto", device: str = "auto",
            font_dir: Path | None = None, embed_fonts: bool = True,
            title: str | None = None, authors: list[str] | None = None,
            name: str | None = None, allow_inference: bool = True) -> tuple[Path, dict]:
    source = source.resolve(strict=True)
    source_hash = digest(source)
    stem = name or output_name(source)
    if Path(stem).name != stem or stem in {".", ".."}:
        raise ValueError("Output name must be a filename, not a path.")
    folder = output_root.resolve() / (stem + "--" + source_hash[:8])
    fonts = font_files(font_dir)
    request = {"source_sha256": source_hash, "profile": asdict(profile),
        "backend": importlib.metadata.version("docling"),
        "renderer": renderer_key(), "formats": list(formats), "ocr": ocr, "device": device,
        "title": title, "authors": authors, "embed_fonts": embed_fonts,
        "fonts": {key: digest(path) for key, path in fonts.items()}}
    if not force and not reextract and receipt_matches(folder, request):
        return folder, json.loads((folder / "conversion-report.json").read_text(encoding="utf-8"))
    started = time.perf_counter()
    document, cache, reused = extract(source, work.resolve(), force=reextract, ocr=ocr, device=device,
                                    allow_inference=allow_inference)
    if title:
        document.title = title
    if authors is not None:
        document.authors = authors
    preserve_source_glyphs(document, source, cache, fonts)
    output_root.mkdir(parents=True, exist_ok=True)
    report = {"complete": False, "request": request, "source": str(source),
        "source_pages": document.pages, "title": document.title,
        "profile": asdict(profile), "extraction_cache": str(cache), "extraction_reused": reused,
        "extraction": document.extraction, "review": list(document.review), "outputs": {}}
    with tempfile.TemporaryDirectory(prefix=".reading-", dir=output_root) as temporary:
        stage = Path(temporary)
        if "pdf" in formats:
            path = stage / (stem + ".reading.pdf")
            typesetting = render_pdf(document, cache, path, profile, fonts)
            validation = validate_pdf(path, document)
            report["outputs"]["pdf"] = {"filename": path.name, **validation, **typesetting}
            if typesetting["overfull_boxes"]:
                report["review"].append({"reason": "Typesetting overflow", "details": typesetting["overfull_boxes"]})
            if validation["text_coverage"]["retained_fraction"] < .995:
                report["review"].append({"reason": "PDF text extraction differs from the shared document",
                    "details": validation["text_coverage"]})
        if "epub" in formats:
            path = stage / (stem + ".epub")
            render_epub(document, cache, path, profile, fonts, embed_fonts=embed_fonts)
            report["outputs"]["epub"] = {"filename": path.name, **validate_epub(path, document)}
        preview = stage / "preview.html"
        render_html(document, cache, preview, profile, fonts)
        report["outputs"]["preview"] = {"filename": preview.name, "sha256": digest(preview)}
        audit = audit_source(source, document)
        report["source_audit"] = audit
        for page in audit["pages"]:
            if page["region_coverage"] < .995:
                report["review"].append({"page": page["page"], "reason": "Native source text outside extracted regions",
                    "coverage": page["region_coverage"], "sample": page["unaccounted_text"]})
        if digest(source) != source_hash:
            raise RuntimeError("The original PDF changed while building the reading copy.")
        folder.mkdir(parents=True, exist_ok=True)
        # Each file replacement is atomic; the receipt is the final completion marker.
        for path in stage.iterdir():
            if path.is_file():
                os.replace(path, folder / path.name)
        for assets in ("assets", "tex"):
            if (stage / assets).exists():
                shutil.copytree(stage / assets, folder / assets, dirs_exist_ok=True)
        report["outputs"]["assets"] = {"filename": "assets-manifest.json", "sha256": ""}
        asset_hashes = {asset: digest(folder / asset) for asset in document_assets(document)}
        atomic_json(folder / "assets-manifest.json", asset_hashes)
        report["outputs"]["assets"]["sha256"] = digest(folder / "assets-manifest.json")
        report["complete"] = True
        report["status"] = "needs_review" if report["review"] else "ready"
        report["elapsed_seconds"] = time.perf_counter() - started
        atomic_json(folder / "conversion-report.json", report)
    return folder, report
