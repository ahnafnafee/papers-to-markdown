"""Send EPUBs, sign in, and inspect Amazon's processing status."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from .browser import KindleError, browser_session, default_state_dir
from .client import KindleClient, WebTransport, account_key, resolve_devices
from .files import select_publications
from .state import Receipts, state_lock


def positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Choose a positive number of seconds.")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send EPUBs through Amazon's Send to Kindle web service.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (("login", "Sign in once in the local browser."),
                               ("send", "Upload selected EPUBs to your Kindle library."),
                               ("status", "Check Amazon's recent-document processing status."),
                               ("devices", "List Kindle devices registered to the signed-in account.")):
        child = commands.add_parser(command, help=help_text)
        child.add_argument("--state-dir", type=Path, default=default_state_dir())
        child.add_argument("--browser", choices=["msedge", "chrome"], default="msedge" if sys.platform == "win32" else "chrome")
        child.add_argument("--headless", action="store_true", help="Use an existing sign-in without opening a window.")
        child.add_argument("--login-timeout", type=positive_seconds, default=600)
        child.add_argument("--timeout", type=positive_seconds, default=120, help="Request timeout in seconds.")
        if command == "send":
            child.add_argument("inputs", type=Path, nargs="+", help="EPUB files or folders.")
            child.add_argument("--recursive", action="store_true")
            child.add_argument("--dry-run", action="store_true", help="Inspect files without opening Amazon or uploading.")
            child.add_argument("--resend", action="store_true", help="Explicitly send another copy, including after an uncertain earlier send.")
            child.add_argument("--device", action="append", default=[], help="Exact device name or identifier; sends to those devices instead of archiving in the library.")
        if command == "status":
            child.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        publications = select_publications(args.inputs, args.recursive) if args.command == "send" else []
        for publication in publications:
            print(f"Selected: {publication.title} ({publication.size / 1048576:.1f} MiB) — {publication.path}", flush=True)
        if args.command == "send" and args.dry_run:
            print("No uploads made. Destination: " + (", ".join(args.device) if args.device else "your Kindle library"))
            return 0
        if args.command == "status" and args.limit <= 0:
            raise ValueError("Status limit must be positive.")
        with state_lock(args.state_dir):
            with browser_session(args.state_dir, headless=args.headless,
                                 login_timeout=args.login_timeout, channel=args.browser) as (playwright, context, page):
                transport = WebTransport(playwright, context, page, args.timeout)
                try:
                    client = KindleClient(transport)
                    recent = client.recent()  # Verify the authenticated service before any upload.
                    if args.command == "login":
                        account_key(page.content())
                        print("Signed in. The local Kindle connection is ready; no book was sent.")
                        return 0
                    if args.command == "status":
                        labels = {"IN_PROGRESS": "Processing", "IN_LIBRARY": "In Kindle library",
                                  "SENT_TO_DEVICE": "Sent to device", "FAILED": "Failed"}
                        for row in recent[:args.limit]:
                            print(f"{labels.get(row.get('status'), row.get('status', 'Unknown'))}: {row.get('filename', 'Untitled')}")
                        if not recent:
                            print("Amazon reports no recently sent documents.")
                        return 0
                    if args.command == "devices":
                        for device in client.devices():
                            print(f"{device['deviceName']}: {device['deviceSerialNumber']}")
                        return 0
                    devices = resolve_devices(args.device, client.devices()) if args.device else []
                    receipts = Receipts(args.state_dir, account_key(page.content()))
                    failures = 0
                    for publication in publications:
                        try:
                            outcome = client.send(publication, receipts, devices=devices, resend=args.resend)
                            prefix = "Already accepted; skipped" if outcome == "already-accepted" else "Accepted by Amazon"
                            print(f"{prefix}: {publication.title}", flush=True)
                        except (KindleError, ValueError, OSError) as error:
                            failures += 1
                            print(f"Failed: {publication.path.name}: {error}", file=sys.stderr, flush=True)
                    print("Acceptance confirms submission. Use the status command to check conversion; sync your Kindle to download it.")
                    return 1 if failures else 0
                finally:
                    transport.close()
    except (KindleError, ValueError, OSError) as error:
        print(f"Kindle: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Kindle command stopped. Check status before repeating any interrupted send.", file=sys.stderr)
        return 130
