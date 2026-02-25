#!/usr/bin/env python3
"""Convert the first 3 top-level PDFs (sorted by filename) to EPUB or Markdown with pdf-craft."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from sympy import marcumq


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert only the first 3 top-level PDFs to EPUB or Markdown files.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Directory to scan for top-level PDFs (default: current directory).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("epubs"),
        help="Directory for generated output files (default: epubs).",
    )
    parser.add_argument(
        "--format",
        choices=["epub", "markdown"],
        default="epub",
        help="Output format: epub or markdown (default: epub).",
    )
    parser.add_argument(
        "--ocr-size",
        choices=["tiny", "small", "base", "large", "gundam"],
        default="small",
        help="OCR model size for pdf-craft.",
    )
    parser.add_argument(
        "--lang",
        choices=["en", "zh"],
        default="en",
        help="Language setting for pdf-craft.",
    )
    parser.add_argument(
        "--models-cache-path",
        type=Path,
        default=Path(".pdf_craft_models"),
        help="Model cache directory for pdf-craft.",
    )
    parser.add_argument(
        "--analysis-root",
        type=Path,
        default=Path(".pdf_craft_work"),
        help="Directory where per-PDF analysis artifacts are stored.",
    )
    parser.add_argument(
        "--latex-render",
        choices=["mathml", "svg", "clipping"],
        default="svg",
        help="How to render LaTeX equations (epub only): mathml, svg (as image), or clipping (default: svg).",
    )
    parser.add_argument(
        "--table-render",
        choices=["html", "clipping"],
        default="html",
        help="How to render tables (epub only): html or clipping (default: html).",
    )
    parser.add_argument(
        "--inline-latex",
        action="store_true",
        default=True,
        help="Preserve inline LaTeX expressions in output (epub only, default: True).",
    )
    parser.add_argument(
        "--no-inline-latex",
        action="store_true",
        help="Disable inline LaTeX preservation.",
    )
    parser.add_argument(
        "--includes-cover",
        action="store_true",
        default=True,
        help="Include cover page (epub only, default: True).",
    )
    parser.add_argument(
        "--no-cover",
        action="store_true",
        help="Exclude cover page.",
    )
    parser.add_argument(
        "--includes-footnotes",
        action="store_true",
        default=True,
        help="Include footnotes (default: False).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print which files would be converted.",
    )
    return parser.parse_args()


def set_writable_cache_env(root: Path) -> None:
    cache_root = root / ".cache"
    hf_root = cache_root / "huggingface"
    mpl_root = cache_root / "matplotlib"
    cache_root.mkdir(parents=True, exist_ok=True)
    hf_root.mkdir(parents=True, exist_ok=True)
    mpl_root.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))
    os.environ.setdefault("HF_HOME", str(hf_root))
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_root))


def pick_pdfs(root: Path) -> list[Path]:
    return sorted(
        [p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"],
        key=lambda p: p.name,
    )


def convert_to_epub(
    pdf: Path,
    output_path: Path,
    analysing_path: Path,
    models_cache: Path,
    args: argparse.Namespace,
) -> None:
    """Convert PDF to EPUB with LaTeX rendering options."""
    from pdf_craft import BookMeta, LaTeXRender, TableRender, transform_epub

    # Map string choices to enum values
    latex_render_map = {
        "mathml": LaTeXRender.MATHML,
        "svg": LaTeXRender.SVG,
        "clipping": LaTeXRender.CLIPPING,
    }
    table_render_map = {
        "html": TableRender.HTML,
        "clipping": TableRender.CLIPPING,
    }

    # Determine flags
    includes_cover = args.includes_cover and not args.no_cover
    inline_latex = args.inline_latex and not args.no_inline_latex

    transform_epub(
        pdf_path=str(pdf),
        epub_path=str(output_path),
        book_meta=BookMeta(title=pdf.stem, authors=["Unknown"]),
        analysing_path=str(analysing_path),
        models_cache_path=str(models_cache),
        ocr_size=args.ocr_size,
        lan=args.lang,
        dpi=600,
        max_page_image_file_size=None,
        includes_cover=includes_cover,
        includes_footnotes=args.includes_footnotes,
        latex_render=latex_render_map[args.latex_render],
        table_render=table_render_map[args.table_render],
        inline_latex=inline_latex,
    )


def convert_to_markdown(
    pdf: Path,
    output_path: Path,
    analysing_path: Path,
    models_cache: Path,
    args: argparse.Namespace,
) -> None:
    """Convert PDF to Markdown."""
    from pdf_craft import transform_markdown

    # Markdown assets (images) go in a subfolder
    assets_path = output_path.parent / f"{output_path.stem}_assets"

    transform_markdown(
        pdf_path=str(pdf),
        markdown_path=str(output_path),
        # markdown_assets_path=str(assets_path),
        dpi=600,
        max_page_image_file_size=None,
        analysing_path=str(analysing_path),
        models_cache_path=str(models_cache),
        ocr_size=args.ocr_size,
        includes_cover=args.includes_cover and not args.no_cover,
        includes_footnotes=args.includes_footnotes,
    )

    # Post-process LaTeX math delimiters for better Markdown rendering
    content = output_path.read_text(encoding="utf-8")
    # Fix escaped backslashes first (\\ -> \)
    content = content.replace("\\\\", "\\")
    # Replace display math delimiters \[...\] with $$...$$
    content = content.replace(r"\[", "$$").replace(r"\]", "$$")
    # Replace inline math delimiters \(...\) with $...$
    content = content.replace(r"\(", "$").replace(r"\)", "$")
    output_path.write_text(content, encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    output_dir = args.output_dir.resolve()
    models_cache = args.models_cache_path.resolve()
    analysis_root = args.analysis_root.resolve()

    set_writable_cache_env(root)

    output_dir.mkdir(parents=True, exist_ok=True)
    models_cache.mkdir(parents=True, exist_ok=True)
    analysis_root.mkdir(parents=True, exist_ok=True)

    pdfs = pick_pdfs(root)
    if not pdfs:
        print(f"No PDFs found in top-level directory: {root}")
        return 1

    print("Selected PDFs:")
    for pdf in pdfs:
        print(f"- {pdf.name}")

    if args.dry_run:
        return 0

    for pdf in pdfs:
        analysing_path = analysis_root / pdf.stem
        analysing_path.mkdir(parents=True, exist_ok=True)

        if args.format == "epub":
            output_path = output_dir / f"{pdf.stem}.epub"
            print(f"Converting to EPUB: {pdf.name}")
            print(f"  LaTeX render: {args.latex_render}")
            print(f"  Table render: {args.table_render}")
            convert_to_epub(pdf, output_path, analysing_path, models_cache, args)
        else:
            output_path = output_dir / f"{pdf.stem}.md"
            print(f"Converting to Markdown: {pdf.name}")
            convert_to_markdown(pdf, output_path, analysing_path, models_cache, args)

        print(f"Created: {output_path}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
