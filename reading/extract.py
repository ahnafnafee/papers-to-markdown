"""Local layout-aware extraction with native text, provenance, and original crops."""

from __future__ import annotations

import importlib.metadata
import json
import re
import sys
import time
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache, wraps
from pathlib import Path
from threading import Lock

from .model import Block, Document, Region, atomic_json, digest, fingerprint, document_assets
from .source import open_native_pdf

ADAPTER_VERSION = "10"
_converters: dict[tuple, object] = {}


@lru_cache(maxsize=4)
def _page_ink_boxes(path: str, page_no: int, modified: int) -> dict:
    """Index tight glyph bounds by their source text origins, not font metrics."""
    import ctypes
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    result = {}
    with pdfium.PdfDocument(path) as document:
        page = document[page_no - 1]
        text = page.get_textpage()
        try:
            height = page.get_height()
            for index in range(text.count_chars()):
                left, bottom, right, top = text.get_charbox(index)
                if right <= left or top <= bottom:
                    continue
                x, y = ctypes.c_double(), ctypes.c_double()
                if not raw.FPDFText_GetCharOrigin(text, index, ctypes.byref(x), ctypes.byref(y)):
                    continue
                origin = (x.value, height - y.value)
                key = tuple(round(v * 10) for v in origin)
                result.setdefault(key, []).append((origin, [left, height - top, right, height - bottom]))
        finally:
            text.close()
            page.close()
    return result


def _ink_box(char: dict, page_no: int, pdf) -> list[float]:
    fallback = [char[k] for k in ("x0", "top", "x1", "bottom")]
    path = getattr(getattr(pdf, "stream", None), "name", None)
    if not isinstance(path, (str, Path)):
        return fallback
    boxes = _page_ink_boxes(str(path), page_no, Path(path).stat().st_mtime_ns)
    origin = (char["matrix"][4], pdf.pages[page_no - 1].height - char["matrix"][5])
    key = tuple(round(v * 10) for v in origin)
    candidates = [entry for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                  for entry in boxes.get((key[0] + dx, key[1] + dy), [])
                  if max(abs(a - b) for a, b in zip(entry[0], origin)) < .12]
    if not candidates:
        return fallback
    # An accent may share its origin with a base glyph; keep both outlines.
    return [min(b[0] for _, b in candidates), min(b[1] for _, b in candidates),
            max(b[2] for _, b in candidates), max(b[3] for _, b in candidates)]


def clean_text(value: str) -> str:
    # Expand presentation ligatures, without compatibility-normalizing mathematical symbols.
    value = value.translate(str.maketrans({"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
        "\ufb03": "ffi", "\ufb04": "ffl", "\u00ad": ""}))
    accents = {"\u00b4": "\u0301", "\u00a8": "\u0308", "\u02c6": "\u0302"}
    value = re.sub(r"([\u00b4\u00a8\u02c6])([A-Za-z])",
        lambda m: unicodedata.normalize("NFC", m[2] + accents[m[1]]), value)
    return re.sub(r"\s+", " ", value).strip()


def item_text(item) -> str:
    text = clean_text(getattr(item, "text", ""))
    marker = clean_text(getattr(item, "marker", ""))
    if marker and not text.startswith(marker):
        text = marker + " " + text
    return text


