"""Queue isolation, top-level library scope, and complete EPUB navigation."""

import json
import io
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

from reading.batch import run_batch
from reading.cli import main as reading_main
from reading.epub import toc_entries, toc_html
from reading.library import publish_paper
from reading.model import Block, Document, ReadingProfile, digest
from reading.pipeline import preserve_source_glyphs
from reading.source import open_native_pdf


class ImmediatePool:
    def __init__(self, *args, **kwargs):
        self._processes = {}

    def shutdown(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as exception:
            future.set_exception(exception)
        return future


class BatchTests(unittest.TestCase):
    def run_delivery(self, documents, sender_error=None):
        errors = io.StringIO()
        with patch("reading.cli.select_inputs", return_value=[Path("first.pdf"), Path("second.pdf")]), \
             patch("reading.batch.run_batch", return_value={"failed": 0, "documents": documents}), \
             patch("kindle_delivery.launch.send_epubs", side_effect=sender_error, return_value=0) as sender, \
             redirect_stdout(io.StringIO()), redirect_stderr(errors):
            result = reading_main(["papers", "--workers", "2", "--send-to-kindle"])
        return result, sender, errors.getvalue()

    def test_parallel_delivery_reports_review_skips_as_incomplete(self):
        result, sender, errors = self.run_delivery({"first.pdf": {"status": "needs_review"}})
        self.assertEqual(result, 1)
        sender.assert_not_called()
        self.assertIn("Kindle delivery skipped until review: first.pdf", errors)

    def test_parallel_delivery_sends_ready_books_and_reports_review_skips(self):
        result, sender, errors = self.run_delivery({
            "first.pdf": {"status": "needs_review"},
            "second.pdf": {"status": "ready", "folder": "output/second",
                           "outputs": {"epub": {"filename": "second.epub"}}}})
        self.assertEqual(result, 1)
        sender.assert_called_once_with([Path("output/second/second.epub")], state_dir=None)
        self.assertIn("Kindle delivery skipped until review: first.pdf", errors)

    def test_parallel_delivery_reports_sender_errors_without_a_traceback(self):
        result, sender, errors = self.run_delivery({
            "first.pdf": {"status": "ready", "folder": "output/first",
                          "outputs": {"epub": {"filename": "first.epub"}}}},
            sender_error=RuntimeError("Sign-in is required"))
        self.assertEqual(result, 1)
        sender.assert_called_once()
        self.assertIn("Kindle: Sign-in is required", errors)

    def test_complete_native_page_tree_uses_the_original_input(self):
        pdf = SimpleNamespace(pages=[object(), object()])
        modules = {"pypdf": SimpleNamespace(PdfReader=lambda *a, **kw: SimpleNamespace(
                       is_encrypted=False, pages=[object(), object()]), PdfWriter=object),
                   "pdfplumber": SimpleNamespace(open=lambda path: pdf)}
        with patch.dict("sys.modules", modules), patch("reading.source.digest") as hashed:
            self.assertIs(open_native_pdf(Path("original.pdf")), pdf)
        hashed.assert_not_called()

    def test_native_timeout_restarts_only_inference_and_retries_with_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "paper.pdf"
            source.write_bytes(b"source")
            cache = root / "cache"
            calls = []

            def prepare(path, *args):
                calls.append(path.name)

            class HangingFirstPool(ImmediatePool):
                submitted = False

                def submit(self, function, *args):
                    if function is prepare_mock and not self.submitted:
                        HangingFirstPool.submitted = True
                        return Future()
                    return super().submit(function, *args)

            report = {"status": "ready", "title": "Paper", "source_pages": 1,
                      "review": [], "outputs": {}}
            with patch("reading.batch.ProcessPoolExecutor", HangingFirstPool), \
                 patch("reading.batch._prepare", side_effect=prepare) as prepare_mock, \
                 patch("reading.batch._render", return_value=(str(root), report)), \
                 patch("reading.extract.cache_location", return_value=cache), \
                 patch("reading.batch.time.monotonic", side_effect=[0, 2, 2]), \
                 patch("reading.batch.wait", side_effect=lambda active, **kw: ({f for f in active if f.done()}, set())):
                state = run_batch([source], root / "out", cache, ReadingProfile(), extraction_timeout=1)
            self.assertEqual(state["finished"], 1)
            self.assertEqual(state["failed"], 0)
            self.assertEqual(json.loads((cache / "parser-fallback.json").read_text())["backend"], "pdfium")

    def test_bad_glyph_is_kept_as_a_legible_source_crop(self):
        block = Block("bad", "paragraph", "Original \x00 \uffff text")
        from reading.model import Region
        block.regions = [Region(1, [0, 0, 100, 20], [612, 792])]
        note = Block("note", "footnote", "Original note", backlinks=[{"block": "bad", "anchor": "old"}])
        doc = Document("a" * 64, "source.pdf", "Paper", [], 1, [block, note])
        pdf = SimpleNamespace(pages=[SimpleNamespace(crop=lambda box: SimpleNamespace(chars=[{"size": 9}]))])

        def crop(block, *args):
            block.asset = "assets/bad.png"
            block.asset_width_pt, block.asset_height_pt = 100, 20

        with patch("reading.pipeline.cmap", return_value=set(range(128)) | {0xFFFF}), \
             patch.dict("sys.modules", {"pdfplumber": SimpleNamespace(open=lambda path: nullcontext(pdf))}), \
             patch("reading.pipeline.open_native_pdf", return_value=nullcontext(pdf)), \
             patch("reading.extract._crop", side_effect=crop):
            preserve_source_glyphs(doc, Path("source.pdf"), Path("cache"), {"regular": Path("font.ttf")})
        self.assertEqual(block.text, "Original \x00 \uffff text")
        self.assertEqual(block.asset_role, "source_text")
        self.assertEqual(block.asset_text_size, 9)
        self.assertEqual(note.backlinks, [])
        self.assertTrue(doc.review)

    def test_failure_is_isolated_and_no_paper_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / (name + ".pdf") for name in ("first", "bad", "last")]
            for i, path in enumerate(sources):
                path.write_bytes(str(i).encode())
            prepared, rendered = [], []

            def prepare(path, work, options):
                prepared.append(path.name)
                if path.stem == "bad":
                    raise ValueError("test extraction failure")

            def render(path, *args):
                rendered.append(path.name)
                return str(root / path.stem), {"status": "ready", "title": path.stem,
                    "source_pages": 1, "review": [], "outputs": {}}

            with patch("reading.batch.ProcessPoolExecutor", ImmediatePool), \
                 patch("reading.batch._prepare", side_effect=prepare), \
                 patch("reading.batch._render", side_effect=render):
                state = run_batch(sources, root / "out", root / "cache", ReadingProfile(), workers=2)
            self.assertTrue(state["complete"])
            self.assertEqual(state["finished"], 2)
            self.assertEqual(state["failed"], 1)
            self.assertEqual(prepared, [p.name for p in sources])
            self.assertEqual(rendered, ["first.pdf", "last.pdf"])
            self.assertEqual(json.loads((root / "out/batch-progress.json").read_text())["failed"], 1)

    def test_publish_requires_expected_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper.epub").write_bytes(b"actual output")
            report = {"outputs": {"epub": {"filename": "paper.epub", "sha256": "wrong"}}}
            with self.assertRaises(RuntimeError):
                publish_paper(root / "published", root, report)
            self.assertFalse((root / "published/EPUBs/paper.epub").exists())
            report["outputs"]["epub"]["sha256"] = digest(root / "paper.epub")
            publish_paper(root / "published", root, report)
            self.assertEqual((root / "published/EPUBs/paper.epub").read_bytes(), b"actual output")

    def test_contents_keeps_subsections_and_escapes_titles(self):
        blocks = [Block("title", "title", "Paper"), Block("h1", "heading", "1 A & B", level=1),
                  Block("h2", "heading", "1.1 Detail", level=2),
                  Block("h3", "heading", "1.1.1 More", level=3),
                  Block("h4", "heading", "2 Results", level=1)]
        doc = Document("a" * 64, "source.pdf", "Paper", [], 1, blocks)
        locations = {b.id: "body.xhtml#" + b.id for b in blocks}
        root = ET.fromstring(toc_html(toc_entries(doc, locations)))
        self.assertEqual(len(root.findall(".//a")), 5)
        self.assertEqual(root.find("li[2]/ol/li/ol/li/a").text, "1.1.1 More")
        self.assertEqual(root.find("li[2]/a").text, "1 A & B")


if __name__ == "__main__":
    unittest.main()
