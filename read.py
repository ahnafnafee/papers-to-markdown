# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#   "docling==2.140.0",
#   "transformers==4.57.6",
#   "torch==2.10.0",
#   "torchvision==0.25.0",
#   "pdfplumber==0.11.10",
#   "pypdf==6.6.0",
#   "fonttools==4.61.1",
#   "pytest==9.1.1",
# ]
# [[tool.uv.index]]
# name = "pytorch-cuda"
# url = "https://download.pytorch.org/whl/cu128"
# explicit = true
# [tool.uv.sources]
# torch = { index = "pytorch-cuda" }
# torchvision = { index = "pytorch-cuda" }
# ///
"""Reading-copy entry point; uv keeps its dependencies separate from pdf-craft."""

if __name__ == "__main__":
    from reading.cli import main

    raise SystemExit(main())
