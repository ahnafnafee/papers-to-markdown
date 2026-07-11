#!/usr/bin/env python3
"""Convert every PDF in the input directory (papers/) to Markdown or EPUB.

Markdown output is post-processed: LaTeX delimiters are normalized to $/$$,
OCR hyphenation artifacts are repaired, and publisher boilerplate is stripped
for detected venues (ACM, IEEE, arXiv, ...).
"""

import argparse
import os
import re
from pathlib import Path

# Venue boilerplate registry. A venue's `strip` patterns (re.MULTILINE) run only
# when its `detect` regex matches the document, or when --venue forces it.
# Patterns are line-anchored on purpose: whole-line matches cannot eat body
# prose, and reference entries are `[^n]:`-prefixed so they never match.
# ponytail: covers first-page and footer junk; running headers with venue/date
# lines (e.g. "UIST '25, ..., Busan") vary too much to match safely and stay in.
VENUES: dict[str, dict] = {
    "acm": {
        "name": "ACM (CHI, UIST, CSCW, IMWUT, TOCHI, ...)",
        "detect": r"dl\.acm\.org|ACM Reference Format|ACM ISBN|permissions@acm\.org",
        "strip": [
            # ACM DL e-reader preamble: everything before the real H1 title
            r"\ALatest updates: https://dl\.acm\.org[\s\S]*?(?=^# )",
            r"^Latest updates: https://dl\.acm\.org\S*$",
            r"^RESEARCH-?\s?ARTICLE$",
            r"^Check for updates$",
            r"^View all$",
            r"^Open Access Support provided by:$",
            r"Permission to make digital or hard copies[\s\S]{0,1200}?permissions@acm\.org\.?",
            r"^© \d{4} Copyright (?:is )?held by the owner/author\(s\).*$",
            r"^ACM ISBN .*$",
        ],
    },
    "ieee": {
        "name": "IEEE (Xplore conferences and journals)",
        "detect": r"ieeexplore\.ieee\.org|IEEE Xplore|© ?\d{4} IEEE|/\\?\$31\.00",
        "strip": [
            r"^Authorized licensed use limited to:.*$",
            r"^\d{3}-\d-\d{4}-\d{4}-\d/\d{2}/\\?\$\d+\.\d{2} ?© ?\d{4} IEEE$",
            r"© \d{4} IEEE\. Personal use of this material is permitted[\s\S]{0,800}?other works\.",
            r"^Digital Object Identifier .*$",
        ],
    },
    "arxiv": {
        "name": "arXiv preprints",
        "detect": r"arXiv:\d{4}\.\d{4,5}",
        "strip": [
            r"^arXiv:\d{4}\.\d{4,5}v?\d*\s*\[[^\]\n]+\]\s*\d{1,2} [A-Z][a-z]{2,8} \d{4}\.?$",
            r"^Preprint\. Under review\.$",
        ],
    },
    "springer": {
        "name": "Springer (LNCS, Springer Nature journals)",
        "detect": r"Springer Nature|link\.springer\.com|Lecture Notes in Computer Science",
        "strip": [
            r"^© (?:The )?Author\(s\).*Springer.*$",
            r"^© Springer.*\d{4}.*$",
        ],
    },
    "elsevier": {
        "name": "Elsevier (ScienceDirect journals)",
        "detect": r"ScienceDirect|journal homepage: (?:www\.)?elsevier",
        "strip": [
            r"^Contents lists available at ScienceDirect$",
            r"^journal homepage:.*$",
        ],
    },
    "neurips": {
        "name": "NeurIPS",
        "detect": r"Neural Information Processing Systems|NeurIPS",
        "strip": [
            r"^\d+(?:st|nd|rd|th) Conference on Neural Information Processing Systems.*$",
        ],
    },
    "icml": {
        "name": "ICML (PMLR proceedings)",
        "detect": r"International Conference on Machine Learning|PMLR \d+",
        "strip": [
            r"^Proceedings of the \d+\s?(?:st|nd|rd|th) International Conference on Machine Learning.*$",
            r"^Copyright \d{4} by the author\(s\)\.?$",
        ],
    },
    "iclr": {
        "name": "ICLR",
        "detect": r"conference paper at ICLR",
        "strip": [
            r"^(?:Published as a conference paper|Under review as a conference paper) at ICLR \d{4}\.?$",
        ],
    },
    "acl": {
        "name": "ACL Anthology (ACL, EMNLP, NAACL, ...)",
        "detect": r"Association for Computational Linguistics|aclanthology\.org",
        "strip": [
            r"^Proceedings of the .*, pages \d+.*$",
            r"^© ?\d{4} Association for Computational Linguistics$",
        ],
    },
    "cvf": {
        "name": "CVF open access (CVPR, ICCV, WACV)",
        "detect": r"Computer Vision Foundation",
        "strip": [
            r"^This (?:CVPR|ICCV|WACV) paper is the Open Access version, provided by the Computer Vision Foundation\.?$",
            r"^Except for this watermark, it is identical to the accepted version.*$",
            r"^The final published version of the proceedings is available on IEEE Xplore\.?$",
        ],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert every PDF in the input directory to Markdown or EPUB files."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("papers"),
        help="Directory to scan for PDFs (default: papers).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for generated files (default: output/markdown or output/epubs by format).",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "epub"],
        default="markdown",
        help="Output format (default: markdown).",
    )
    parser.add_argument(
        "--venue",
        choices=["auto", "none", *VENUES],
        default="auto",
        help="Publisher boilerplate to strip from markdown output: "
        "auto = detect, none = keep everything (markdown only, default: auto).",
    )
    parser.add_argument(
        "--ocr-size",
        choices=["tiny", "small", "base", "large", "gundam"],
        default="small",
        help="OCR model size for pdf-craft (default: small).",
    )
    parser.add_argument(
        "--lang",
        choices=["en", "zh"],
        default="en",
        help="Document language (epub only, default: en).",
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
        help="How to render LaTeX equations (epub only, default: svg).",
    )
    parser.add_argument(
        "--table-render",
        choices=["html", "clipping"],
        default="html",
        help="How to render tables (epub only, default: html).",
    )
    parser.add_argument(
        "--cover",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include the cover page.",
    )
    parser.add_argument(
        "--footnotes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Extract footnotes and references.",
    )
    parser.add_argument(
        "--inline-latex",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Preserve inline LaTeX expressions (epub only).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-convert PDFs whose output file already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print which files would be converted.",
    )
    return parser.parse_args()


