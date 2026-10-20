# Send to Kindle web integration

[← README](../README.md) · [Reading guide](reading-guide.md)

The sender follows Amazon's Send to Kindle website for EPUB delivery. It preserves the selected EPUB bytes and takes titles and authors from their package metadata. Amazon performs the Kindle conversion. The current implementation targets `www.amazon.com`.

## Use the sender

Run commands from the repository root with uv and Python 3.13:

```powershell
uv run --locked --script kindle.py login
uv run --locked --script kindle.py send "C:\Reading Copies\example.epub" --dry-run
uv run --locked --script kindle.py send "C:\Reading Copies\example.epub"
uv run --locked --script kindle.py status
```

Sign in on Amazon's page and complete any verification in the visible browser. Windows uses installed Edge; add `--browser chrome` to use installed Chrome. `login` verifies the connection without sending a document. A dry run validates selected EPUBs without opening Amazon or uploading.

To send a folder, use `send "C:\Reading Copies"`; add `--recursive` when EPUBs are inside per-paper subfolders. `status --limit 20` shows more recent processing records. `devices` lists registered devices, and `send --device "Exact device name"` selects device-only delivery instead of the default Kindle library destination. Device-only delivery has local test coverage but has not been exercised live.

Use `--headless` with an existing sign-in to run without a browser window. Sign-in data and receipts stay under `%LOCALAPPDATA%\PapersToMarkdown\Kindle` on Windows, outside the repository. `--state-dir` chooses another local profile. An uncertain submission is not resent automatically; check Amazon before explicitly choosing `send --resend`.

Reading mode accepts `--send-to-kindle` after conversion. It sends only EPUBs without outstanding review flags; skipped review items are reported and return a nonzero exit status. Existing EPUBs can also be selected explicitly with the standalone sender after review. Amazon acceptance confirms submission; use `status` to distinguish processing from completion, then sync the physical Kindle.

## What was inspected

On October 20, 2026, an isolated browser captured the public [Send to Kindle page](https://www.amazon.com/sendtokindle) and its loaded assets. The capture returned HTTP 200 and contained 114 network entries. Its SHA-256 was `ba5dcbe4730c6d13dc7630374d6a1bfe360aa62e1a7e5867132615266220cc55`. The original HAR remains in a private local capture directory; it is excluded from version control.

The relevant first-party asset was [KindleDocsWebServiceBuzzAssets-home](https://m.media-amazon.com/images/I/7159RxFVlnL.js?AUIClients/KindleDocsWebServiceBuzzAssets-home), 248,577 bytes, SHA-256 `5c7ca9d88347621b4b57f2dc5a641032d51b1aa59f952f222b829680a983ea4b`. Its upload handler, device selector, and recently-sent document view expose the request sequence below. This is static evidence of the website's contract; the unauthenticated capture did not exercise file upload or delivery.

## Request sequence

| Step | Request | Fields or behavior visible in the website asset |
| --- | --- | --- |
| Sign in | Browser navigation to `/sendtokindle/dnd/signin` | Returns to the Send to Kindle page after account sign-in and any verification. |
| Session header | Hidden input `csrfToken` | Same-origin requests send its value as `anti-csrftoken-a2z`; browser cookies authenticate the session. |
| Initialize | `POST /sendtokindle/init` | `fileSize`, `contentType`, `appVersion: "1.0"`, `appName: "drag_drop_web"`, `fileExtension`. Handler expects `uploadUrl` and `stkToken`. |
| Upload bytes | `PUT uploadUrl` | Original file bytes, with the same content type used at initialization. |
| Submit delivery | `POST /sendtokindle/send-v2` | `extName`, `inputFormat`, `extVersion`, `stkToken`, `title`, `dataType`, `stkGuid`, optional `author`, `archive`, `fileSize`, `forceConvert`, `inputFileName`, and UUID `batchId`. |
| Select device only | `POST /sendtokindle/get-device-list` | Request has `extName: "drag_drop_web"` and `extVersion: "1.0"`. The view reads `ownedDevices`, `deviceName`, and `deviceSerialNumber`. Non-archived sends include selected serial numbers in `deviceList`. |
| Check processing | `GET /sendtokindle/recent-docs` | The view expects an array with `docId`, `filename`, `uploadDate`, `status`, `client`, and `failureCode`. |

The asset limits files to 209,715,200 bytes and includes EPUB among its accepted extensions. Its status strings are `IN_PROGRESS`, `FAILED`, `IN_LIBRARY`, and `SENT_TO_DEVICE`. These server-side processing states do not establish that a physical Kindle has downloaded the file.

The implementation sends EPUB as `application/epub+zip`, preserves its publication metadata, uses `archive: true` for the default library destination, and sends `forceConvert: "false"`. This upload and library-submission contract succeeded for all three pilot EPUBs. Device-only mode mirrors the website's alternative `archive: false` and `deviceList` fields and has not been exercised live. Unfamiliar responses stop further requests and leave a submission outcome uncertain.

## Authentication and receipts

Sign-in occurs on Amazon's own page in a dedicated local Edge or Chrome profile. The tool does not request the Amazon password or export browser cookies to source files. CSRF values and upload tokens stay in memory. A separate HTTP context uploads file bytes without Amazon account cookies or CSRF headers. Only HTTPS upload addresses under Amazon-owned host suffixes are accepted, and upload redirects are disabled.

Receipts are separate from browser credentials. They identify the account through a hash of the page's inline `customerId`, then record the selected EPUB hash, destination, title, and submission outcome. A local process lock prevents overlapping sends from the same profile. A confirmed submission is skipped on subsequent runs unless `--resend` is explicitly selected. Submission starts are saved before the request; interruption or an uncertain response blocks automatic resubmission. Requests have bounded timeouts and no automatic retries.

## Validation boundary

Local checks cover valid and malformed EPUBs, Unicode metadata, unchanged upload bytes, account and destination separation, duplicate prevention, damaged receipts, concurrent sends, expired sign-in redirects, upload failures, and uncertain submission outcomes. A real HTTP exercise against a local synthetic fixture checks the wire payloads and verifies that the upload receives no account cookie or CSRF header. It does not contact Amazon.

Public page and asset capture are verified. On October 20, 2026, authenticated connection checks returned HTTP 200 from `/recent-docs`, and a subsequent headless login reused the saved browser session. No documents were submitted during those connection checks.

Three authorized pilot submissions were then verified against their conversion-report hashes and reached Amazon's `IN_LIBRARY` state without failure codes. One was observed in `IN_PROGRESS` before completing. Document identifiers, decoded publication titles, account identity, and EPUB hashes were matched in local delivery receipts. Private identifiers, captures, and delivery records remain outside version control.

The live status response derives `filename` from the publication title, appends `.epub`, and can contain nested HTML entity escaping. Verification matched the returned document identifiers and decoded titles rather than assuming that the response used the local filename.

Amazon submission and processing are verified for these three EPUBs. Actual download and appearance on the e-ink Kindle remain unverified. Device-only delivery, failed-conversion recovery, and other response variants have not been exercised live. The interface is undocumented and may change.

## Calibre comparison

Calibre's built-in [Sharing books by email](https://manual.calibre-ebook.com/faq.html#i-cannot-send-emails-using-calibre) uses a mail service and the device's Kindle email address. This integration follows the first-party web uploader instead. Amazon documents the supported web service and EPUB intake in [Learn About Sending Documents to Your Kindle Library](https://www.amazon.com/gp/help/customer/display.html?nodeId=G5WYD9SAF7PGXRNA).
