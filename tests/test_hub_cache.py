"""Exercise the pinned Hub cache without network access or model downloads."""

import importlib.util
import tempfile
import unittest
import warnings
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import nullcontext
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from reading.extract import prepare_extraction


@unittest.skipUnless(importlib.util.find_spec("huggingface_hub"), "Requires the locked read.py environment")
class HubCacheTests(unittest.TestCase):
    def test_first_parallel_model_downloads_copy_when_windows_denies_symlinks(self):
        from huggingface_hub import file_download

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source.write_bytes(b"source")
            repo = root / "models--example--model"
            blobs = repo / "blobs"
            snapshot = repo / "snapshots" / "revision"
            blobs.mkdir(parents=True)
            snapshot.mkdir(parents=True)
            (blobs / "config").write_bytes(b"model configuration")
            (blobs / "weights").write_bytes(b"model weights")
            probe_started, release_probe, second_started = Event(), Event(), Event()

            def denied_symlink(src, dst, *args, **kwargs):
                if Path(dst).name == "dummy_file_dst":
                    probe_started.set()
                    if not release_probe.wait(5):
                        raise AssertionError("The test did not release the cache probe")
                error = OSError("A required privilege is not held by the client")
                error.winerror = 1314
                raise error

            def save_weights():
                second_started.set()
                file_download._create_symlink(str(blobs / "weights"), str(snapshot / "weights"), new_blob=True)

            def convert(path):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(file_download._create_symlink,
                                        str(blobs / "config"), str(snapshot / "config"))
                    try:
                        self.assertTrue(probe_started.wait(5), "The cache probe did not start")
                        second = pool.submit(save_weights)
                        self.assertTrue(second_started.wait(5), "The second cache writer did not start")
                        # Without serialization this writer observes a provisional True
                        # and raises WinError 1314 before the first probe finishes.
                        try:
                            second.result(timeout=.2)
                        except TimeoutError:
                            pass
                    finally:
                        release_probe.set()
                    first.result(timeout=5)
                    second.result(timeout=5)
                return SimpleNamespace(status=SimpleNamespace(value="success"), errors=[],
                                       document=SimpleNamespace(export_to_dict=lambda: {}))

            options = SimpleNamespace(accelerator_options=None, table_batch_size=0,
                                      model_dump=lambda **kwargs: {})
            modules = {
                "pdfplumber": SimpleNamespace(),
                "docling.datamodel.accelerator_options": SimpleNamespace(
                    AcceleratorDevice=lambda device: device, AcceleratorOptions=lambda **kwargs: kwargs),
                "docling.datamodel.base_models": SimpleNamespace(InputFormat=SimpleNamespace(PDF="pdf")),
                "docling.datamodel.pipeline_options": SimpleNamespace(PdfPipelineOptions=lambda **kwargs: options),
                "docling.document_converter": SimpleNamespace(
                    DocumentConverter=lambda **kwargs: SimpleNamespace(convert=convert),
                    PdfFormatOption=lambda **kwargs: kwargs),
            }
            with patch.dict("sys.modules", modules), \
                 patch("sys.platform", "win32"), \
                 patch("reading.extract.importlib.metadata.version", return_value="2.140.0"), \
                 patch("reading.extract.open_native_pdf", return_value=nullcontext(SimpleNamespace(pages=[]))), \
                 patch("reading.extract._converters", {}), \
                 patch.dict(file_download._are_symlinks_supported_in_dir, {}, clear=True), \
                 patch.object(file_download.os, "symlink", side_effect=denied_symlink), \
                 warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                try:
                    cache = prepare_extraction(source, root / "work", ocr="never")
                except OSError as error:
                    self.fail(f"Parallel model cache writes failed: WinError {error.winerror}")

            self.assertTrue((cache / "extraction.json").is_file())
            self.assertEqual((snapshot / "config").read_bytes(), b"model configuration")
            self.assertEqual((snapshot / "weights").read_bytes(), b"model weights")
            self.assertFalse((snapshot / "config").is_symlink())
            self.assertFalse((snapshot / "weights").is_symlink())
            self.assertTrue((blobs / "config").is_file(), "Existing shared blobs must be preserved")
            self.assertFalse((blobs / "weights").exists(), "New blobs can move into the snapshot")


if __name__ == "__main__":
    unittest.main()
