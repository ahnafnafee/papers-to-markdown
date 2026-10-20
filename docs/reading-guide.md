# Reading copies

[← README](../README.md) · [Batch libraries](batch-guide.md) · [Kindle delivery](send-to-kindle.md)

Reading mode produces a single-column PDF and a reflowable EPUB from one shared document. It retains the paper's prose, source-page locations, figures, tables, and mathematical notation, with a report of material that needs comparison against the original.

## Requirements

- **uv and Python 3.13.** `read.py` and its adjacent lockfile define an isolated environment. Run the commands from the repository root.
- **Installed Verdana.** The expected files are `verdana.ttf`, `verdanab.ttf`, `verdanai.ttf`, and `verdanaz.ttf`. Windows uses its system Fonts folder by default. On another installation, pass `--font-dir "/path/to/installed/verdana"`. The font's embedding permissions are checked; font binaries are not included in the repository.
- **XeLaTeX for PDFs.** Install TeX Live or MiKTeX and make `xelatex` available on your path. The template uses `fontspec`, `geometry`, `microtype`, `graphicx`, `adjustbox`, `needspace`, `fancyhdr`, `lastpage`, `xurl`, and `hyperref`. Latin Modern Math supplies additional symbols when found through `kpsewhich`.
- **Local model storage.** The first conversion downloads layout and OCR models. CUDA is used when available; `--device cpu` selects CPU processing. Windows is the tested platform. Other operating systems require their own compatible Torch, font, and TeX installations.

EPUB-only output does not invoke XeLaTeX. Verdana is still used for the cover and document rendering, including when `--no-embed-fonts` leaves the final EPUB's font choice to the reader.

## Convert a paper

```powershell
uv run --locked --script read.py "C:\Papers\example.pdf"
uv run --locked --script read.py "C:\Papers\example.pdf" --format epub
uv run --locked --script read.py "C:\Papers\example.pdf" --output-dir "C:\Reading Copies"
```

`--format reading` is the default and exports both PDF and EPUB. Folder selection stays at the top level unless `--recursive` is supplied. Use `--dry-run` to inspect selected inputs without loading conversion models.

## Typography

The `verdana-reading` profile uses 13 pt body text, a 20 pt baseline, and 10 pt paragraph gaps. The US Letter page has 1.05-inch side margins, a 0.90-inch top margin, and a 0.85-inch bottom margin. Paragraphs have no first-line indent and align to the left.

```powershell
uv run --locked --script read.py "C:\Papers\example.pdf" --font-size 14 --line-spacing 22 --paragraph-spacing 11
```

The EPUB uses corresponding relative spacing. It embeds the four Verdana faces by default with EPUB font obfuscation. `--no-embed-fonts` omits font embedding. A Kindle's publisher-font setting may be needed to use document fonts, and reader controls can change the final spacing.

EPUBs have a 1600 × 2560 portrait cover, a cover page, visible contents, hierarchical EPUB navigation, and NCX compatibility navigation. Covers use the selected title and available author metadata. Section links, numeric citations, and recovered footnotes are linked where a target can be identified unambiguously; author-year citations retain their text.

## Metadata

The title is selected from source headings, the filename, and PDF metadata. PDF author metadata may be absent or inaccurate. For a single input, override it explicitly:

```powershell
uv run --locked --script read.py "C:\Papers\example.pdf" --title "Example Paper" --author "First Author" --author "Second Author" --name "Example-Paper"
```

`--name` controls the output basename. For multiple papers, use `--metadata overrides.json`, an object keyed by the complete source SHA-256:

```json
{
  "<source-sha256>": {
    "title": "Example Paper",
    "authors": ["First Author", "Second Author"],
    "name": "Example-Paper"
  }
}
```

## OCR and source fidelity

`--ocr auto` enables OCR for pages with sparse native text, including scanned bodies surrounded by text watermarks. `--ocr never` disables it, and `--ocr always` requests full-page OCR. The reading OCR configuration currently uses English.

Figures, tables, and display equations are kept as original source crops. Native text alignment recovers emphasis, scripts, and supported inline notation. Unsupported glyphs and unreliable character maps use original renderings and produce review notes. These passages keep their source appearance and may have limited searchability or reflow.

A native text reader can occasionally miss pages in a malformed PDF page tree. The reader can rebuild a temporary compatible copy and compare every rendered page with the original before using it. Original inputs are never replaced. Whole pages omitted by layout extraction require the explicit [library repair workflow](batch-guide.md#recovery-and-final-checks).

## Output and review

```text
output/pdf/Example-Paper--<hash-prefix>/
├── Example-Paper.reading.pdf
├── Example-Paper.epub
├── preview.html
├── assets/
├── assets-manifest.json
├── tex/
└── conversion-report.json
```

PDF-only or EPUB-only runs contain the requested publication plus their supporting files. `tex/` is produced for PDF output. Intermediate extraction stays under `.cache/reading/` by default.

The report records source pages, provenance checks, output hashes, extracted-text comparisons, and review reasons. `ready` means the automated checks reported no outstanding issues. `needs_review` means publications were created but flagged passages should be compared with the original. Neither status substitutes for checking mathematical or scientific content. Source crops preserve the original typography where reliable reformatting is not possible.

Use `--force` to rerender from cached extraction, or `--reextract` to run extraction again. Receipts and assets are checked by hash before a completed result is reused. Changing spacing does not repeat layout inference.

## Compatibility entry point

```powershell
uv run main.py --format reading --files "C:\Papers\example.pdf"
uv run main.py --format epub --profile verdana-reading --files "C:\Papers\example.pdf"
```

These commands delegate to the isolated reading environment. Existing Markdown and EPUB commands without the reading profile retain their previous converter.
