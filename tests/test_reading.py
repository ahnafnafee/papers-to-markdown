"""Reading-copy regressions using small documents and bounded native geometry."""

import json
import base64
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from reading.cli import select_inputs
from reading.epub import plain_segments, render_epub, text_html, block_html, xhtml
from reading.extract import _native_format, _prepare_fragments, _group_panels, _ink_box, clean_text, item_text, select_title
from reading.model import Block, Document, ReadingProfile, Region, atomic_json, digest, document_assets
from reading.pdf import tex_escape, text_tex
from reading.pipeline import receipt_matches
from reading.validate import expected_text, validate_epub, validate_pdf, html_text, text_coverage


def document(blocks):
    return Document("a" * 64, "source.pdf", "Reading test", ["Example Author"], 1, blocks)


class DocumentTests(unittest.TestCase):
    def test_provenance_and_caption_regions_roundtrip(self):
        region = Region(1, [10, 20, 100, 30], [612, 792], "81")
        doc = document([Block("b1", "paragraph", "Complete text", regions=[region], caption_regions=[region])])
        self.assertEqual(Document.from_dict(doc.to_dict()), doc)

    def test_reject_duplicate_blocks(self):
        doc = document([Block("b1", "paragraph", "A"), Block("b1", "paragraph", "B")])
        with self.assertRaises(ValueError):
            Document.from_dict(doc.to_dict())

    def test_reject_unsupported_document(self):
        value = document([Block("b1", "paragraph", "A")]).to_dict()
        value["schema_version"] = 999
        with self.assertRaises(ValueError):
            Document.from_dict(value)

    def test_invalid_profile_does_not_reach_renderer(self):
        for values in ({"font_size": 0}, {"line_spacing": 4}, {"font_size": float("nan")},
                       {"page_width": 1}, {"paragraph_spacing": -1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ReadingProfile(**values)

    def test_ligature_and_accent_repairs_preserve_math_alphabet(self):
        self.assertEqual(clean_text("\ufb01nd L\u00b4eon Sch\u00a8olkopf \U0001d44b"), "find Léon Schölkopf \U0001d44b")

    def test_reference_list_markers_are_retained_once(self):
        self.assertEqual(item_text(SimpleNamespace(text="Original reference", marker="[12]")), "[12] Original reference")
        self.assertEqual(item_text(SimpleNamespace(text="[12] Original reference", marker="[12]")), "[12] Original reference")


class InputAndReceiptTests(unittest.TestCase):
    def test_recursive_selection_deduplicates_bytes_and_excludes_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder, contents in (("a", b"one"), ("b", b"two"), ("copy", b"one"),
                                     ("_incoming", b"incoming"), ("outputs", b"output")):
                (root / folder).mkdir()
                (root / folder / "same.PDF").write_bytes(contents)
            selected = select_inputs([root], recursive=True, exclusions=["_incoming"], output_root=root / "outputs")
            self.assertEqual(len(selected), 2)
            self.assertEqual({p.read_bytes() for p in selected}, {b"one", b"two"})

    def test_nonrecursive_selection_does_not_descend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "nested" / "paper.pdf").write_bytes(b"source")
            self.assertEqual(select_inputs([root]), [])

    def test_completion_receipt_detects_asset_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "assets" / "figure.png"
            asset.parent.mkdir()
            asset.write_bytes(b"complete figure")
            atomic_json(root / "assets-manifest.json", {"assets/figure.png": digest(asset)})
            request = {"source_sha256": "unchanged"}
            atomic_json(root / "conversion-report.json", {"complete": True, "request": request,
                "outputs": {"assets": {"filename": "assets-manifest.json", "sha256": digest(root / "assets-manifest.json")}}})
            self.assertTrue(receipt_matches(root, request))
            asset.write_bytes(b"partial")
            self.assertFalse(receipt_matches(root, request))

    def test_partial_receipt_never_counts_as_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json(root / "conversion-report.json", {"complete": False, "request": {}, "outputs": {}})
            self.assertFalse(receipt_matches(root, {}))


class NativeNotationTests(unittest.TestCase):
    def test_regular_computer_modern_prose_stays_reflowable(self):
        pdf = self.native("Regular body text")
        for char in pdf.pages[0].crop(None).extract_words()[0]["chars"]:
            char["fontname"] = "ABCDEF+CMR10"
        with patch("reading.extract._crop") as crop:
            text, spans = _native_format("Regular body text", [Region(1, [0, 0, 200, 100], [612, 792])],
                                         pdf, block_id="body", cache=Path("unused"))
        self.assertEqual(text, "Regular body text")
        self.assertFalse(any(s.get("asset") for s in spans))
        crop.assert_not_called()

    def test_metadata_title_is_matched_to_real_paper_heading(self):
        blocks = [Block("journal", "title", "Example University Journal", regions=[Region(1, [0, 10, 300, 25], [612, 792])]),
                  Block("paper", "heading", "The Impact of Difficulty on Player Experience", regions=[Region(1, [0, 100, 300, 120], [612, 792])])]
        title = select_title(blocks, Path("5. The Impact of Difficulty on Player Experience.pdf"), {})
        self.assertEqual(title, blocks[1].text)
        self.assertEqual(blocks[1].kind, "title")

    def test_missing_layout_symbol_uses_literal_native_unicode(self):
        text, _ = _native_format("Z = \ufffd i", [Region(1, [0, 0, 200, 100], [612, 792])],
                                 self.native("Z = \u2211 i"))
        self.assertEqual(text, "Z = \u2211 i")

    def test_inline_crop_uses_glyph_outline_instead_of_shifted_font_box(self):
        char = {"x0": 20, "top": 80, "x1": 30, "bottom": 90,
                "matrix": (1, 0, 0, 1, 20, 712)}
        with tempfile.NamedTemporaryFile() as source:
            pdf = SimpleNamespace(stream=SimpleNamespace(name=source.name),
                                  pages=[SimpleNamespace(height=792)])
            with patch("reading.extract._page_ink_boxes", return_value={
                    (200, 800): [((20, 80), [20.5, 74, 29.5, 80.1])]}):
                self.assertEqual(_ink_box(char, 1, pdf), [20.5, 74, 29.5, 80.1])

    def test_adjacent_figure_panels_share_their_original_numbered_caption(self):
        blocks = [Block("f1", "figure", regions=[Region(1, [10, 10, 50, 50], [612, 792])], asset="a.png"),
            Block("f2", "figure", regions=[Region(1, [55, 10, 100, 50], [612, 792])], asset="b.png"),
            Block("c1", "caption", "Figure 1: Original comparison.",
                  regions=[Region(1, [10, 55, 100, 70], [612, 792])])]
        page = SimpleNamespace(crop=lambda box: SimpleNamespace(extract_words=lambda:
            [{"text": "Figure", "top": 55}]))
        with patch("reading.extract._crop") as crop:
            result = _group_panels(blocks, SimpleNamespace(pages=[page]), Path("."))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].caption, "Figure 1: Original comparison.")
        self.assertEqual(result[0].regions[0].bbox, [10, 10, 100, 50])
        crop.assert_called_once()

    def test_plot_caption_and_axis_label_do_not_interrupt_body_paragraph(self):
        class Item(SimpleNamespace):
            def model_copy(self, *, update):
                return Item(**{**self.__dict__, **update})

        def provenance(box, span):
            bbox = SimpleNamespace(to_top_left_origin=lambda page_height:
                SimpleNamespace(l=box[0], t=box[1], r=box[2], b=box[3]))
            return SimpleNamespace(page_no=1, bbox=bbox, charspan=span)

        fragments = ["This depends on the quality of", "Number of Points", "Figure 4: Original caption."]
        text = " ".join(fragments)
        offset, prov = 0, []
        for fragment, box in zip(fragments, ([50, 680, 295, 710], [330, 215, 380, 220], [315, 225, 560, 250])):
            prov.append(provenance(box, (offset, offset + len(fragment))))
            offset += len(fragment) + 1
        paragraph = Item(self_ref="#/text/1", text=text, prov=prov,
                         label=SimpleNamespace(value="text"))
        picture = Item(self_ref="#/pictures/1", prov=[provenance([315, 80, 560, 221], (0, 0))], captions=[])
        doc = SimpleNamespace(texts=[paragraph], pictures=[picture], tables=[])
        pdf = SimpleNamespace(pages=[SimpleNamespace(width=612, height=792)])
        overrides, captions, labels = _prepare_fragments(doc, pdf)
        self.assertEqual(overrides[paragraph.self_ref][0], fragments[0])
        self.assertEqual(captions[picture.self_ref][0][0], fragments[2])
        self.assertEqual(labels[picture.self_ref][0][0], fragments[1])

    @staticmethod
    def native(text, stacked=False, subscript=False):
        chars = []
        for i, letter in enumerate(text.replace(" ", "")):
            size, x, top, baseline = 10, 10 + i * 7, 70, 78
            if stacked and i < 2:
                size, x = 7, 10
                top, baseline = ((70, 76) if i == 0 else (78, 84))
            if subscript and i == 1:
                size, top, baseline = 7, 76, 82
            chars.append({"text": letter, "fontname": "Example-Regular", "size": size,
                          "x0": x, "x1": x + 5, "top": top, "bottom": top + size,
                          "matrix": (1, 0, 0, 1, x, 792 - baseline)})
        lines = [{"x0": 10, "x1": 15, "top": 77, "bottom": 77}] if stacked else []
        page = SimpleNamespace(height=792, lines=lines,
            crop=lambda box: SimpleNamespace(extract_words=lambda **kwargs: [{"chars": chars}]))
        return SimpleNamespace(pages=[page])

    def test_stacked_fraction_requires_source_rule(self):
        text, _ = _native_format("12 items", [Region(1, [0, 0, 200, 100], [612, 792])],
                                 self.native("12 items", stacked=True))
        self.assertEqual(text, "1/2 items")

    def test_ordinary_twelve_is_not_a_fraction(self):
        text, _ = _native_format("12 items", [Region(1, [0, 0, 200, 100], [612, 792])],
                                 self.native("12 items"))
        self.assertEqual(text, "12 items")

    def test_small_lowered_character_retains_subscript(self):
        text, spans = _native_format("x2 items", [Region(1, [0, 0, 200, 100], [612, 792])],
                                     self.native("x2 items", subscript=True))
        self.assertEqual(text, "x2 items")
        self.assertTrue(any(s["sub"] for s in spans if "sub" in s))

    def test_superscript_inside_model_name_is_not_a_note_reference(self):
        pdf = self.native("D4LCN")
        chars = pdf.pages[0].crop(None).extract_words()[0]["chars"]
        chars[1].update(size=7, top=66, bottom=73, x1=22.4,
                        matrix=(1, 0, 0, 1, 17, 719))
        _, spans = _native_format("D4LCN", [Region(1, [0, 0, 200, 100], [612, 792])], pdf)
        script = next(s for s in spans if s["start"] == 1)
        self.assertTrue(script["sup"])
        self.assertTrue(script["notation"])


