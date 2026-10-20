"""Delivery regressions; no test sends a document or reads real credentials."""

import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kindle_delivery.browser import KindleError, SignInRequired
from kindle_delivery.cli import main
from kindle_delivery.client import KindleClient, WebTransport, account_key, resolve_devices, validate_upload_url
from kindle_delivery.files import inspect_epub, select_publications
from kindle_delivery.state import Receipts, state_lock


def epub(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        archive.writestr("OEBPS/book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Learning &amp; α</dc:title><dc:creator>Léon Example</dc:creator></metadata></package>')
    return path


class Transport:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def request(self, method, route, payload=None, **kwargs):
        self.calls.append((method, route, payload, kwargs))
        if self.fail == route:
            raise KindleError("Request outcome unknown")
        if route == "/init":
            return {"uploadUrl": "https://example.s3.amazonaws.com/upload?signature=PRIVATE", "stkToken": "PRIVATE-TOKEN"}
        return None

    def put(self, url, data):
        self.calls.append(("PUT", url, data))
        if self.fail == "PUT":
            raise KindleError("Upload failed")


class FileTests(unittest.TestCase):
    def test_epub_title_and_authors_preserve_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            book = inspect_epub(epub(Path(directory) / "paper.epub"))
            self.assertEqual(book.title, "Learning & α")
            self.assertEqual(book.author, "Léon Example")

    def test_recursive_selection_deduplicates_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            original = epub(root / "paper.epub")
            (root / "nested" / "copy.epub").write_bytes(original.read_bytes())
            self.assertEqual(len(select_publications([root], recursive=True)), 1)

    def test_bad_container_and_oversized_metadata_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.epub"
            path.write_bytes(b"not a ZIP")
            with self.assertRaises(ValueError):
                inspect_epub(path)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", "x" * 2_000_001)
            with self.assertRaisesRegex(ValueError, "metadata"):
                inspect_epub(path)

    def test_changed_selected_file_is_not_uploaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = inspect_epub(epub(root / "paper.epub"))
            book.path.write_bytes(b"changed")
            transport = Transport()
            with self.assertRaisesRegex(ValueError, "changed"):
                KindleClient(transport).send(book, Receipts(root, "account"))
            self.assertFalse(transport.calls)

    def test_dry_run_never_opens_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            path = epub(Path(directory) / "paper.epub")
            with patch("kindle_delivery.cli.browser_session") as browser, redirect_stdout(io.StringIO()):
                self.assertEqual(main(["send", str(path), "--dry-run"]), 0)
            browser.assert_not_called()


class DeliveryTests(unittest.TestCase):
    def test_absent_author_is_omitted_from_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = replace(inspect_epub(epub(root / "paper.epub")), authors=())
            transport = Transport()
            KindleClient(transport).send(book, Receipts(root, "account"))
            self.assertNotIn("author", transport.calls[-1][2])

    def test_send_contract_preserves_metadata_and_skips_accepted_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = inspect_epub(epub(root / "paper.epub"))
            transport = Transport()
            receipts = Receipts(root, "account")
            client = KindleClient(transport)
            self.assertEqual(client.send(book, receipts), "accepted")
            self.assertEqual([c[0] for c in transport.calls], ["POST", "PUT", "POST"])
            request = transport.calls[-1][2]
            self.assertEqual(request["title"], book.title)
            self.assertEqual(request["author"], book.author)
            self.assertTrue(request["archive"])
            self.assertEqual(request["forceConvert"], "false")
            self.assertNotIn("deviceList", request)
            self.assertEqual(client.send(book, Receipts(root, "account")), "already-accepted")
            self.assertEqual(len(transport.calls), 3)
            stored = receipts.path.read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE", stored)
            self.assertNotIn("delivered", stored)

    def test_interrupted_submission_is_recorded_and_never_retried_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = inspect_epub(epub(root / "paper.epub"))
            transport = Transport(fail="/send-v2")
            client = KindleClient(transport)
            with self.assertRaises(KindleError):
                client.send(book, Receipts(root, "account"))
            self.assertEqual(Receipts(root, "account").get(book.sha256, "library")["status"], "uncertain")
            with self.assertRaisesRegex(KindleError, "uncertain"):
                client.send(book, Receipts(root, "account"))
            self.assertEqual(len(transport.calls), 3)
            transport.fail = None
            self.assertEqual(client.send(book, Receipts(root, "account"), resend=True), "accepted")

    def test_upload_failure_never_submits_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = inspect_epub(epub(root / "paper.epub"))
            transport = Transport(fail="PUT")
            with self.assertRaises(KindleError):
                KindleClient(transport).send(book, Receipts(root, "account"))
            self.assertFalse(any(c[1] == "/send-v2" for c in transport.calls))
            self.assertEqual(Receipts(root, "account").get(book.sha256, "library")["status"], "not-submitted")

    def test_device_delivery_has_its_own_receipt_and_does_not_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = inspect_epub(epub(root / "paper.epub"))
            transport = Transport()
            client = KindleClient(transport)
            receipts = Receipts(root, "account")
            client.send(book, receipts)
            client.send(book, receipts, devices=["device-one"])
            payload = transport.calls[-1][2]
            self.assertFalse(payload["archive"])
            self.assertEqual(payload["deviceList"], ["device-one"])

    def test_bad_initialization_does_not_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = inspect_epub(epub(root / "paper.epub"))
            transport = Mock()
            transport.request.return_value = {"unexpected": "response"}
            with self.assertRaisesRegex(KindleError, "initialization"):
                KindleClient(transport).send(book, Receipts(root, "account"))
            transport.put.assert_not_called()

    def test_account_receipts_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Receipts(root, "one").save("file", "library", {"status": "accepted"})
            self.assertIsNone(Receipts(root, "two").get("file", "library"))


class BoundaryTests(unittest.TestCase):
    def test_upload_urls_reject_non_amazon_hosts_and_never_echo_signature(self):
        for url in ("http://bucket.s3.amazonaws.com/file", "https://amazon.com.evil.example/?secret=PRIVATE",
                    "https://user:PRIVATE@bucket.s3.amazonaws.com/file", "https://bucket.s3.amazonaws.com:123/file",
                    "https://bucket.s3.amazonaws.com:PRIVATE/file"):
            with self.subTest(url=url), self.assertRaises(KindleError) as error:
                validate_upload_url(url)
            self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(validate_upload_url("https://bucket.s3.amazonaws.com/file?signature=value"),
                         "https://bucket.s3.amazonaws.com/file?signature=value")

    def test_customer_identifier_is_hashed_and_ambiguous_account_fails(self):
        key = account_key('var opts={customerId: "A123456789ABC"};')
        self.assertEqual(len(key), 64)
        self.assertNotIn("A123456789ABC", key)
        with self.assertRaises(KindleError):
            account_key('customerId:""')
        with self.assertRaises(KindleError):
            account_key('customerId:"A123456789ABC", customerId:"B123456789ABC"')

    def test_device_names_must_be_unambiguous(self):
        devices = [{"deviceName": "Kindle", "deviceSerialNumber": str(n)} for n in range(2)]
        with self.assertRaisesRegex(KindleError, "ambiguous"):
            resolve_devices(["Kindle"], devices)
        self.assertEqual(resolve_devices(["1"], devices), ["1"])

    def test_corrupt_receipts_are_not_treated_as_unsent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "receipts.json").write_text("partial JSON", encoding="utf-8")
            with self.assertRaisesRegex(KindleError, "unreadable"):
                Receipts(root, "account")

    def test_unknown_receipt_status_is_not_treated_as_unsent(self):
        with tempfile.TemporaryDirectory() as directory:
            receipts = Receipts(Path(directory), "account")
            receipts.save("file", "library", {"status": "unknown"})
            with self.assertRaisesRegex(KindleError, "unknown status"):
                receipts.get("file", "library")

    def test_concurrent_sender_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with state_lock(Path(directory)):
                with self.assertRaisesRegex(KindleError, "already"):
                    with state_lock(Path(directory)):
                        self.fail("Second sender acquired the lock")

    def test_redirect_to_login_is_not_success_and_does_not_leak_csrf(self):
        transport = WebTransport.__new__(WebTransport)
        transport.timeout = 1000
        transport.page = Mock()
        transport.page.locator.return_value.first.input_value.return_value = "PRIVATE-CSRF"
        response = Mock(status=302)
        transport.context = Mock()
        transport.context.request.fetch.return_value = response
        with self.assertRaises(SignInRequired) as error:
            transport.request("POST", "/init", {"fileSize": 10})
        self.assertNotIn("PRIVATE-CSRF", str(error.exception))
        kwargs = transport.context.request.fetch.call_args.kwargs
        self.assertEqual(kwargs["max_retries"], 0)
        self.assertEqual(kwargs["max_redirects"], 0)
        response.dispose.assert_called_once()

    def test_upload_uses_an_isolated_context_without_amazon_auth_headers(self):
        playwright, context, page = Mock(), Mock(), Mock()
        transport = WebTransport(playwright, context, page)
        playwright.request.new_context.assert_called_once_with()
        transport.upload_context.put.return_value.status = 200
        transport.put("https://bucket.s3.amazonaws.com/file?secret=PRIVATE", b"file")
        self.assertEqual(transport.upload_context.put.call_args.kwargs["headers"], {"Content-Type": "application/epub+zip"})
        context.request.fetch.assert_not_called()


class ConversionIntegrationTests(unittest.TestCase):
    def test_reading_conversion_sends_only_ready_epubs(self):
        from reading.cli import main as reading_main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "paper.pdf"
            source.write_bytes(b"source")
            for review, expected in (([], 0), ([{"reason": "Uncertain extraction"}], 1)):
                report = {"review": review, "outputs": {"epub": {"filename": "paper.epub"}}}
                with patch("reading.pipeline.convert", return_value=(root, report)), \
                     patch("kindle_delivery.launch.send_epubs", return_value=0) as send, \
                     redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(reading_main([str(source), "--send-to-kindle"]), expected)
                self.assertEqual(send.call_count, 0 if review else 1)

    def test_pdf_only_send_fails_before_any_conversion(self):
        from reading.cli import main as reading_main

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            reading_main(["--format", "pdf", "--send-to-kindle"])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