def set_writable_cache_env() -> None:
    # Anchored to the working directory, not the PDF input directory.
    cache_root = Path(".cache").resolve()
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


def normalize_math_delimiters(content: str) -> str:
    r"""Rewrite \(...\) as $...$ and \[...\] as $$...$$ so math renders in plain
    Markdown viewers. Only the delimiters change: formula bodies (including \\
    row breaks) pass through verbatim, and the lookbehinds skip escaped
    literals like \\[. A blanket string replace here corrupts formulas."""
    content = re.sub(
        r"(?<!\\)\\\[(.{0,4000}?)(?<!\\)\\\]", r"$$\1$$", content, flags=re.DOTALL
    )
    content = re.sub(r"(?<!\\)\\\((.{0,800}?)(?<!\\)\\\)", r"$\1$", content)
    return content


def postprocess_markdown(content: str, venue: str) -> tuple[str, list[str]]:
    """Return (cleaned markdown, applied venue ids)."""
    content = normalize_math_delimiters(content)
    # OCR hyphenation artifact: "Processing- based" -> "Processing-based".
    # \w on both sides keeps list bullets ("- item") and " - " dashes intact;
    # the lookahead keeps runs like "Whac- A- Mole" fixable in one pass.
    content = re.sub(r"(\w)- (?=\w)", r"\1-", content)

    if venue == "none":
        applied = []
    elif venue == "auto":
        applied = [vid for vid, v in VENUES.items() if re.search(v["detect"], content)]
    else:
        applied = [venue]
    for vid in applied:
        for pattern in VENUES[vid]["strip"]:
            content = re.sub(pattern, "", content, flags=re.MULTILINE)

    content = re.sub(r"[ \t]+$", "", content, flags=re.MULTILINE)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip("\n") + "\n", applied


