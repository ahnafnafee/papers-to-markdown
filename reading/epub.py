"""EPUB 3 and local HTML from the same document used for PDF rendering."""

from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .cover import cover_png
from .fonts import cmap
from .model import Block, Document, ReadingProfile, source_label, document_assets


def sections(document: Document) -> list[list[Block]]:
    result: list[list[Block]] = []
    current: list[Block] = []
    for block in document.blocks:
        if block.kind == "heading" and block.level <= 2 and current:
            result.append(current)
            current = []
        current.append(block)
    if current:
        result.append(current)
    return result


def citation_targets(document: Document) -> dict[str, str]:
    result = {}
    for block in document.blocks:
        if block.kind == "reference":
            match = re.match(r"\s*\[(\d+)\]", block.text)
            if match:
                result[match[1]] = block.id
    return result


def plain_segments(block: Block) -> list[tuple[str, dict]]:
    cuts = {0, len(block.text)}
    for span in block.spans:
        cuts.update((max(0, span["start"]), min(len(block.text), span["end"])))
    cuts = {p for p in cuts if not any(s["start"] < p < s["end"] for s in block.spans if s.get("asset"))}
    positions = sorted(cuts)
    result = []
    for a, b in zip(positions, positions[1:]):
        style = {}
        for span in block.spans:
            if span["start"] <= a and span["end"] >= b:
                style.update({k: v for k, v in span.items() if k not in ("start", "end")})
        result.append((block.text[a:b], style))
    return result


def text_html(block: Block, targets: dict[str, str], locations: dict[str, str],
              body_cmap: set[int], symbol_cmap: set[int]) -> str:
    def characters(text: str) -> str:
        return "".join(
            '<span class="symbol">' + escape(c) + "</span>"
            if ord(c) not in body_cmap and ord(c) in symbol_cmap else escape(c)
            for c in text
        )

    def citations(text: str) -> str:
        result = []
        previous = 0
        for match in re.finditer(r"\[(\d+(?:\s*[,;\u2013-]\s*\d+)*)\]", text):
            result.append(characters(text[previous:match.start()]))
            label = match.group(1)
            pieces = re.split(r"(\d+)", label)
            links = []
            for piece in pieces:
                target = targets.get(piece)
                if target and target != block.id:
                    links.append(f'<a href="{escape(locations[target], quote=True)}">{piece}</a>')
                else:
                    links.append(characters(piece))
            result.append("[" + "".join(links) + "]")
            previous = match.end()
        result.append(characters(text[previous:]))
        return "".join(result)

    result = []
    for text, style in plain_segments(block):
        if style.get("asset"):
            part = (f'<img src="{escape(style["asset"], quote=True)}" '
                'alt="Original mathematical notation" '
                f'style="width:{style["width_em"]:.4f}em;height:{style["height_em"]:.4f}em;'
                f'vertical-align:-{style["depth_em"]:.4f}em;" />')
        else:
            part = citations(text)
            for flag, tag in (("italic", "em"), ("bold", "strong"), ("sup", "sup"), ("sub", "sub")):
                if style.get(flag):
                    part = f"<{tag}>{part}</{tag}>"
        if style.get("note_target"):
            part = (f'<a epub:type="noteref" id="{style["note_anchor"]}" '
                    f'href="{locations[style["note_target"]]}">{part}</a>')
        result.append(part)
    return "".join(result)


