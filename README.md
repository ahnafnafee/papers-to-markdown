# Papers to Markdown - Academic PDF Converter

Convert academic papers from PDF to Markdown or EPUB format with AI-powered OCR and LaTeX support.

## 🚀 Features

- **AI-Powered PDF Conversion**: Transform research papers and academic PDFs into clean Markdown or EPUB formats
- **LaTeX Equation Support**: Preserve mathematical equations with multiple rendering options (MathML, SVG, or clipping)
- **Smart Table Extraction**: Convert complex tables to HTML or image clipping
- **Multi-Language OCR**: Support for English and Chinese documents
- **Configurable OCR Models**: Choose from tiny to gundam-sized models based on your accuracy needs
- **Batch Processing**: Automatically process multiple PDFs in your directory
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

Convert PDFs to Markdown format:

```bash
uv run main.py --format markdown --output-dir markdown
```

### EPUB Conversion

Convert PDFs to EPUB format:

```bash
uv run main.py --format epub --output-dir epubs
```

### Advanced Options

```bash
uv run main.py \
  --root . \
  --format markdown \
  --output-dir output \
  --ocr-size base \
  --lang en \
  --latex-render svg \
  --table-render html
```

## ⚙️ Configuration Options

| Option                | Values                                     | Default             | Description                               |
| --------------------- | ------------------------------------------ | ------------------- | ----------------------------------------- |
| `--root`              | Path                                       | `.`                 | Directory containing PDF files            |
| `--output-dir`        | Path                                       | `epubs`             | Output directory for converted files      |
| `--format`            | `epub`, `markdown`                         | `epub`              | Output format                             |
| `--ocr-size`          | `tiny`, `small`, `base`, `large`, `gundam` | `small`             | OCR model size (larger = more accurate)   |
| `--lang`              | `en`, `zh`                                 | `en`                | Document language                         |
| `--latex-render`      | `mathml`, `svg`, `clipping`                | `svg`               | LaTeX equation rendering mode (EPUB only) |
| `--table-render`      | `html`, `clipping`                         | `html`              | Table rendering mode (EPUB only)          |
| `--models-cache-path` | Path                                       | `.pdf_craft_models` | Model cache directory                     |
| `--analysis-root`     | Path                                       | `.pdf_craft_work`   | Work directory for analysis artifacts     |
| `--dry-run`           | Flag                                       | `False`             | Preview files without converting          |

## 📁 Project Structure

```
papers-to-markdown/
├── main.py                 # Main conversion script
├── pyproject.toml         # Project dependencies (UV)
├── README.md              # This file
├── .gitignore            # Git ignore rules
├── .venv/                # Virtual environment
├── .cache/               # HuggingFace and matplotlib cache
├── .pdf_craft_models/    # Cached OCR models
├── .pdf_craft_work/      # Temporary analysis files
└── markdown/             # Output directory for Markdown files
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📝 License

This project uses [pdf-craft](https://github.com/KAIST-VICLab/pdf-craft) for PDF processing.

## 🔍 Keywords

academic paper converter, PDF to Markdown, PDF to EPUB, research paper OCR, LaTeX equation preservation, academic document processing, AI PDF converter, scientific paper conversion, Python PDF tool, batch PDF conversion

## 📞 Support

For issues and questions, please open an issue on GitHub.

---

**Made with ❤️ for researchers and academics**
