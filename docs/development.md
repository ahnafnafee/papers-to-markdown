# Development

[← README](../README.md)

## Entry points and environments

| Entry point | Environment | Purpose |
| :--- | :--- | :--- |
| `main.py` | `pyproject.toml` | Existing Markdown and EPUB conversion; delegation to reading mode. |
| `read.py` | PEP 723 metadata + `read.py.lock` | Docling extraction, Verdana PDFs, EPUBs, and batch conversion. |
| `kindle.py` | PEP 723 metadata + `kindle.py.lock` | Local browser sign-in and Amazon web delivery. |

The reading pipeline and pdf-craft need different Transformers versions. Keep their environments separate and use the locked script entry points when running reading or Kindle commands. Font binaries, PDFs used for personal conversions, generated publications, captures, browser profiles, and delivery receipts do not belong in source control.

## Source layout

```text
reading/
  model.py         Shared document, source regions, and reading profile
  extract.py       Local extraction, native text alignment, and source crops
  source.py        Verified temporary compatibility copies for damaged page trees
  pdf.py           XeLaTeX PDF renderer
  epub.py          EPUB package, navigation, links, fonts, and HTML preview
  cover.py         High-contrast typographic EPUB covers
  validate.py      Source audits and publication validation
  pipeline.py      Cache receipts and complete publication bundles
  batch.py         Isolated inference and parallel exports
  library.py       Hash-verified copies and searchable catalogue
  cli.py           Input selection and reading commands
kindle_delivery/   Browser session, upload client, local receipts, and CLI
scripts/           Pilot, library publication, inspection, and repair tools
tests/             Reading, batch, layout, and Kindle regression checks
test_main.py       Existing Markdown cleanup checks
```

## Local validation

```powershell
uv run --no-project --with playwright==1.64.0 python -m unittest discover -s tests -v
uv run python test_main.py
```

These checks use synthetic documents and fixtures. The Kindle suite mocks Amazon and also exercises real HTTP serialization against a loopback server; it sends no books. It covers separate account and upload cookie jars, malformed EPUBs, duplicate protection, uncertain submissions, and local receipt locking.

Reading tests check source provenance, inline notation, safe rendering, cover and contents structure, links, font obfuscation, top-level input selection, duplicate inputs, interrupted receipts, worker failures, and library publication. For actual conversions, supplement them with source-page comparison, rendered-page inspection, and EPUBCheck. Formal package validity does not establish scientific fidelity or physical Kindle appearance.

When changing the renderers, cached receipts detect their source fingerprint. Changes to CLI, batch scheduling, or catalogue presentation do not invalidate publication rendering. Keep recovery metadata and backups available when inspecting an existing library.
