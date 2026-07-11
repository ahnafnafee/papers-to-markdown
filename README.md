# Papers to Markdown - Academic PDF Converter

Convert academic papers from PDF to Markdown or EPUB format with AI-powered OCR, LaTeX support, and venue-aware cleanup.

## 🚀 Features

- **AI-Powered PDF Conversion**: Transform research papers and academic PDFs into clean Markdown or EPUB formats
- **Venue-Aware Cleanup**: Auto-detects the publisher (ACM, IEEE, arXiv, Springer, Elsevier, NeurIPS, ICML, ICLR, ACL, CVF) and strips its boilerplate — copyright blocks, Xplore footers, digital-library preambles, page headers
- **Markdown Post-Processing**: Normalizes LaTeX delimiters to `$...$` / `$$...$$` without corrupting formulas, and repairs OCR hyphenation artifacts (`Processing- based` → `Processing-based`)
- **LaTeX Equation Support**: Preserve mathematical equations with multiple rendering options (MathML, SVG, or clipping)
- **Smart Table Extraction**: Convert complex tables to HTML or image clipping
- **Multi-Language OCR**: Support for English and Chinese documents
- **Configurable OCR Models**: Choose from tiny to gundam-sized models based on your accuracy needs
- **Batch Processing**: Processes every top-level PDF, skips already-converted files, and keeps going if one PDF fails
- **GPU Acceleration**: Leverages PyTorch and CUDA for fast processing

## 📋 Requirements

- Python 3.13 or higher
- CUDA-compatible GPU (recommended for faster processing)
- UV package manager

## 🔧 Installation

1. Clone this repository:

```bash
git clone https://github.com/ahnafnafee/papers-to-markdown.git
cd papers-to-markdown
```

2. Install dependencies using UV:

```bash
uv sync
```

3. Activate the virtual environment:

```bash
# Windows
.venv\Scripts\activate

# Linux/Mac
source .venv/bin/activate
```

## 💻 Usage

### Basic Markdown Conversion

Drop PDFs in the project root and run — Markdown is the default format, output goes to `markdown/`:

```bash
uv run main.py
```

### EPUB Conversion

```bash
uv run main.py --format epub
```

### Venue Cleanup Control

Boilerplate stripping is auto-detected by default. Force a specific venue or turn it off:

```bash
uv run main.py --venue ieee    # force IEEE cleanup rules
uv run main.py --venue none    # keep the raw OCR output
```

### Advanced Options

```bash
uv run main.py \
  --root . \
  --format markdown \
  --output-dir output \
  --ocr-size base \
  --venue auto \
  --no-footnotes \
  --force
```

## ⚙️ Configuration Options

| Option                          | Values                                                | Default              | Description                                     |
| ------------------------------- | ----------------------------------------------------- | -------------------- | ----------------------------------------------- |
| `--root`                        | Path                                                  | `.`                  | Directory containing PDF files                  |
| `--output-dir`                  | Path                                                  | `markdown` / `epubs` | Output directory (picked by format)             |
| `--format`                      | `markdown`, `epub`                                    | `markdown`           | Output format                                   |
| `--venue`                       | `auto`, `none`, or a venue key (see below)            | `auto`               | Publisher boilerplate stripping (Markdown only) |
| `--ocr-size`                    | `tiny`, `small`, `base`, `large`, `gundam`            | `small`              | OCR model size (larger = more accurate)         |
| `--lang`                        | `en`, `zh`                                            | `en`                 | Document language (EPUB only)                   |
| `--latex-render`                | `mathml`, `svg`, `clipping`                           | `svg`                | LaTeX equation rendering mode (EPUB only)       |
| `--table-render`                | `html`, `clipping`                                    | `html`               | Table rendering mode (EPUB only)                |
| `--cover` / `--no-cover`        | Flag                                                  | on                   | Include the cover page                          |
| `--footnotes` / `--no-footnotes`| Flag                                                  | on                   | Extract footnotes and references                |
| `--inline-latex` / `--no-inline-latex` | Flag                                          | on                   | Preserve inline LaTeX (EPUB only)               |
| `--force`                       | Flag                                                  | off                  | Re-convert PDFs whose output already exists     |
| `--models-cache-path`           | Path                                                  | `.pdf_craft_models`  | Model cache directory                           |
| `--analysis-root`               | Path                                                  | `.pdf_craft_work`    | Work directory for analysis artifacts           |
| `--dry-run`                     | Flag                                                  | off                  | Preview files without converting                |

## 🏛️ Supported Venues

Markdown output is scanned for publisher fingerprints; every venue that matches gets its boilerplate stripped. Use `--venue <key>` to force one, `--venue none` to disable.

| Key        | Covers                                                                  |
| ---------- | ----------------------------------------------------------------------- |
| `acm`      | ACM Digital Library — CHI, UIST, CSCW, IMWUT, TOCHI, and other ACM venues |
| `ieee`     | IEEE Xplore conferences and journals                                    |
| `arxiv`    | arXiv preprints (margin stamp, "Preprint. Under review.")               |
| `springer` | Springer / LNCS / Springer Nature journals                              |
| `elsevier` | Elsevier / ScienceDirect journals                                       |
| `neurips`  | NeurIPS proceedings footers                                             |
| `icml`     | ICML / PMLR proceedings footers                                         |
| `iclr`     | ICLR page headers ("Published as a conference paper at ICLR ...")       |
| `acl`      | ACL Anthology — ACL, EMNLP, NAACL, and other ACL venues                  |
| `cvf`      | CVF open access — CVPR, ICCV, WACV watermark notices                    |

Adding a venue is a small edit: append an entry with a `detect` regex and `strip` patterns to the `VENUES` dict in `main.py`, and it automatically shows up as a `--venue` choice. Run `python test_main.py` to sanity-check the cleanup rules.

## 📁 Project Structure

```
papers-to-markdown/
├── main.py                # Main conversion script (pipeline + venue rules)
├── test_main.py           # Self-check for the post-processing rules
├── pyproject.toml         # Project dependencies (UV)
├── README.md              # This file
├── .gitignore             # Git ignore rules
├── .venv/                 # Virtual environment
├── .cache/                # HuggingFace and matplotlib cache
├── .pdf_craft_models/     # Cached OCR models
├── .pdf_craft_work/       # Temporary analysis files
└── markdown/              # Output directory for Markdown files
    └── assets/            # Extracted figures (hash-named, shared across papers)
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

This project uses [pdf-craft](https://github.com/oomol-lab/pdf-craft) for PDF processing.

## 🔍 Keywords

academic paper converter, PDF to Markdown, PDF to EPUB, research paper OCR, LaTeX equation preservation, academic document processing, AI PDF converter, scientific paper conversion, Python PDF tool, batch PDF conversion, ACM, IEEE, arXiv, NeurIPS

## 📞 Support

For issues and questions, please open an issue on GitHub.

---

**Made with ❤️ for researchers and academics**
