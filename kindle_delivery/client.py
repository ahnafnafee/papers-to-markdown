"""Client for the Send to Kindle web application's observed request contract."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .browser import CSRF_SELECTOR, HOME, ORIGIN, KindleError, SignInRequired
from .files import Publication
from .state import Receipts

MIME = "application/epub+zip"
ROUTES = {"/init", "/send-v2", "/recent-docs", "/get-device-list"}


def account_key(html: str) -> str:
    # Amazon's page includes customerId in its inline navigation configuration.
    ids = set(re.findall(r'''["']?customerId["']?\s*:\s*["']([A-Z0-9]{8,32})["']''', html))
    if len(ids) != 1:
        raise KindleError("Could not identify the signed-in Amazon account. The web page may have changed; no book was sent.")
    return hashlib.sha256((ORIGIN + ":" + ids.pop()).encode()).hexdigest()


def validate_upload_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise KindleError("Amazon returned an unexpected upload address. No file was uploaded.") from None
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or port not in (None, 443) or parts.username or parts.password
            or parts.fragment or not any(host.endswith("." + suffix) for suffix in ("amazonaws.com", "amazon.com"))):
        raise KindleError("Amazon returned an unexpected upload address. No file was uploaded.")
    return url


class WebTransport:
    def __init__(self, playwright, context, page, timeout: float = 120):
        self.context, self.page = context, page
        self.timeout = timeout * 1000
        self.upload_context = playwright.request.new_context()

    def close(self):
        self.upload_context.dispose()

    def request(self, method: str, route: str, payload: dict | None = None,
                *, allow_empty: bool = False):
        if route not in ROUTES:
            raise ValueError("Unsupported Kindle route.")
        token = self.page.locator(CSRF_SELECTOR).first.input_value()
        if not token:
            raise SignInRequired("Amazon sign-in expired. Reopen the login window.")
        try:
            response = self.context.request.fetch(HOME + route, method=method, data=payload,
                headers={"anti-csrftoken-a2z": token, "Accept": "application/json",
                         "Content-Type": "application/json", "Origin": ORIGIN, "Referer": HOME},
                timeout=self.timeout, max_redirects=0, max_retries=0)
        except Exception:
            raise KindleError("The Amazon request did not finish. Its outcome may need checking in Kindle status.") from None
        try:
            if response.status in (301, 302, 303, 307, 308, 401, 403):
                raise SignInRequired("Amazon requires a fresh sign-in. Reopen the login window.")
            if not 200 <= response.status < 300:
                raise KindleError(f"Amazon rejected the request (HTTP {response.status}).")
            body = response.body()
            if not body and allow_empty:
                return None
            if len(body) > 2_000_000 or "json" not in response.headers.get("content-type", "").lower():
                raise KindleError("Amazon returned an unexpected response. Check status before sending again.")
            try:
                result = json.loads(body)
            except ValueError:
                raise KindleError("Amazon returned an unreadable response. Check status before sending again.") from None
            if isinstance(result, dict) and (result.get("success") is False or result.get("error") or result.get("errors")):
                raise KindleError("Amazon reported an error. Check status before sending again.")
            return result
        finally:
            response.dispose()

    def put(self, url: str, data: bytes):
        validate_upload_url(url)
        try:
            response = self.upload_context.put(url, data=data, headers={"Content-Type": MIME},
                timeout=self.timeout, max_redirects=0, max_retries=0)
        except Exception:
            raise KindleError("The file upload did not finish. Amazon has not been asked to deliver it.") from None
        try:
            if not 200 <= response.status < 300:
                raise KindleError(f"The file upload failed (HTTP {response.status}).")
        finally:
            response.dispose()


class KindleClient:
    def __init__(self, transport):
        self.transport = transport

    def recent(self) -> list[dict]:
        result = self.transport.request("GET", "/recent-docs")
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise KindleError("Amazon's recent-document response has changed.")
        return result

    def devices(self) -> list[dict]:
        result = self.transport.request("POST", "/get-device-list", {"extName": "drag_drop_web", "extVersion": "1.0"})
        if not isinstance(result, dict) or not isinstance(result.get("ownedDevices"), list):
            raise KindleError("Amazon's device-list response has changed.")
        devices = result["ownedDevices"]
        if any(not isinstance(d, dict) or not isinstance(d.get("deviceName"), str)
               or not isinstance(d.get("deviceSerialNumber"), str) for d in devices):
            raise KindleError("Amazon returned an unfamiliar device record.")
        return devices

    def send(self, publication: Publication, receipts: Receipts, *, devices: list[str] | None = None,
             resend: bool = False) -> str:
        destination = "library" if not devices else hashlib.sha256(json.dumps(sorted(set(devices))).encode()).hexdigest()
        previous = receipts.get(publication.sha256, destination)
        if previous and not resend:
            if previous.get("status") == "accepted":
                return "already-accepted"
            if previous.get("status") in {"submitting", "uncertain"}:
                raise KindleError("An earlier send has an uncertain outcome. Check Kindle status; use --resend only if another copy is wanted.")
        data = publication.read_verified()
        record = {"filename": publication.path.name, "title": publication.title,
                  "bytes": publication.size, "started": datetime.now(timezone.utc).isoformat(), "status": "preparing"}
        receipts.save(publication.sha256, destination, record)
        submitting = False
        try:
            init = self.transport.request("POST", "/init", {"fileSize": publication.size,
                "contentType": MIME, "appVersion": "1.0", "appName": "drag_drop_web", "fileExtension": "epub"})
            if (not isinstance(init, dict) or not isinstance(init.get("uploadUrl"), str)
                    or not isinstance(init.get("stkToken"), str) or not init["stkToken"]):
                raise KindleError("Amazon's upload initialization response has changed.")
            validate_upload_url(init["uploadUrl"])
            self.transport.put(init["uploadUrl"], data)
            payload = {"extName": "drag_drop_web", "inputFormat": "epub", "extVersion": "1.0",
                "stkToken": init["stkToken"], "title": publication.title, "dataType": MIME,
                "stkGuid": "", "archive": not bool(devices),
                "fileSize": publication.size, "forceConvert": "false", "inputFileName": publication.path.name,
                "batchId": str(uuid.uuid4())}
            if publication.author:
                payload["author"] = publication.author
            if devices:
                payload["deviceList"] = sorted(set(devices))
            record["status"] = "submitting"
            receipts.save(publication.sha256, destination, record)
            submitting = True
            self.transport.request("POST", "/send-v2", payload, allow_empty=True)
            record.update(status="accepted", accepted=datetime.now(timezone.utc).isoformat())
            receipts.save(publication.sha256, destination, record)
            return "accepted"
        except Exception:
            record["status"] = "uncertain" if submitting else "not-submitted"
            receipts.save(publication.sha256, destination, record)
            raise


def resolve_devices(requested: list[str], available: list[dict]) -> list[str]:
    result = []
    for value in requested:
        matches = [d for d in available if value == d["deviceSerialNumber"] or value.casefold() == d["deviceName"].casefold()]
        if len(matches) != 1:
            raise KindleError(f"Device name is missing or ambiguous: {value}. Run the devices command and select its exact identifier.")
        if matches[0]["deviceSerialNumber"] not in result:
            result.append(matches[0]["deviceSerialNumber"])
    return result