def convert_to_epub(
    pdf: Path,
    output_path: Path,
    analysing_path: Path,
    models_cache: Path,
    args: argparse.Namespace,
) -> None:
    """Convert PDF to EPUB; book metadata is extracted from the PDF itself."""
    from pdf_craft import LaTeXRender, TableRender, transform_epub

    latex_render_map = {
        "mathml": LaTeXRender.MATHML,
        "svg": LaTeXRender.SVG,
        "clipping": LaTeXRender.CLIPPING,
    }
    table_render_map = {
        "html": TableRender.HTML,
        "clipping": TableRender.CLIPPING,
    }

    transform_epub(
        pdf_path=str(pdf),
        epub_path=str(output_path),
        analysing_path=str(analysing_path),
        models_cache_path=str(models_cache),
        ocr_size=args.ocr_size,
        lan=args.lang,
        dpi=600,
        includes_cover=args.cover,
        includes_footnotes=args.footnotes,
        latex_render=latex_render_map[args.latex_render],
        table_render=table_render_map[args.table_render],
        inline_latex=args.inline_latex,
    )


def convert_to_markdown(
    pdf: Path,
    output_path: Path,
    analysing_path: Path,
    models_cache: Path,
    args: argparse.Namespace,
) -> None:
    """Convert PDF to Markdown, then clean the output."""
    from pdf_craft import transform_markdown

    # Images land in <output-dir>/assets/ (hash-named), linked relative to the file.
    transform_markdown(
        pdf_path=str(pdf),
        markdown_path=str(output_path),
        analysing_path=str(analysing_path),
        models_cache_path=str(models_cache),
        ocr_size=args.ocr_size,
        dpi=600,
        includes_cover=args.cover,
        includes_footnotes=args.footnotes,
    )

    content = output_path.read_text(encoding="utf-8")
    content, applied = postprocess_markdown(content, args.venue)
    output_path.write_text(content, encoding="utf-8")
    print(f"  Venue boilerplate stripped: {', '.join(applied) if applied else 'none detected'}")


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        print(f"PDF input directory not found: {root}")
        return 1
    output_dir = (
        args.output_dir
        or Path("output/epubs" if args.format == "epub" else "output/markdown")
    ).resolve()
    models_cache = args.models_cache_path.resolve()
    analysis_root = args.analysis_root.resolve()

    set_writable_cache_env()

    output_dir.mkdir(parents=True, exist_ok=True)
    models_cache.mkdir(parents=True, exist_ok=True)
    analysis_root.mkdir(parents=True, exist_ok=True)

    pdfs = pick_pdfs(root)
    if not pdfs:
        print(f"No PDFs found in {root} — drop your PDFs there and rerun.")
        return 1

    print("Selected PDFs:")
    for pdf in pdfs:
        print(f"- {pdf.name}")

    if args.dry_run:
        return 0

    suffix = ".epub" if args.format == "epub" else ".md"
    failures = 0
    for pdf in pdfs:
        output_path = output_dir / (pdf.stem + suffix)
        if output_path.exists() and not args.force:
            print(f"Skipping (output exists, use --force): {output_path.name}")
            continue

        analysing_path = analysis_root / pdf.stem
        analysing_path.mkdir(parents=True, exist_ok=True)
        print(f"Converting to {args.format}: {pdf.name}")
        try:
            if args.format == "epub":
                convert_to_epub(pdf, output_path, analysing_path, models_cache, args)
            else:
                convert_to_markdown(pdf, output_path, analysing_path, models_cache, args)
        except Exception as error:  # one bad PDF must not kill the batch
            failures += 1
            print(f"FAILED: {pdf.name}: {error}")
            continue
        print(f"Created: {output_path}")

    if failures:
        print(f"Done with {failures} failure(s) out of {len(pdfs)} PDF(s).")
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
