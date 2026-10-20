# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = ["playwright==1.64.0"]
# ///
"""Send existing EPUBs to Kindle with a local Amazon browser sign-in."""

if __name__ == "__main__":
    import sys

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    from kindle_delivery.cli import main

    raise SystemExit(main())
