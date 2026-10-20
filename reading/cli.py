"""Command-line interface for reading copies."""

import argparse
import fnmatch
import json
import sys
from pathlib import Path

from .model import ReadingProfile, digest


def select_inputs(inputs: list[Path], recursive: bool = False, exclusions: list[str] | None = None,
                  output_root: Path | None = None) -> list[Path]:
    result, seen = [], set()
    for entry in inputs:
        entry = entry.resolve(strict=True)
        candidates = ([entry] if entry.is_file() else sorted(entry.rglob("*") if recursive else entry.iterdir()))
        for path in candidates:
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            relative = path.relative_to(entry) if entry.is_dir() else Path(path.name)
            if any(fnmatch.fnmatch(relative.as_posix(), pattern) or pattern in relative.parts
                   for pattern in exclusions or []):
                continue
            if entry.is_dir() and output_root and path.resolve().is_relative_to(output_root.resolve()):
                continue
            identity = digest(path)
            if identity not in seen:
                seen.add(identity)
                result.append(path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create Verdana reading PDFs and Kindle EPUBs.")
    parser.add_argument("inputs", type=Path, nargs="*", help="PDF files or directories.")
    parser.add_argument("--root", type=Path, help="Additional input directory (default: papers).")
    parser.add_argument("--output-dir", type=Path, default=Path("output/pdf"))
    parser.add_argument("--work-dir", type=Path, default=Path(".cache/reading"))
    parser.add_argument("--format", choices=["reading", "pdf", "epub"], default="reading")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--exclude", action="append", default=[], help="Relative glob or directory name to exclude.")
    parser.add_argument("--font-size", type=float, default=13)
    parser.add_argument("--line-spacing", type=float, default=20)
    parser.add_argument("--paragraph-spacing", type=float, default=10)
    parser.add_argument("--font-dir", type=Path, help="Folder containing installed Verdana font files.")
    parser.add_argument("--no-embed-fonts", action="store_true", help="Use the Kindle's selected fonts instead.")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"], default="auto")
    parser.add_argument("--ocr", choices=["auto", "never", "always"], default="auto")
    parser.add_argument("--force", action="store_true", help="Rerender outputs, reusing extraction.")
    parser.add_argument("--reextract", action="store_true", help="Discard cached extraction and process the PDF again.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=1,
                        help="CPU export processes, with one shared inference process (1-16).")
    parser.add_argument("--checkpoint", type=Path, help="Batch progress file; completed outputs are verified on resume.")
    parser.add_argument("--epubcheck", type=Path, help="Optional EPUBCheck JAR for formal validation in parallel mode.")
    parser.add_argument("--send-to-kindle", action="store_true", help="Send ready EPUBs to your Kindle library after conversion.")
    parser.add_argument("--kindle-state-dir", type=Path, help="Use a separate local Kindle sign-in profile.")
    parser.add_argument("--title", help="Override metadata title when selecting one PDF.")
    parser.add_argument("--author", action="append", help="Override metadata authors when selecting one PDF.")
    parser.add_argument("--name", help="Short output basename when selecting one PDF.")
    parser.add_argument("--metadata", type=Path, help="JSON object keyed by source SHA-256, with title/authors/name overrides.")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be between 1 and 16.")
    if args.send_to_kindle and args.format == "pdf":
        parser.error("--send-to-kindle needs reading or epub output.")
    try:
        profile = ReadingProfile(font_size=args.font_size, line_spacing=args.line_spacing,
                                 paragraph_spacing=args.paragraph_spacing)
        entries = args.inputs + ([args.root] if args.root else [])
        sources = select_inputs(entries or [Path("papers")], args.recursive, args.exclude, args.output_dir)
        if not sources:
            parser.error("No PDF files selected.")
        if len(sources) != 1 and (args.title or args.author or args.name):
            parser.error("--title, --author, and --name require exactly one input PDF; use --metadata for a batch.")
        metadata = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else {}
        if not isinstance(metadata, dict):
            raise ValueError("The metadata file must contain an object keyed by source hashes.")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    for source in sources:
        print(f"Selected: {source}", flush=True)
    if args.dry_run:
        if args.send_to_kindle:
            print("Ready EPUBs would be sent after conversion. No conversion or upload made.")
        return 0
    from .pipeline import convert

    if args.workers > 1 or args.checkpoint or args.epubcheck:
        from .batch import run_batch
        overrides = dict(metadata)
        if len(sources) == 1:
            overrides[digest(sources[0])] = {**overrides.get(digest(sources[0]), {}),
                **{k: v for k, v in {"title": args.title, "authors": args.author, "name": args.name}.items()
                   if v is not None}}
        state = run_batch(sources, args.output_dir, args.work_dir, profile, workers=args.workers,
            options={"formats": ("pdf", "epub") if args.format == "reading" else (args.format,),
                     "force": args.force, "reextract": args.reextract, "ocr": args.ocr,
                     "device": args.device, "font_dir": args.font_dir,
                     "embed_fonts": not args.no_embed_fonts},
            metadata=overrides, checkpoint=args.checkpoint, epubcheck=args.epubcheck)
        delivery_failures = 0
        if args.send_to_kindle:
            from kindle_delivery.launch import send_epubs
            for source, item in state["documents"].items():
                if item["status"] == "needs_review":
                    delivery_failures += 1
                    print(f"Kindle delivery skipped until review: {Path(source).name}",
                          file=sys.stderr, flush=True)
            paths = [Path(item["folder"]) / item["outputs"]["epub"]["filename"]
                     for item in state["documents"].values() if item["status"] == "ready"]
            if paths:
                try:
                    if send_epubs(paths, state_dir=args.kindle_state_dir):
                        delivery_failures += 1
                except (OSError, RuntimeError) as error:
                    delivery_failures += 1
                    print(f"Kindle: {error}", file=sys.stderr, flush=True)
        return 1 if state["failed"] or delivery_failures else 0

    failures = 0
    delivery_paths = []
    for source in sources:
        try:
            overrides = metadata.get(digest(source), {})
            if not isinstance(overrides, dict) or set(overrides) - {"title", "authors", "name"}:
                raise ValueError("Metadata overrides allow only title, authors, and name.")
            folder, report = convert(source, args.output_dir, args.work_dir, profile,
                formats=("pdf", "epub") if args.format == "reading" else (args.format,),
                force=args.force, reextract=args.reextract, ocr=args.ocr, device=args.device,
                font_dir=args.font_dir, embed_fonts=not args.no_embed_fonts,
                title=args.title or overrides.get("title"), authors=args.author or overrides.get("authors"),
                name=args.name or overrides.get("name"))
            print(f"{'Needs review' if report['review'] else 'Ready'}: {folder}", flush=True)
            if args.send_to_kindle:
                if report['review']:
                    failures += 1
                    print(f"Kindle delivery skipped until review: {source.name}", file=sys.stderr, flush=True)
                else:
                    delivery_paths.append(folder / report['outputs']['epub']['filename'])
        except Exception as error:
            failures += 1
            print(f"Failed: {source.name}: {error}", file=sys.stderr, flush=True)
    if delivery_paths:
        from kindle_delivery.launch import send_epubs

        try:
            if send_epubs(delivery_paths, state_dir=args.kindle_state_dir):
                failures += 1
        except (OSError, RuntimeError) as error:
            failures += 1
            print(f"Kindle: {error}", file=sys.stderr, flush=True)
    return 1 if failures else 0