def _native_format(text: str, regions: list[Region], pdf, *, block_id: str = "", cache: Path | None = None) -> tuple[str, list[dict]]:
    """Recover emphasis and scripts through literal alignment and native baselines.

    A stacked digit fraction is repaired only when the PDF also contains its
    horizontal fraction rule. This prevents interpreting ordinary `12` as `1/2`.
    """
    units = []
    for region in regions:
        page = pdf.pages[region.page - 1]
        box = tuple(region.bbox)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        words = page.crop(box).extract_words(extra_attrs=["fontname", "size"], return_chars=True)
        for word in words:
            for char in word["chars"]:
                for letter in clean_text(char["text"]):
                    if not letter.isspace():
                        units.append((letter, char, region.page))
    native = "".join(x[0] for x in units).replace("\u2212", "-")
    positions = [i for i, c in enumerate(text) if not c.isspace()]
    compact = "".join(text[i] for i in positions).replace("\u2212", "-")
    if "\ufffd" in compact:
        repairs = []
        for tag, a, b, c, d in SequenceMatcher(None, native, compact, autojunk=False).get_opcodes():
            replacement = native[a:b]
            if (tag == "replace" and re.fullmatch(r"\ufffd+", compact[c:d]) and replacement
                    and not any(unicodedata.category(x) in {"Cc", "Co", "Cs"} for x in replacement)
                    and "\ufffd" not in replacement and "cid:" not in replacement):
                repairs.append((positions[c], positions[d - 1] + 1, replacement))
        for a, b, replacement in reversed(repairs):
            text = text[:a] + replacement + text[b:]
        positions = [i for i, c in enumerate(text) if not c.isspace()]
        compact = "".join(text[i] for i in positions).replace("\u2212", "-")
    aligned = {}
    for match in SequenceMatcher(None, native, compact, autojunk=False).get_matching_blocks():
        for k in range(match.size):
            aligned[match.a + k] = positions[match.b + k]
    sizes = [u[1]["size"] for u in units if u[0].isalpha()]
    typical = sorted(sizes)[len(sizes) // 2] if sizes else 10
    normal = [u for u in units if u[1]["size"] >= typical * .9]
    styles = {}
    for index, (letter, char, page_no) in enumerate(units):
        if index not in aligned:
            continue
        name = char["fontname"].lower()
        style = {"italic": bool(re.search(r"italic|oblique|cmmi|(?:^|[-+])[^-]*-it", name)),
                 "bold": bool(re.search(r"bold|semibold|demi|cmbx", name))}
        if char["size"] < typical * .9:
            page = pdf.pages[page_no - 1]
            baseline = page.height - char["matrix"][5]
            peers = [u[1] for u in normal if u[2] == page_no and
                     abs((page.height - u[1]["matrix"][5]) - baseline) < typical]
            if peers:
                parent = min(peers, key=lambda c: abs(c["x0"] - char["x0"]) * .08
                             + abs((page.height - c["matrix"][5]) - baseline))
                difference = baseline - (page.height - parent["matrix"][5])
                if difference < -1:
                    style["sup"] = True
                    # A script inside an identifier (e.g. D^4LCN) is notation,
                    # even when a footnote happens to use the same number.
                    parent_baseline = page.height - parent["matrix"][5]
                    if any(u[0].isalnum() and u[2] == page_no
                           and -.2 < u[1]["x0"] - char["x1"] < typical * .2
                           and abs(page.height - u[1]["matrix"][5] - parent_baseline) < .8
                           for u in normal):
                        style["notation"] = True
                elif difference > 1:
                    style["sub"] = True
                if style.get("sup") or style.get("sub"):
                    style["source_page"] = page_no
        styles[aligned[index]] = style
    insertions = set()
    for i, (numerator, upper, page_no) in enumerate(units):
        if not numerator.isdigit() or i not in aligned or upper["size"] >= typical * .9:
            continue
        for j in range(i + 1, min(i + 8, len(units))):
            denominator, lower, other_page = units[j]
            if (other_page != page_no or j not in aligned or not denominator.isdigit()
                    or abs(lower["x0"] - upper["x0"]) > 1
                    or not (2 < lower["top"] - upper["top"] < typical * 1.3)
                    or aligned[j] != aligned[i] + 1):
                continue
            page = pdf.pages[page_no - 1]
            rule = any(abs(line["bottom"] - line["top"]) < .8
                       and line["x0"] <= upper["x0"] + 1 and line["x1"] >= upper["x1"] - 1
                       and upper["top"] + upper["size"] * .5 < line["top"] < lower["bottom"]
                       for line in page.lines)
            if rule:
                insertions.add(aligned[j])
                for p in (aligned[i], aligned[j]):
                    styles[p].pop("sup", None)
                    styles[p].pop("sub", None)
                break
    cuts = sorted(insertions)
    for position in reversed(cuts):
        text = text[:position] + "/" + text[position:]
    spans = []
    for position, style in sorted(styles.items()):
        if not any(style.values()):
            continue
        start = position + bisect_right(cuts, position)
        end = position + 1 + bisect_left(cuts, position + 1)
        if spans and spans[-1]["end"] == start and all(spans[-1].get(k) == v for k, v in style.items()) and set(spans[-1]) - {"start", "end"} == set(style):
            spans[-1]["end"] = end
        else:
            spans.append({"start": start, "end": end, **style})
    if cache is not None and block_id:
        # Reuse actual glyph outlines for mathematical fonts with unreliable Unicode
        # maps. Native characters still establish exact text offsets and geometry.
        math_font = re.compile(r"cmmi|cmsy|cmex|msbm|msam|math|txsy|txmi|txex|newtxmi|standardsym|symbol", re.I)
        rows = {}
        for index, (letter, char, page_no) in enumerate(units):
            page = pdf.pages[page_no - 1]
            baseline = page.height - char["matrix"][5]
            if char["size"] < typical * .9:
                peers = [u[1] for u in normal if u[2] == page_no and
                         abs((page.height - u[1]["matrix"][5]) - baseline) < typical]
                if peers:
                    parent = min(peers, key=lambda c: abs(c["x0"] - char["x0"]) * .08
                        + abs((page.height - c["matrix"][5]) - baseline))
                    baseline = page.height - parent["matrix"][5]
            rows.setdefault((page_no, round(baseline / 1.5)), []).append((index, letter, char))
        images = []
        for (page_no, _), row in rows.items():
            row.sort(key=lambda u: u[2]["x0"])
            groups, group = [], []
            for unit in row:
                index, letter, char = unit
                eligible = bool(math_font.search(char["fontname"])) or letter in "0123456789()[]{}+-=*/<>|.,:"
                if (not eligible or (group and char["x0"] - max(u[2]["x1"] for u in group) > typical * .6)):
                    if group:
                        groups.append(group)
                    group = []
                if eligible:
                    group.append(unit)
            if group:
                groups.append(group)
            for group in groups:
                if not any(math_font.search(u[2]["fontname"]) for u in group):
                    continue
                mapped = [aligned[u[0]] for u in group if u[0] in aligned]
                if not mapped:
                    continue
                a, b = min(mapped), max(mapped) + 1
                a += bisect_right(cuts, a)
                b += bisect_left(cuts, b)
                # Do not absorb intervening prose when a glyph could not be aligned.
                if b - a > sum(len(u[1]) for u in group) + 6:
                    continue
                chars = [u[2] for u in group]
                page = pdf.pages[page_no - 1]
                ink = [_ink_box(c, page_no, pdf) for c in chars]
                region = Region(page_no, [min(b[0] for b in ink), min(b[1] for b in ink),
                    max(b[2] for b in ink), max(b[3] for b in ink)],
                    [float(page.width), float(page.height)])
                image = Block(f"{block_id}-inline-{len(images):03}", "equation", regions=[region])
                _crop(image, pdf, cache, padding=.35)
                body_baseline = sum(page.height - c["matrix"][5] for c in chars if c["size"] >= typical * .9)
                count = sum(c["size"] >= typical * .9 for c in chars)
                if count:
                    body_baseline /= count
                else:
                    body_baseline = max(page.height - c["matrix"][5] for c in chars)
                images.append({"start": a, "end": b, "asset": image.asset,
                    "source_page": page_no,
                    "width_em": image.asset_width_pt / typical,
                    "height_em": image.asset_height_pt / typical,
                    "depth_em": max(0, region.bbox[3] + .35 - body_baseline) / typical})
        # A superscript may be discovered in the same expression as its base.
        # Retain one opaque image per non-overlapping source expression.
        selected = []
        for image in sorted(images, key=lambda x: (x["start"], -(x["end"] - x["start"]))):
            if selected and image["start"] < selected[-1]["end"]:
                continue
            selected.append(image)
        spans.extend(selected)
    return text, spans


def _regions(item, doc, pdf) -> list[Region]:
    regions = []
    for prov in getattr(item, "prov", []):
        page = pdf.pages[prov.page_no - 1]
        box = prov.bbox.to_top_left_origin(page_height=float(page.height))
        coords = [max(0, box.l), max(0, box.t), min(float(page.width), box.r),
                  min(float(page.height), box.b)]
        if coords[2] <= coords[0] or coords[3] <= coords[1]:
            # Empty parser markers have no crop geometry. Keep their literal
            # text and mark the absent provenance in normalization instead.
            continue
        regions.append(Region(prov.page_no, coords, [float(page.width), float(page.height)]))
    return regions


@lru_cache(maxsize=2)
def _page_image(path: str):
    from PIL import Image

    with Image.open(path) as image:
        return image.convert("RGB")


def _crop(block: Block, pdf, cache: Path, padding: float = 1.5) -> None:
    from PIL import Image

    images, widths, heights = [], [], []
    scale = 300 / 72
    for region in block.regions:
        page = pdf.pages[region.page - 1]
        page_path = cache / "pages" / f"page-{region.page:03}.png"
        if not page_path.is_file():
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page.to_image(resolution=300, antialias=True).original.convert("RGB").save(page_path)
        l, t, r, b = region.bbox
        l, t, r, b = max(0, l - padding), max(0, t - padding), min(page.width, r + padding), min(page.height, b + padding)
        crop = _page_image(str(page_path)).crop((int(l * scale), int(t * scale), int(r * scale + .5), int(b * scale + .5)))
        images.append(crop)
        widths.append(r - l)
        heights.append(b - t)
    if not images:
        raise ValueError(f"No source location for visual block {block.id}")
    combined = Image.new("RGB", (max(i.width for i in images), sum(i.height for i in images)), "white")
    y = 0
    for image in images:
        combined.paste(image, (0, y))
        y += image.height
    block.asset = f"assets/{block.id}.png"
    target = cache / block.asset
    target.parent.mkdir(parents=True, exist_ok=True)
    combined.save(target, optimize=True)
    block.asset_width_pt = max(widths)
    block.asset_height_pt = sum(heights)


def _prepare_fragments(doc, pdf) -> tuple[dict, dict, dict]:
    """Separate captions and plot labels accidentally joined into a body paragraph."""
    visuals = {i.self_ref: _regions(i, doc, pdf) for i in [*doc.pictures, *doc.tables]}
    linked = {r.cref for i in [*doc.pictures, *doc.tables] for r in i.captions}
    overrides, captions, labels = {}, {}, {}
    for item in doc.texts:
        if item.self_ref in linked or item.label.value in {"page_header", "page_footer"}:
            continue
        kept_text, kept_regions, changed = [], [], False
        for prov in item.prov:
            a, b = prov.charspan
            fragment = item.text[a:b] if len(item.prov) > 1 else item.text
            areas = _regions(item.model_copy(update={"prov": [prov]}), doc, pdf)
            if not areas:
                kept_text.append(fragment)
                continue
            region = areas[0]
            l, t, r, bottom = region.bbox
            caption = bool(re.match(r"^\s*(?:Figure|Fig\.|Table)\s*\d+\s*[.:]", fragment))
            candidates = []
            for reference, areas in visuals.items():
                for area in areas:
                    if area.page != region.page:
                        continue
                    x0, y0, x1, y1 = area.bbox
                    overlap = max(0, min(r, x1) - max(l, x0)) * max(0, min(bottom, y1) - max(t, y0))
                    ratio = overlap / max(.01, (r - l) * (bottom - t))
                    if caption and -3 <= t - y1 <= 90 and x0 - 20 <= (l + r) / 2 <= x1 + 20:
                        candidates.append((max(0, t - y1), reference, "caption"))
                    elif (len(item.prov) > 1 and len(fragment) < 80 and bottom - t < 20 and ratio > .5):
                        candidates.append((0, reference, "label"))
            if candidates:
                _, reference, kind = min(candidates)
                (captions if kind == "caption" else labels).setdefault(reference, []).append((fragment, region))
                changed = True
            else:
                kept_text.append(fragment)
                kept_regions.append(region)
        if changed:
            overrides[item.self_ref] = (clean_text(" ".join(kept_text)), kept_regions)
    return overrides, captions, labels


_FIGURE_CAPTION = re.compile(r"\b(?:Figure|Fig\.)\s*\d+\s*[-\u2013\u2014:.]", re.I)


def _group_panels(blocks: list[Block], pdf, cache: Path) -> list[Block]:
    """Keep consecutive panels belonging to one original numbered figure together."""
    for anchor in list(blocks):
        caption = anchor.caption if anchor.kind == "figure" else anchor.text
        match = _FIGURE_CAPTION.search(caption)
        if not match or (anchor.kind not in {"caption", "figure", "paragraph"}):
            continue
        if anchor.kind != "figure" and match.start() > 100:
            continue
        index = blocks.index(anchor)
        chosen, labels = ([anchor] if anchor.kind == "figure" else []), []
        preceding = index - 1
        page = anchor.regions[0].page if anchor.regions else 0
        while preceding >= 0:
            candidate = blocks[preceding]
            if not candidate.regions or any(r.page != page for r in candidate.regions):
                break
            if candidate.kind == "figure" and not _FIGURE_CAPTION.search(candidate.caption):
                chosen.append(candidate)
            elif candidate.kind in {"caption", "paragraph"} and re.match(r"^\([a-z]\)", candidate.text):
                labels.append(candidate)
            else:
                break
            preceding -= 1
        if not chosen or (len(chosen) == 1 and anchor.kind == "figure"):
            continue
        areas = [r for b in chosen for r in b.regions] + [r for b in labels for r in b.regions]
        if not areas or len({r.page for r in areas}) != 1:
            continue
        caption_regions = anchor.caption_regions if anchor.kind == "figure" else anchor.regions
        main_regions, sub_regions = [], []
        for region in caption_regions:
            words = pdf.pages[region.page - 1].crop(tuple(region.bbox)).extract_words()
            first = next((w for w in words if re.match(r"^(?:Figure\d*|Fig\.)$", w["text"], re.I)), None)
            if first:
                top = first["top"]
                main_regions.append(Region(region.page, [region.bbox[0], top, region.bbox[2], region.bbox[3]], region.page_size))
                if top > region.bbox[1] + 3:
                    sub_regions.append(Region(region.page, [region.bbox[0], region.bbox[1], region.bbox[2], top - .2], region.page_size))
            else:
                sub_regions.append(region)
        if not main_regions:
            main_regions, sub_regions = list(caption_regions), []
        areas.extend(sub_regions)
        for candidate in chosen:
            if candidate is not anchor:
                areas.extend(candidate.caption_regions)
        combined = Region(page, [min(r.bbox[0] for r in areas), min(r.bbox[1] for r in areas),
            max(r.bbox[2] for r in areas), max(r.bbox[3] for r in areas)], areas[0].page_size)
        first = min(chosen, key=blocks.index)
        replacement = Block(first.id, "figure", regions=[combined], caption_regions=main_regions,
            caption=caption[match.start():], spans=[{**s, "start": s["start"] - match.start(),
            "end": s["end"] - match.start()} for s in anchor.spans if s["start"] >= match.start()])
        _crop(replacement, pdf, cache)
        removed_ids = {b.id for b in [*chosen, *labels, anchor]}
        insertion = min(blocks.index(b) for b in chosen)
        blocks = blocks[:insertion] + [replacement] + [b for b in blocks[insertion:] if b.id not in removed_ids]
    return blocks


def select_title(blocks: list[Block], source: Path, metadata: dict) -> str:
    """Match the supplied filename or PDF metadata to an actual heading."""
    def words(value):
        return set(re.findall(r"[^\W\d_]+", clean_text(value).casefold()))

    hints = [re.sub(r"^\d+[.\s_-]+", "", source.stem).replace("_", " "), metadata.get("Title") or ""]
    scored = []
    for block in blocks:
        if block.kind not in {"title", "heading"} or not block.regions or block.regions[0].page > 5:
            continue
        candidate = words(block.text)
        if len(candidate) < 2:
            continue
        for hint in hints:
            expected = words(hint)
            overlap = candidate & expected
            if len(expected) >= 2 and len(overlap) / len(expected) >= .65:
                score = 2 * len(overlap) / (len(candidate) + len(expected))
                scored.append((score, -block.regions[0].page, block))
    if scored:
        block = max(scored, key=lambda entry: entry[:2])[2]
        block.kind, block.level = "title", 1
        # A PDF's ligature cells can introduce spaces inside words. Repair only
        # spacing in a prefix whose letters match a supplied title exactly.
        pretty = block.text
        compact = re.sub(r"\s+", "", pretty).casefold()
        for hint in reversed(hints):
            expected = re.sub(r"\s+", "", hint).casefold()
            if len(expected) > 8 and len(expected) >= len(compact) * .7 and compact.startswith(expected):
                positions = [i for i, c in enumerate(pretty) if not c.isspace()]
                pretty = hint.strip() + pretty[positions[len(expected) - 1] + 1:]
                break
        if pretty != block.text and all(not s.get("asset") and s["start"] == 0
                                       and s["end"] == len(block.text) for s in block.spans):
            block.text = pretty
            for span in block.spans:
                span["end"] = len(pretty)
        return pretty
    return next((b.text for b in blocks if b.kind == "title"), hints[0])


def normalize(raw: dict, source: Path, cache: Path, extraction: dict) -> Document:
    import pdfplumber
    from docling_core.types.doc import DoclingDocument

    doc = DoclingDocument.model_validate(raw)
    blocks, removed, review = [], [], []
    caption_refs = {r.cref for p in [*doc.pictures, *doc.tables] for r in p.captions}
    items = list(doc.iterate_items())
    with open_native_pdf(source) as pdf:
        metadata = pdf.metadata or {}
        native_path = Path(pdf.stream.name)
        if native_path.resolve() != source.resolve():
            extraction = {**extraction, "native_input_repair": {
                "copy": str(native_path), "copy_sha256": digest(native_path),
                "original_sha256": digest(source), "proof": str(native_path.with_suffix(".json"))}}
        authors = [re.sub(r"^(?:and\s+)|[\d*\u00b9\u00b2\u00b3]+$", "", x.strip()).strip()
                   for x in re.split(r",\s*|\s+and\s+", metadata.get("Author") or "") if x.strip()]
        authors = [a for a in authors if a and a.casefold() not in
                   {"user", "administrator", "admin", "author", "unknown", "adobe", "microsoft"}
                   and not re.search(r"\b(?:IEEE|Member|Contributors)\b", a)]
        for item in doc.texts:
            if item.label.value in {"page_header", "page_footer"}:
                removed.append({"kind": item.label.value, "text": clean_text(item.text),
                    "regions": [r.__dict__ for r in _regions(item, doc, pdf)]})
        overrides, extra_captions, extra_labels = _prepare_fragments(doc, pdf)
        continuation_ids = set()
        for number, (item, tree_level) in enumerate(items):
            if not hasattr(item, "label"):
                continue
            label = item.label.value
            text = item_text(item)
            regions = _regions(item, doc, pdf)
            if item.self_ref in overrides:
                text, regions = overrides[item.self_ref]
                if not text:
                    continue
                continuation_ids.add(f"b{number:05}")
            if label in {"page_header", "page_footer"}:
                removed.append({"kind": label, "text": text, "pages": [r.page for r in regions],
                                "regions": [r.__dict__ for r in regions]})
                continue
            if item.self_ref in caption_refs:
                continue
            if not regions and re.fullmatch(r"[.\u00b7\u2022\s]*", text):
                removed.append({"kind": "empty_source_marker", "text": text,
                                "reason": "Parser marker has zero source area", "regions": []})
                continue
            kind = {"section_header": "heading", "picture": "figure", "formula": "equation",
                    "list_item": "paragraph", "text": "paragraph"}.get(label, label)
            if kind not in {"title", "heading", "figure", "table", "equation", "caption", "footnote", "reference"}:
                kind = "paragraph"
            block = Block(id=f"b{number:05}", kind=kind, text=text, regions=regions)
            if kind == "heading":
                prefix = re.match(r"^(\d+(?:\.\d+)*)\s", text)
                block.level = prefix.group(1).count(".") + 1 if prefix else getattr(item, "level", 1)
            if kind in {"figure", "table", "equation"}:
                block.caption = clean_text(item.caption_text(doc)) if hasattr(item, "caption_text") else ""
                for ref in getattr(item, "captions", []):
                    block.caption_regions.extend(_regions(ref.resolve(doc), doc, pdf))
                additions = extra_captions.get(item.self_ref, [])
                if additions:
                    block.caption = clean_text(" ".join([block.caption, *(t for t, r in additions)]))
                    block.caption_regions.extend(r for t, r in additions)
                for label_text, label_region in extra_labels.get(item.self_ref, []):
                    for region in block.regions:
                        if region.page == label_region.page:
                            region.bbox = [min(region.bbox[0], label_region.bbox[0]),
                                min(region.bbox[1], label_region.bbox[1]),
                                max(region.bbox[2], label_region.bbox[2]),
                                max(region.bbox[3], label_region.bbox[3])]
                if kind == "table":
                    block.table_html = item.export_to_html(doc=doc, add_caption=False)
                _crop(block, pdf, cache)
                if block.caption and block.caption_regions:
                    block.caption, block.spans = _native_format(block.caption, block.caption_regions,
                        pdf, block_id=block.id + "-caption", cache=cache)
            elif text:
                block.text, block.spans = _native_format(text, regions, pdf, block_id=block.id, cache=cache)
                formatting = getattr(item, "formatting", None)
                if formatting:
                    flags = {k: getattr(formatting, k, False) for k in ("bold", "italic")}
                    if any(flags.values()):
                        block.spans.append({"start": 0, "end": len(text), **flags})
            else:
                continue
            if not regions:
                review.append({"block": block.id, "reason": "No source location"})
            visible_text = block.caption if block.asset else block.text
            for span in sorted((s for s in block.spans if s.get("asset")), key=lambda s: s["start"], reverse=True):
                visible_text = visible_text[:span["start"]] + " " + visible_text[span["end"]:]
            if "\ufffd" in visible_text or re.search(r"\(cid:\d+\)", visible_text):
                review.append({"block": block.id, "reason": "Unresolved source glyph; inspect original"})
            blocks.append(block)
        blocks = _group_panels(blocks, pdf, cache)
        title = select_title(blocks, source, metadata)
        for block in list(blocks):
            if block.id not in continuation_ids or not block.text or block.text[-1] in ".!?;:":
                continue
            index = blocks.index(block) + 1
            while index < len(blocks) and blocks[index].kind in {"figure", "table", "caption", "equation"}:
                index += 1
            if index < len(blocks):
                following = blocks[index]
                if following.kind == "paragraph" and following.text and following.text[0].islower():
                    offset = len(block.text) + 1
                    block.text += " " + following.text
                    block.regions.extend(following.regions)
                    block.spans.extend({**s, "start": s["start"] + offset, "end": s["end"] + offset}
                                       for s in following.spans)
                    blocks.pop(index)
        # The detector sometimes places a two-column abstract before the full-width
        # title, or splits the author columns around it. Order this bounded prelude
        # by its actual first-page geometry, retaining body reading order elsewhere.
        abstract = next((b for b in blocks if b.kind == "heading" and b.text.casefold() == "abstract"), None)
        if abstract and abstract.regions:
            top = min(r.bbox[1] for r in abstract.regions)
            prelude = [b for b in blocks if b.regions and all(r.page == 1 and r.bbox[3] < top for r in b.regions)]
            prelude.sort(key=lambda b: (round(min(r.bbox[1] for r in b.regions) / 4), min(r.bbox[0] for r in b.regions)))
            if prelude:
                title_block = prelude[0]
                title_block.kind, title_block.level = "title", 0
                titles = [title_block.text]
                for block in prelude[1:]:
                    if block.kind == "heading":
                        block.kind = "paragraph"
                prelude_ids = {b.id for b in prelude}
                blocks = prelude + [b for b in blocks if b.id not in prelude_ids]
        # A full-width prelude can be placed after the left body column by the
        # layout model. Move only content physically above the first body section.
        first_section = next((b for b in blocks if b.kind == "heading" and b.regions
            and b.regions[0].page == 1 and re.match(r"^(?:1[.\s]+|(?:I[.\s]+)?INTRODUCTION\b)", b.text, re.I)), None)
        if first_section:
            boundary = min(r.bbox[1] for r in first_section.regions)
            prelude = [b for b in blocks if b.regions and all(r.page == 1 and r.bbox[3] <= boundary + 1 for r in b.regions)]
            prelude.sort(key=lambda b: (0 if b.kind == "title" and b.text == title else 1,
                                        min(r.bbox[1] for r in b.regions), min(r.bbox[0] for r in b.regions)))
            ids = {b.id for b in prelude}
            blocks = prelude + [b for b in blocks if b.id not in ids]
        in_references = False
        running_headers = {clean_text(entry["text"]).casefold() for entry in removed if entry["kind"] == "page_header"}
        repeated = [b for b in blocks if b.regions and b.text.casefold() in running_headers
                    and all(r.page > 1 and r.bbox[1] < r.page_size[1] * .12 for r in b.regions)]
        for block in repeated:
            removed.append({"kind": "repeated_running_header", "text": block.text,
                            "regions": [r.__dict__ for r in block.regions]})
        blocks = [b for b in blocks if b not in repeated]
        for block in blocks:
            if block.kind == "heading":
                if re.fullmatch(r"(?:\d+\s+)?(?:references|bibliography)", block.text, re.I):
                    in_references = True
                elif in_references:
                    in_references = False
            elif in_references and block.kind == "paragraph":
                block.kind = "reference"
        labels = {region["page"]: entry["text"] for entry in removed
                  if entry["kind"] == "page_footer" and re.fullmatch(r"\d+|[ivxlcdm]+", entry["text"], re.I)
                  for region in entry["regions"]}
        for block in blocks:
            for region in [*block.regions, *block.caption_regions]:
                region.printed_label = labels.get(region.page, "")
        notes = {}
        for block in blocks:
            match = re.match(r"^\s*(\d+|[*\u2217\u2020\u2021])", block.text)
            if block.kind == "footnote" and match and block.regions:
                mark = match[1].replace("\u2217", "*")
                notes[(block.regions[0].page, mark)] = block
        for block in blocks:
            if block.kind in {"footnote", "reference"} or block.asset:
                continue
            for span in block.spans:
                if span.get("notation"):
                    continue
                mark = block.text[span["start"]:span["end"]].strip().replace("\u2217", "*")
                if not re.fullmatch(r"\d+|[*\u2020\u2021]", mark) or not (span.get("sup") or span.get("asset")):
                    continue
                if span.get("asset") and mark.isdigit():
                    continue
                if not span.get("asset") and any(s.get("asset") and s["start"] <= span["start"]
                        and span["end"] <= s["end"] for s in block.spans):
                    continue
                page = span.get("source_page", block.regions[0].page if block.regions else 0)
                note = notes.get((page, mark))
                if note:
                    span["note_target"] = note.id
                    span["note_anchor"] = f"note-{block.id}-{span['start']}"
                    note.backlinks.append({"block": block.id, "anchor": span["note_anchor"]})
        extraction = {**extraction, "adapter_version": ADAPTER_VERSION}
        result = Document(
            source_hash=digest(source), source_name=source.name,
            title=title,
            authors=authors, pages=len(pdf.pages), blocks=blocks,
            removed=removed, review=review, extraction=extraction,
        )
    represented = {r.page for b in blocks for r in b.regions}
    for page in sorted(set(range(1, result.pages + 1)) - represented):
        result.review.append({"page": page, "reason": "No reading content extracted from this page"})
    if not result.blocks:
        raise ValueError("The PDF produced no reading content.")
    return result


def cache_location(source: Path, work: Path, ocr: str, device: str) -> Path:
    source_hash = digest(source)
    version = importlib.metadata.version("docling")
    key = fingerprint({"docling": version, "ocr": ocr, "device": device})
    return work / source_hash / key[:16]


def cached_document(source_hash: str, cache: Path) -> Document | None:
    cached_doc = cache / "document.json"
    if cached_doc.is_file():
        document = Document.from_dict(json.loads(cached_doc.read_text(encoding="utf-8")))
        if (document.source_hash == source_hash and document.extraction.get("adapter_version") == ADAPTER_VERSION
                and all((cache / asset).is_file() for asset in document_assets(document))):
            return document
    return None


@lru_cache(maxsize=1)
def _configure_hub_cache() -> None:
    """Serialize the pinned Hub client's Windows symlink capability checks."""
    if sys.platform != "win32":
        return
    from huggingface_hub import file_download

    original_check = file_download.are_symlinks_supported
    lock = Lock()

    @wraps(original_check)
    def checked(cache_dir=None):
        # Hub 0.36.2 publishes a provisional True before its probe finishes.
        # Parallel snapshot downloads can read it and fail with WinError 1314.
        with lock:
            return original_check(cache_dir)

    file_download.are_symlinks_supported = checked


def prepare_extraction(source: Path, work: Path, *, force: bool = False,
                       ocr: str = "auto", device: str = "auto") -> Path:
    """The only stage that loads inference models; safe to isolate in one process."""
    source_hash = digest(source)
    version = importlib.metadata.version("docling")
    cache = cache_location(source, work, ocr, device)
    if not force and cached_document(source_hash, cache):
        return cache
    cache.mkdir(parents=True, exist_ok=True)
    raw_path = cache / "docling.json"
    extraction_path = cache / "extraction.json"
    if not force and raw_path.is_file() and extraction_path.is_file():
        return cache
    else:
        _configure_hub_cache()
        import pdfplumber
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        with open_native_pdf(source) as pdf:
            sparse = []
            for i, page in enumerate(pdf.pages):
                body = [c for c in page.chars if page.height * .1 < (c["top"] + c["bottom"]) / 2 < page.height * .9]
                large_bitmap = any(image["width"] * image["height"] > page.width * page.height * .35
                                   for image in page.images)
                if len(page.chars) < 32 or (len(body) < 32 and large_bitmap):
                    sparse.append(i + 1)
        use_ocr = ocr == "always" or (ocr == "auto" and bool(sparse))
        options = PdfPipelineOptions(do_ocr=use_ocr, do_table_structure=True,
            generate_page_images=False, enable_remote_services=False)
        options.document_timeout = 240
        if ocr == "always":
            options.ocr_options.force_full_page_ocr = True
            options.ocr_options.lang = ["iso:en"]
        options.accelerator_options = AcceleratorOptions(device=AcceleratorDevice(device), num_threads=4)
        options.table_batch_size = 2
        parser = "docling"
        if (cache / "parser-fallback.json").is_file():
            parser = "pdfium"
        converter_key = (use_ocr, device, parser)
        if converter_key not in _converters:
            if _converters:
                _converters.clear()
                import gc
                import torch
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            backend = {}
            if parser == "pdfium":
                from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
                backend["backend"] = PyPdfiumDocumentBackend
            _converters[converter_key] = DocumentConverter(format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=options, **backend),
            })
        started = time.perf_counter()
        result = _converters[converter_key].convert(source)
        if result.status.value != "success":
            raise RuntimeError(f"Incomplete extraction ({result.status}): {result.errors}")
        if digest(source) != source_hash:
            raise RuntimeError("The source changed during extraction; retry after syncing completes.")
        raw = result.document.export_to_dict()
        extraction = {"backend": "docling", "version": version, "adapter_version": ADAPTER_VERSION,
            "ocr_used": use_ocr, "sparse_pages": sparse, "device": device, "pdf_parser": parser,
            "elapsed_seconds": time.perf_counter() - started,
            "options": options.model_dump(mode="json"),
            "errors": [str(e) for e in result.errors]}
        atomic_json(raw_path, raw)
        atomic_json(extraction_path, extraction)
    return cache


def extract(source: Path, work: Path, *, force: bool = False, ocr: str = "auto",
            device: str = "auto", allow_inference: bool = True) -> tuple[Document, Path, bool]:
    cache = cache_location(source, work, ocr, device)
    if not force:
        document = cached_document(digest(source), cache)
        if document is not None:
            return document, cache, True
    if allow_inference:
        cache = prepare_extraction(source, work, force=force, ocr=ocr, device=device)
    if not (cache / "docling.json").is_file() or not (cache / "extraction.json").is_file():
        raise RuntimeError("Raw extraction is missing; run the inference stage before CPU rendering.")
    raw = json.loads((cache / "docling.json").read_text(encoding="utf-8"))
    extraction = json.loads((cache / "extraction.json").read_text(encoding="utf-8"))
    document = normalize(raw, source, cache, extraction)
    atomic_json(cache / "document.json", document.to_dict())
    return document, cache, False