class ExportTests(unittest.TestCase):
    def test_table_only_document_has_no_missing_prose(self):
        self.assertEqual(text_coverage("", "")['retained_fraction'], 1)
        self.assertEqual(text_coverage("", "Metadata")['missing_words'], 0)
        self.assertLess(text_coverage("Original content", "Original")['retained_fraction'], 1)

    def test_native_pdf_check_keeps_page_and_content_checks_without_duplicate_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reading.pdf"
            path.write_bytes(b"test fixture")
            reader = SimpleNamespace(pages=[object(), object()])
            with patch.dict("sys.modules", {"pypdf": SimpleNamespace(PdfReader=lambda path: reader)}), \
                 patch("reading.validate.shutil.which", return_value="pdftotext"), \
                 patch("reading.validate.subprocess.check_output", return_value="Original body\fSecond page\f"):
                result = validate_pdf(path, document([Block("p", "paragraph", "Original body")]))
            self.assertEqual(result["pages"], 2)
            self.assertEqual(result["text_coverage"]["retained_fraction"], 1)

    def test_heading_source_crop_is_used_instead_of_broken_characters(self):
        block = Block("h1", "heading", "2 \x07 Method", asset="assets/heading.png",
                      asset_role="source_text", asset_width_pt=50, asset_text_size=10)
        html = block_html(block, {}, {}, set(range(128)), set())
        self.assertIn('<img src="assets/heading.png"', html)
        self.assertNotIn("\x07", html)
        self.assertIn("width:5.000em", html)

    def test_empty_or_control_only_page_title_has_a_valid_label(self):
        from xml.etree import ElementTree as ET
        root = ET.fromstring(xhtml("\x07\uffff", "<p>Original body</p>"))
        self.assertEqual(root.find("{http://www.w3.org/1999/xhtml}head/{http://www.w3.org/1999/xhtml}title").text, "Paper")

    def test_epub_notes_link_both_ways_across_sections(self):
        doc = document([Block("h1", "heading", "Introduction"),
            Block("p1", "paragraph", "An observation1", spans=[{"start": 14, "end": 15,
                "sup": True, "note_target": "fn1", "note_anchor": "note-p1-14"}]),
            Block("h2", "heading", "Notes"),
            Block("fn1", "footnote", "1 The original explanation.",
                  backlinks=[{"block": "p1", "anchor": "note-p1-14"}])])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "reading.epub"
            font = root / "installed.ttf"
            font.write_bytes(b"installed font" * 100)
            fonts = {k: font for k in ("regular", "bold", "italic", "bolditalic")}
            with patch("reading.epub.cmap", return_value=set(range(128))), \
                 patch("reading.epub.cover_png", return_value=PNG):
                render_epub(doc, root, target, ReadingProfile(), fonts)
            self.assertTrue(validate_epub(target, doc)["container_valid"])

    def test_html_block_boundaries_do_not_join_note_backlinks_into_urls(self):
        from xml.etree import ElementTree as ET
        root = ET.fromstring('<aside><p>https://example.org/9roTh3hDCW8</p><p>Return to text</p></aside>')
        self.assertIn("9roTh3hDCW8\n", html_text(root))
        root = ET.fromstring('<p>A super<em>script</em> and <img src="math.png"/> value.</p>')
        self.assertIn("superscript", html_text(root))

    def test_extracted_tex_is_literal(self):
        result = tex_escape(r"50% & $x_1$ {text} \write18{bad}")
        self.assertIn(r"\textbackslash{}write18\{bad\}", result)
        self.assertIn(r"50\% \& \$x\_1\$", result)

    def test_html_text_is_escaped(self):
        b = Block("b1", "paragraph", '<script>alert("x")</script> & text')
        html = text_html(b, {}, {}, set(range(128)), set())
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_inline_crop_is_not_repeated_by_nested_emphasis(self):
        b = Block("b1", "paragraph", "x2", spans=[{"start": 1, "end": 2, "sub": True},
            {"start": 0, "end": 2, "asset": "assets/math.png", "width_em": 1,
             "height_em": 1, "depth_em": .2}])
        html = text_html(b, {}, {}, set(range(128)), set())
        self.assertEqual(html.count("<img"), 1)
        tex = text_tex(b, {}, set(range(128)), set(), Path("."))
        self.assertEqual(tex.count("includegraphics"), 1)
        self.assertEqual(document_assets(document([b])), {"assets/math.png"})
        self.assertEqual(expected_text(document([b])).strip(), "")

    def test_epub_cross_section_citations_and_obfuscated_fonts(self):
        doc = document([Block("h1", "heading", "Introduction"),
            Block("p1", "paragraph", "An observation [7] & its evidence."),
            Block("h2", "heading", "References"),
            Block("r7", "reference", "[7] Example Author. Evidence. 2026.")])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            font = root / "installed.ttf"
            original = bytes(range(256)) * 8
            font.write_bytes(original)
            fonts = {k: font for k in ("regular", "bold", "italic", "bolditalic")}
            target = root / "reading.epub"
            with patch("reading.epub.cmap", return_value=set(range(128))), \
                 patch("reading.epub.cover_png", return_value=PNG):
                render_epub(doc, root, target, ReadingProfile(), fonts)
            result = validate_epub(target, doc)
            self.assertEqual(result["text_coverage"]["retained_fraction"], 1)
            with zipfile.ZipFile(target) as archive:
                self.assertIn(b'section-002.xhtml#r7', archive.read("OEBPS/section-001.xhtml"))
                embedded = bytearray(archive.read("OEBPS/fonts/regular.ttf"))
                import hashlib
                key = hashlib.sha1(("urn:sha256:" + doc.source_hash).encode()).digest()
                for i in range(1040):
                    embedded[i] ^= key[i % 20]
                self.assertEqual(bytes(embedded), original)
                from xml.etree import ElementTree as ET
                opf = ET.fromstring(archive.read("OEBPS/package.opf"))
                ns = {"o": "http://www.idpf.org/2007/opf"}
                self.assertEqual([e.attrib["idref"] for e in opf.findall("o:spine/o:itemref", ns)][:2], ["cover", "nav"])
                self.assertIn(b'section-002.xhtml#h2', archive.read("OEBPS/nav.xhtml"))


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jScsAAAAASUVORK5CYII=")


if __name__ == "__main__":
    unittest.main()
