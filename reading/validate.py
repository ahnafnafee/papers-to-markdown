"""Content, source-region, and container checks independent of the exporters."""

from __future__ import annotations

import json
import posixpath
import re
import shutil
import subprocess
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from .extract import clean_text
from .model import Document, digest
from .source import open_native_pdf


def tokens(text: str) -> Counter:
    return Counter(re.findall(r"[\w]+", clean_text(text).casefold()))


def text_coverage(expected: str, actual: str) -> dict:
    wanted = tokens(expected)
    found = tokens(actual)
    missing = wanted - found
    total = sum(wanted.values())
    return {"expected_words": total, "missing_words": sum(missing.values()),
            "retained_fraction": (total - sum(missing.values())) / total if total else 1.0,
            "missing_sample": missing.most_common(25)}


def expected_text(document: Document) -> str:
    result = []
    for block in document.blocks:
        text = block.caption if block.asset else block.text
        for span in sorted((s for s in block.spans if s.get("asset")), key=lambda x: x["start"], reverse=True):
            text = text[:span["start"]] + " " + text[span["end"]:]
        result.append(text)
    return "\n".join(result)


def html_text(element) -> str:
    """Keep inline words intact while respecting HTML block boundaries."""
    tag = element.tag.rsplit("}", 1)[-1]
    block = tag in {"p", "div", "section", "aside", "figure", "figcaption", "li",
                    "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "td", "th"}
    if tag == "img":
        return " "
    result = ["\n" if block else "", element.text or ""]
    for child in element:
        result.extend([html_text(child), child.tail or ""])
    if block:
        result.append("\n")
    return "".join(result)


def validate_pdf(path: Path, document: Document) -> dict:
    from pypdf import PdfReader

    pdf = PdfReader(path)
    if not pdf.pages:
        raise ValueError("The reading PDF has no pages.")
    # Poppler handles kerning such as `To` and `Tr` without introducing word spaces.
    poppler = shutil.which("pdftotext")
    if poppler:
        text = subprocess.check_output([poppler, str(path), "-"], encoding="utf-8")
        pages = text.split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        if len(pages) != len(pdf.pages):
            raise ValueError("PDF text page count differs from the PDF page tree.")
    else:
        pages = [p.extract_text() or "" for p in pdf.pages]
        text = "\n".join(pages)
    if any(not p.strip() for p in pages):
        raise ValueError("The reading PDF contains an empty page.")
    coverage = text_coverage(expected_text(document), text)
    return {"pages": len(pdf.pages), "searchable_text": True, "text_coverage": coverage,
            "sha256": digest(path), "bytes": path.stat().st_size}


def validate_epub(path: Path, document: Document) -> dict:
    namespaces = {"opf": "http://www.idpf.org/2007/opf", "html": "http://www.w3.org/1999/xhtml"}
    with zipfile.ZipFile(path) as archive:
        if archive.namelist()[0] != "mimetype" or archive.read("mimetype") != b"application/epub+zip":
            raise ValueError("Invalid EPUB mimetype member.")
        if archive.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
            raise ValueError("The EPUB mimetype must be uncompressed.")
        names = set(archive.namelist())
        if len(names) != len(archive.namelist()):
            raise ValueError("Duplicate EPUB archive entries.")
        roots = {n: ET.fromstring(archive.read(n)) for n in names if n.endswith((".xhtml", ".opf", ".xml"))}
        package = roots["OEBPS/package.opf"]
        cover_items = package.findall('opf:manifest/opf:item[@properties="cover-image"]', namespaces)
        if len(cover_items) != 1 or archive.read("OEBPS/" + cover_items[0].attrib["href"])[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Missing or invalid EPUB cover image.")
        spine_ids = [e.attrib["idref"] for e in package.findall("opf:spine/opf:itemref", namespaces)]
        if spine_ids[:2] != ["cover", "nav"]:
            raise ValueError("EPUB cover and visible contents must precede the paper.")
        identifiers = {item.attrib["id"] for item in package.findall("opf:manifest/opf:item", namespaces)}
        for ref in package.findall("opf:spine/opf:itemref", namespaces):
            if ref.attrib["idref"] not in identifiers:
                raise ValueError("Missing spine target.")
        for item in package.findall("opf:manifest/opf:item", namespaces):
            target = posixpath.normpath("OEBPS/" + item.attrib["href"])
            if target not in names:
                raise ValueError(f"Missing EPUB manifest resource: {target}")
        links = 0
        for name, root in roots.items():
            if not name.endswith(".xhtml"):
                continue
            ids = [e.attrib["id"] for e in root.iter() if "id" in e.attrib]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate XHTML IDs in {name}")
            for element in root.iter():
                for attribute in ("href", "src"):
                    value = element.attrib.get(attribute)
                    if not value:
                        continue
                    parsed = urlsplit(value)
                    if parsed.scheme or parsed.netloc:
                        continue
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(parsed.path))) if parsed.path else name
                    if target not in names:
                        raise ValueError(f"Broken EPUB link: {name} -> {value}")
                    if parsed.fragment:
                        ids = {e.attrib.get("id") for e in roots[target].iter()}
                        if unquote(parsed.fragment) not in ids:
                            raise ValueError(f"Broken EPUB anchor: {name} -> {value}")
                    links += 1
        text = "\n".join(html_text(roots[n].find("html:body", namespaces))
            for n in sorted(roots) if n.startswith("OEBPS/section-") and n.endswith(".xhtml"))
        coverage = text_coverage(expected_text(document), text)
        if coverage["retained_fraction"] < 1:
            raise ValueError(f"Text was lost in EPUB export: {coverage}")
    return {"container_valid": True, "cover_valid": True, "visible_contents": True,
            "links_checked": links, "text_coverage": coverage,
            "sha256": digest(path), "bytes": path.stat().st_size}


def audit_source(source: Path, document: Document) -> dict:
    import pdfplumber

    by_page: dict[int, list] = {}
    for block in document.blocks:
        for region in [*block.regions, *block.caption_regions]:
            by_page.setdefault(region.page, []).append(region.bbox)
    for item in document.removed:
        for region in item.get("regions", []):
            by_page.setdefault(region["page"], []).append(region["bbox"])
    pages = []
    with open_native_pdf(source) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            boxes = by_page.get(number, [])
            missing, accounted, total = [], 0, 0
            for char in page.chars:
                if not char["text"].strip():
                    continue
                total += 1
                x, y = (char["x0"] + char["x1"]) / 2, (char["top"] + char["bottom"]) / 2
                if any(l - 2 <= x <= r + 2 and t - 2 <= y <= b + 2 for l, t, r, b in boxes):
                    accounted += 1
                else:
                    missing.append(char["text"])
            pages.append({"page": number, "native_characters": total, "accounted_characters": accounted,
                "region_coverage": accounted / max(1, total), "unaccounted_text": "".join(missing)[:500]})
    return {"pages": pages, "all_pages_represented": all(p["page"] in by_page for p in pages),
            "native_characters": sum(p["native_characters"] for p in pages),
            "accounted_characters": sum(p["accounted_characters"] for p in pages)}