def style_sheet(profile: ReadingProfile, embedded: bool = False) -> str:
    faces = ""
    if embedded:
        for filename, weight, style in (
            ("regular.ttf", "normal", "normal"), ("bold.ttf", "bold", "normal"),
            ("italic.ttf", "normal", "italic"), ("bolditalic.ttf", "bold", "italic"),
        ):
            faces += (f'@font-face {{font-family: Verdana; src: url("fonts/{filename}"); '
                      f'font-weight: {weight}; font-style: {style};}}\n')
        faces += '@font-face {font-family: ReadingSymbols; src: url("fonts/symbols.otf");}\n'
    return faces + f"""
body {{ font-family: Verdana, sans-serif; font-size: 1em;
  line-height: {profile.line_spacing / profile.font_size:.5f};
  margin: 0; padding: 0; text-align: left; }}
p {{ margin: 0 0 {profile.paragraph_spacing / profile.font_size:.5f}em; text-indent: 0; }}
h1, h2, h3, h4 {{ text-align: left; line-height: 1.3; margin: 1.2em 0 .6em;
  break-after: avoid; page-break-after: avoid; }}
h1 {{ font-size: 1.5em; }} h2 {{ font-size: 1.18em; }} h3 {{ font-size: 1.08em; }}
figure {{ margin: 1em 0; padding: 0; break-inside: avoid; page-break-inside: avoid; }}
img {{ max-width: 100%; height: auto; }}
figure img {{ display: block; margin: .3em auto; }}
figcaption {{ margin: .5em 0; line-height: 1.5; }}
.source {{ font-size: .72em; margin: 1.2em 0 .4em; }}
.note {{ font-size: .9em; }} .symbol {{ font-family: ReadingSymbols, serif; }}
table {{ border-collapse: collapse; width: 100%; margin: .8em 0; }}
td, th {{ border-bottom: 1px solid; padding: .3em; text-align: left; vertical-align: top; }}
pre {{ white-space: pre-wrap; }}
a {{ overflow-wrap: anywhere; }}
nav ol {{ padding-left: 1.3em; }} nav li {{ margin: .55em 0; }}
.cover {{ margin: 0; padding: 0; text-align: center; }}
.cover img {{ width: 100%; max-height: 100vh; object-fit: contain; }}
"""


def block_html(block: Block, targets: dict[str, str], locations: dict[str, str],
               body_cmap: set[int], symbol_cmap: set[int]) -> str:
    attrs = f'id="{block.id}"'
    text = text_html(block, targets, locations, body_cmap, symbol_cmap)
    if block.kind in {"title", "heading"} and not block.asset:
        level = 1 if block.kind == "title" else min(6, block.level + 1)
        return f"<h{level} {attrs}>{text}</h{level}>"
    if block.asset:
        caption_block = Block(block.id + "c", "caption", text=block.caption, spans=block.spans)
        caption_text = text_html(caption_block, targets, locations, body_cmap, symbol_cmap)
        caption = f"<figcaption>{caption_text}</figcaption>" if block.caption else ""
        alt = block.caption or ("Original " + block.kind + "; " + source_label(block))
        image_style = f"width:{block.asset_width_pt * 2 / 10:.3f}em;max-width:100%;"
        if block.kind == "equation" or block.asset_role == "source_text":
            image_style = f"width:{block.asset_width_pt / (block.asset_text_size or 10):.3f}em;max-width:100%;"
        image = (f'<img src="{escape(block.asset, quote=True)}" alt="{escape(display_label(alt), quote=True)}" '
                 f'style="{image_style}" />')
        return f"<figure {attrs}>{image}{caption}</figure>"
    if block.kind == "footnote":
        links = " ".join('<a href="' + locations[b["block"]].split("#")[0] + "#" + b["anchor"]
                         + '">' + (str(i + 1) if len(block.backlinks) > 1 else "Return to text") + '</a>'
                         for i, b in enumerate(block.backlinks))
        if len(block.backlinks) > 1:
            links = "Return to text: " + links
        return f'<aside {attrs} epub:type="footnote" class="note"><p>{text}</p><p>{links}</p></aside>'
    if block.kind == "table" and block.table_html:
        return f"<div {attrs}>{block.table_html}</div>"
    return f"<p {attrs}>{text}</p>"


def xhtml(title: str, body: str) -> str:
    title = display_label(title).strip() or "Paper"
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">'
            f'<head><title>{escape(title)}</title><link rel="stylesheet" '
            'type="text/css" href="reading.css" /></head><body>' + body + '</body></html>')


