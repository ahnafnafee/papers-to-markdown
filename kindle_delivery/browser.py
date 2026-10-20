"""Keep Amazon authentication in a dedicated local browser profile."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

ORIGIN = "https://www.amazon.com"
HOME = ORIGIN + "/sendtokindle"
CSRF_SELECTOR = 'input[name="csrfToken"]'


class KindleError(RuntimeError):
    pass


class SignInRequired(KindleError):
    pass


def default_state_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "PapersToMarkdown/Kindle"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/papers-to-markdown/kindle"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "papers-to-markdown/kindle"


def ready(page) -> bool:
    from urllib.parse import urlsplit

    url = urlsplit(page.url)
    if url.scheme != "https" or url.netloc != "www.amazon.com" or url.path != "/sendtokindle":
        return False
    return bool(page.locator(CSRF_SELECTOR).count() and page.locator(CSRF_SELECTOR).first.input_value())


@contextmanager
def browser_session(state_dir: Path, *, headless: bool = False,
                    login_timeout: float = 600, channel: str = "msedge"):
    from playwright.sync_api import sync_playwright, Error as BrowserError

    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(state_dir / "browser"), channel=channel, headless=headless,
                viewport={"width": 1100, "height": 850}, locale="en-US")
        except BrowserError:
            raise KindleError("Could not open the Kindle browser profile. Close its other window, or select an installed browser with --browser chrome.") from None
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(15_000)
            page.goto(HOME, wait_until="domcontentloaded", timeout=60_000)
            if not headless:
                page.bring_to_front()
            if not ready(page):
                if headless:
                    raise SignInRequired("Amazon sign-in is needed. Run: uv run --script kindle.py login")
                page.goto(HOME + "/dnd/signin", wait_until="domcontentloaded", timeout=60_000)
                page.bring_to_front()
                print("Sign in to Amazon in the browser window. Complete any verification there; the tool does not ask for your password.", flush=True)
                try:
                    page.wait_for_function("""() => location.origin === 'https://www.amazon.com'
                        && location.pathname === '/sendtokindle'
                        && !!document.querySelector('input[name="csrfToken"]')?.value""",
                        timeout=login_timeout * 1000)
                except BrowserError:
                    raise SignInRequired("Sign-in did not finish. Run the login command again when ready.") from None
            if not ready(page):
                raise SignInRequired("Amazon did not return an authenticated Send to Kindle page.")
            yield playwright, context, page
        except BrowserError:
            raise KindleError("The Amazon browser request failed. Check your connection or reopen the login window.") from None
        finally:
            try:
                context.close()
            except BrowserError:
                pass
