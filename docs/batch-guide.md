# Batch conversion and reading libraries

[← README](../README.md) · [Reading guide](reading-guide.md) · [Development](development.md)

## Convert a folder

```powershell
uv run --locked --script read.py "C:\Papers" --dry-run
uv run --locked --script read.py "C:\Papers" --workers 4
```

The default selects only PDFs directly inside the source folder. Byte-identical files are deduplicated by SHA-256. Add `--recursive` to descend into subfolders, and repeat `--exclude` for relative globs or directory names:

```powershell
uv run --locked --script read.py "C:\Papers" --recursive --exclude _incoming --exclude "archive/*" --workers 4
```

`--workers` accepts 1–16 CPU export processes. Parallel mode uses one persistent inference process and a bounded queue of normalization and export jobs. The default of one worker uses serial conversion unless a checkpoint or EPUBCheck option selects the batch runner. GPU memory is used by the inference process; increasing export workers does not create one model instance per worker. Start with a few workers and adjust for available memory.

## Resume and validation

Run the same command again to resume. Validated per-paper receipts include input, profile, font, renderer, publication, and asset hashes. Modified or incomplete bundles are rebuilt. A conversion with review flags is still a completed bundle, so it is retained on resume.

Parallel mode writes `batch-progress.json` under the output directory. `--checkpoint` selects another progress file. Failures are recorded per input and later inputs continue. If native inference stalls for five minutes, its isolated process is stopped and a PDFium parser retry is attempted. This does not stop unrelated processes.

To add formal EPUB validation, install [EPUBCheck](https://github.com/w3c/epubcheck) and a compatible Java runtime, then pass its JAR:

```powershell
uv run --locked --script read.py "C:\Papers" --workers 4 --epubcheck "C:\Tools\epubcheck.jar"
```

EPUBCheck validates the EPUB package. Internal checks also examine navigation, cover structure, references, source-page coverage, output text, and PDF typesetting. Review notes remain useful even when an EPUB passes formal validation.

## Publish an inventoried library

The advanced library scripts accept an explicit inventory. This fixes the source selection before a long run and keeps duplicate aliases for the final catalogue. All source paths must be absolute, all files must be directly inside `root`, and every original must still match its recorded hash.

Create the inventory with a trusted local PDF inventory tool using this schema. Group byte-identical files into one document, record the physical source page count independently, and set `recursive` to `false`:

```json
{
  "root": "C:\\Papers",
  "recursive": false,
  "source_files": 2,
  "unique_documents": 1,
  "documents": [
    {
      "name": "Example Paper.pdf",
      "source": "C:\\Papers\\Example Paper.pdf",
      "aliases": ["C:\\Papers\\Example Paper - copy.pdf"],
      "sha256": "<complete-source-sha256>",
      "pages": 12,
      "metadata_title": "Example Paper"
    }
  ]
}
```

Save the completed inventory as `.cache/library-inventory.json`, replacing the placeholders with actual hashes and page counts. The simple folder command above does not require an inventory. For the script workflow, use the Python executable from the reading environment; the scripts import the same installed extraction and export dependencies. With a uv version that supports pylock export, the following preserves the locked package sources in a separate script environment:

```powershell
New-Item -ItemType Directory -Force .cache | Out-Null
uv export --script read.py --locked --format pylock.toml --output-file .cache/pylock.reading.toml
uv venv --python 3.13 .venv-reading
uv pip sync --python .venv-reading/Scripts/python.exe .cache/pylock.reading.toml

.venv-reading/Scripts/python.exe scripts/build_library.py --inventory .cache/library-inventory.json --publish-dir "C:\Reading Library" --workers 4 --device auto --epubcheck "C:\Tools\epubcheck.jar"
```

On Unix, create `.cache/` with `mkdir -p .cache` and use `.venv-reading/bin/python`. First-time model downloads still occur locally. `--limit` makes a bounded pilot, and `--prioritize` moves matching filename prefixes earlier. Reuse the same inventory and output directory when continuing the full collection.

Publication copies only the PDF and EPUB publications, verifies each copied file by hash, and generates:

```text
Reading Library/
├── START HERE.html
├── PDFs/
├── EPUBs/
├── Review notes/
└── conversion-details.json
```

The catalogue searches titles, source filenames, and duplicate aliases. Review notes link to the original local PDF. Extraction caches and typesetting work remain in the working output directories. The catalogue and conversion details contain local source paths; keep them with your own reading library.

## Recovery and final checks

After the batch has finished, the repair script can retry failures and restore whole pages omitted by layout extraction as labelled source images. It also retains source passages when mathematical notation cannot fit a reflowed line. Recovered material remains flagged for review:

```powershell
.venv-reading/Scripts/python.exe scripts/repair_library.py --inventory .cache/library-inventory.json --publish-dir "C:\Reading Library" --epubcheck "C:\Tools\epubcheck.jar"
.venv-reading/Scripts/python.exe scripts/check_library.py --inventory .cache/library-inventory.json --publish-dir "C:\Reading Library"
```

`check_library.py` verifies original hashes, physical source-page counts against the inventory, publication receipts, formal EPUBCheck results, and copied output hashes. `--sheets .cache/review-sheets` renders contact sheets for visual inspection. The default progress path is `output/pdf/library/batch-progress.json`; pass `--progress` when checking a different output directory.

`scripts/repair_layout.py` provides a Python helper for targeted inspection and repair of remaining long-line or caption overflow. It is not a command-line batch entry point. Preserve its backups, inspect the changed pages, rerun formal EPUB validation after modifying an EPUB, and refresh library copies and receipts before delivery.