def display_label(text: str) -> str:
    # Parser control codes cannot appear in XML navigation or metadata. The
    # affected body passage is preserved separately as an original source crop.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", text)


def toc_entries(document: Document, locations: dict[str, str]) -> list[dict]:
    entries = []
    first = document.blocks[0]
    if first.kind != "heading":
        entries.append({"title": "Paper", "href": locations[first.id], "level": 1})
    for block in document.blocks:
        if block.kind == "heading":
            entries.append({"title": display_label(block.text).strip() or "Section", "href": locations[block.id],
                            "level": max(1, min(6, block.level))})
    return entries


def toc_html(entries: list[dict]) -> str:
    roots, stack = [], []
    for entry in entries:
        node = {**entry, "children": []}
        while stack and stack[-1]["level"] >= node["level"]:
            stack.pop()
        (stack[-1]["children"] if stack else roots).append(node)
        stack.append(node)

    def render(nodes):
        return "<ol>" + "".join('<li><a href="' + escape(n["href"], quote=True)
            + '">' + escape(n["title"]) + '</a>'
            + (render(n["children"]) if n["children"] else "") + '</li>' for n in nodes) + "</ol>"

    return render(roots)


def render_epub(document: Document, cache: Path, output: Path, profile: ReadingProfile,
                fonts: dict[str, Path], embed_fonts: bool = True) -> None:
    body_cmap = cmap(fonts["regular"])
    symbol_cmap = cmap(fonts["symbols"]) if "symbols" in fonts else set()
    chunks = sections(document)
    names = [f"section-{i+1:03}.xhtml" for i in range(len(chunks))]
    locations = {b.id: f"{name}#{b.id}" for name, chunk in zip(names, chunks) for b in chunk}
    targets = citation_targets(document)
    book_title = display_label(document.title).strip() or "Research paper"
    manifest = ['<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
                '<item id="cover-image" href="cover.png" media-type="image/png" properties="cover-image"/>',
                '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="reading.css" media-type="text/css"/>']
    spine = ['<itemref idref="cover"/>', '<itemref idref="nav"/>']
    navigation = toc_entries(document, locations)
    uid = "urn:sha256:" + document.source_hash
    font_key = hashlib.sha1(uid.encode("utf-8")).digest()
    encryption = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", '<?xml version="1.0"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/package.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>')
        css = style_sheet(profile, embedded=embed_fonts)
        if "symbols" not in fonts:
            css = re.sub(r'@font-face \{font-family: ReadingSymbols;[^}]+\}\n', "", css)
        archive.writestr("OEBPS/reading.css", css)
        archive.writestr("OEBPS/cover.png", cover_png(book_title, [display_label(a) for a in document.authors], fonts))
        archive.writestr("OEBPS/cover.xhtml", xhtml("Cover", '<section class="cover" epub:type="cover">'
            '<img src="cover.png" alt="Cover: ' + escape(book_title, quote=True) + '" /></section>'))
        for index, (name, chunk) in enumerate(zip(names, chunks)):
            title = next((b.text for b in chunk if b.kind in {"title", "heading"} and b.text.strip()), document.title)
            content, seen_pages = [], set()
            for block in chunk:
                pages = {r.page for r in block.regions}
                if pages - seen_pages:
                    content.append(f'<p class="source">{escape(source_label(block))}</p>')
                    seen_pages.update(pages)
                content.append(block_html(block, targets, locations, body_cmap, symbol_cmap))
            archive.writestr("OEBPS/" + name, xhtml(title, "\n".join(content)))
            manifest.append(f'<item id="s{index}" href="{name}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="s{index}"/>')
        for asset in sorted(document_assets(document)):
            archive.write(cache / asset, "OEBPS/" + asset)
            manifest.append(f'<item id="a{len(manifest)}" href="{asset}" media-type="image/png"/>')
        if embed_fonts:
            for style, path in fonts.items():
                suffix = ".otf" if style == "symbols" else ".ttf"
                member = f"OEBPS/fonts/{style}{suffix}"
                data = bytearray(path.read_bytes())
                # IDPF font obfuscation binds the embedded document fonts to this publication.
                for i in range(min(1040, len(data))):
                    data[i] ^= font_key[i % len(font_key)]
                archive.writestr(member, bytes(data))
                manifest.append(f'<item id="font-{style}" href="fonts/{style}{suffix}" '
                                f'media-type="font/{"otf" if style == "symbols" else "ttf"}"/>')
                encryption.append('<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">'
                    '<EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
                    f'<CipherData><CipherReference URI="{member}"/></CipherData></EncryptedData>')
            archive.writestr("META-INF/encryption.xml", '<?xml version="1.0"?>'
                '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                + "".join(encryption) + '</encryption>')
        nav = '<nav epub:type="toc" id="toc"><h1>Contents</h1>' + toc_html(navigation) + '</nav>'
        nav += ('<nav epub:type="landmarks" hidden="hidden"><h2>Reading order</h2><ol>'
                '<li><a epub:type="cover" href="cover.xhtml">Cover</a></li>'
                '<li><a epub:type="toc" href="nav.xhtml#toc">Contents</a></li>'
                f'<li><a epub:type="bodymatter" href="{names[0]}">Paper</a></li></ol></nav>')
        archive.writestr("OEBPS/nav.xhtml", xhtml("Contents", nav))
        points = ''.join(f'<navPoint id="n{i}" playOrder="{i+1}"><navLabel><text>'
            + escape(e['title']) + '</text></navLabel><content src="' + escape(e['href'], quote=True)
            + '"/></navPoint>' for i, e in enumerate(navigation))
        archive.writestr("OEBPS/toc.ncx", '<?xml version="1.0" encoding="utf-8"?>'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f'<head><meta name="dtb:uid" content="{uid}"/><meta name="dtb:depth" content="1"/>'
            '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>'
            f'<docTitle><text>{escape(book_title)}</text></docTitle><navMap>{points}</navMap></ncx>')
        authors = "".join(f"<dc:creator>{escape(display_label(a))}</dc:creator>" for a in document.authors)
        modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        archive.writestr("OEBPS/package.opf", '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:identifier id="book-id">{uid}</dc:identifier><dc:title>{escape(book_title)}</dc:title>'
            f'<dc:language>en</dc:language>{authors}<dc:source>{escape(document.source_name)}</dc:source>'
            f'<meta property="dcterms:modified">{modified}</meta><meta name="cover" content="cover-image"/>'
            '</metadata><manifest>' + "".join(manifest) + '</manifest><spine toc="ncx">'
            + "".join(spine) + '</spine><guide><reference type="cover" title="Cover" href="cover.xhtml"/>'
            '<reference type="toc" title="Contents" href="nav.xhtml"/></guide></package>')


def render_html(document: Document, cache: Path, output: Path, profile: ReadingProfile,
                fonts: dict[str, Path]) -> None:
    body_cmap = cmap(fonts["regular"])
    symbol_cmap = cmap(fonts["symbols"]) if "symbols" in fonts else set()
    targets = citation_targets(document)
    locations = {b.id: "#" + b.id for b in document.blocks}
    body, seen_pages = [], set()
    for block in document.blocks:
        pages = {r.page for r in block.regions}
        if pages - seen_pages:
            body.append(f'<p class="source">{escape(source_label(block))}</p>')
            seen_pages.update(pages)
        body.append(block_html(block, targets, locations, body_cmap, symbol_cmap))
    text = xhtml(document.title, "\n".join(body))
    text = text.replace('<link rel="stylesheet" type="text/css" href="reading.css" />',
        '<style>' + style_sheet(profile) + f'\nbody {{max-width: {profile.width_pt}pt; '
        f'font-size:{profile.font_size}pt; margin:3em auto; padding:0 1.5em;}}' + '</style>')
    output.write_text(text, encoding="utf-8")
    for asset in document_assets(document):
        destination = output.parent / asset
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cache / asset, destination)
