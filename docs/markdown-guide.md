# Markdown and original EPUB conversion

[← README](../README.md) · [Verdana reading mode](reading-guide.md)

`main.py` keeps the original pdf-craft pipeline for Markdown and EPUB. Markdown is the default, with extracted figure assets and venue-aware boilerplate removal. The new Verdana PDF and EPUB exporters are selected separately through `read.py` or `main.py --format reading`.

## Basic commands

```powershell
uv sync
uv run main.py
uv run main.py --files "C:\Papers\example.pdf"
uv run main.py --root "C:\Papers" --format epub
```

The default input is `papers/`. Markdown goes to `output/markdown/`, and the original EPUB exporter writes to `output/epubs/`. Existing publications are skipped unless `--force` is supplied. A failed PDF is reported while remaining inputs continue.

## Options

| Option | Values / default | Purpose |
| :--- | :--- | :--- |
| `--root` | `papers` | Input folder. |
| `--files` | Explicit PDF paths | Select individual files. |
| `--output-dir` | Based on format | Choose the output folder. |
| `--format` | `markdown` or `epub`; default `markdown` | Select an original exporter. |
| `--venue` | `auto`, `none`, or a venue key | Control Markdown boilerplate cleanup. |
| `--ocr-size` | `tiny`, `small`, `base`, `large`, `gundam`; default `small` | Choose the original OCR model size. |
| `--lang` | `en` or `zh`; default `en` | EPUB document language. |
| `--latex-render` | `mathml`, `svg`, `clipping`; default `svg` | EPUB equation rendering. |
| `--table-render` | `html` or `clipping`; default `html` | EPUB table rendering. |
| `--cover` / `--no-cover` | On | Include the cover. |
| `--footnotes` / `--no-footnotes` | On | Include footnotes and references. |
| `--inline-latex` / `--no-inline-latex` | On | Preserve inline LaTeX in EPUB. |
| `--models-cache-path` | `.pdf_craft_models` | Store original OCR models. |
| `--analysis-root` | `.pdf_craft_work` | Store intermediate analysis. |
| `--force` | Off | Rebuild an existing output. |
| `--dry-run` | Off | List selected files without converting. |

The reading pipeline has its own [OCR, spacing, font, and batch options](reading-guide.md). `--format epub --profile verdana-reading` selects its EPUB exporter through `main.py`.

## Publisher cleanup

Markdown cleanup normalizes LaTeX delimiters while preserving formula content, repairs OCR hyphenation artifacts, and removes matched publisher boilerplate. Venue detection can match more than one publisher fingerprint. Force one rule set with `--venue ieee`, or disable removal with `--venue none`.

| Key | Publisher or collection |
| :--- | :--- |
| `acm` | ACM Digital Library, including CHI, UIST, CSCW, IMWUT, and TOCHI. |
| `ieee` | IEEE Xplore conferences and journals. |
| `arxiv` | arXiv preprints. |
| `springer` | Springer, LNCS, and Springer Nature. |
| `elsevier` | Elsevier and ScienceDirect. |
| `neurips` | NeurIPS proceedings. |
| `icml` | ICML and PMLR proceedings. |
| `iclr` | ICLR conference headers. |
| `acl` | ACL Anthology, including ACL, EMNLP, and NAACL. |
| `cvf` | CVF open-access papers, including CVPR, ICCV, and WACV. |

Add a venue by extending `VENUES` in `main.py` with a detection expression and narrowly scoped removal patterns. Update `test_main.py` with examples that exercise both the removed boilerplate and prose that must remain.
