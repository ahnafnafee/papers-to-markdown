"""Retry failed library items and retain original pages missed by layout extraction."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reading.batch import run_batch
from reading.extract import _crop
from reading.source import open_native_pdf
from reading.library import publish_paper, write_catalog
from reading.model import Block, Document, ReadingProfile, Region, atomic_json
from reading.validate import expected_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("output/pdf/library"))
    parser.add_argument("--work-dir", type=Path, default=Path(".cache/reading"))
    parser.add_argument("--publish-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--epubcheck", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    progress = args.output_dir / "batch-progress.json"
    state = json.loads(progress.read_text(encoding="utf-8"))
    if not state["complete"]:
        raise ValueError("Finish the active batch before repairing its saved outputs.")
    selected = []
    source_pages = {entry["source"]: entry["pages"] for entry in inventory["documents"]}
    for source_name, item in state["documents"].items():
        source = Path(source_name)
        if source.resolve().parent != Path(inventory["root"]).resolve():
            raise ValueError("Repair input is outside the top-level source directory.")
        if item["status"] == "failed":
            selected.append(source)
            continue
        report = json.loads((Path(item["folder"]) / "conversion-report.json").read_text(encoding="utf-8"))
        canonical = Path(report["extraction_cache"]) / "document.json"
        if report["source_pages"] != source_pages[source_name]:
            backup = canonical.with_name("document.before-physical-page-repair.json")
            if not backup.exists():
                backup.write_bytes(canonical.read_bytes())
            canonical.unlink()
            selected.append(source)
            continue
        document = Document.from_dict(json.loads(canonical.read_text(encoding="utf-8")))
        pages = sorted({r["page"] for r in document.review
                        if r["reason"] == "No reading content extracted from this page"})
        math_blocks = set()
        tex = Path(item["folder"]) / "tex/reading.tex"
        lines = tex.read_text(encoding="utf-8").splitlines()
        import re
        for warning in report["outputs"]["pdf"]["overfull_boxes"]:
            match = re.search(r"at lines (\d+)--(\d+)", warning)
            if not match:
                continue
            index = int(match[1]) - 1
            if r"\includegraphics" not in lines[index]:
                continue
            for prior in reversed(lines[:index + 1]):
                if re.fullmatch(r"% b\d+", prior):
                    math_blocks.add(prior[2:])
                    break
        if not pages and not math_blocks:
            continue
        import pdfplumber
        backup = canonical.with_name("document.before-page-recovery.json")
        if not backup.exists():
            atomic_json(backup, document.to_dict())
        with open_native_pdf(source) as pdf:
            for block in document.blocks:
                if block.id not in math_blocks:
                    continue
                text_crop = not block.asset
                if block.asset:
                    block.regions.extend(block.caption_regions)
                _crop(block, pdf, canonical.parent)
                block.caption, block.spans = "", []
                if text_crop:
                    sizes = [c["size"] for region in block.regions
                             for c in pdf.pages[region.page - 1].crop(tuple(region.bbox)).chars if c["size"] > 0]
                    block.asset_role = "source_text"
                    block.asset_text_size = sorted(sizes)[len(sizes) // 2] if sizes else 10
                for note in document.blocks:
                    note.backlinks = [b for b in note.backlinks if b["block"] != block.id]
                document.review.append({"block": block.id, "page": block.regions[0].page,
                    "reason": "Original source passage retained for mathematical notation that could not fit a reflowed line"})
            for number in pages:
                page = pdf.pages[number - 1]
                block = Block(id=f"original-page-{number:04}", kind="figure",
                    regions=[Region(number, [0, 0, float(page.width), float(page.height)],
                                    [float(page.width), float(page.height)])],
                    caption=f"Original page {number}. This page is preserved as an image because its layout could not be reformatted reliably.")
                _crop(block, pdf, canonical.parent, padding=0)
                index = next((i for i, b in enumerate(document.blocks)
                              if b.regions and min(r.page for r in b.regions) > number), len(document.blocks))
                document.blocks.insert(index, block)
                print(f"Retained original page {number}: {source.name}", flush=True)
        document.review = [r for r in document.review
                           if r["reason"] != "No reading content extracted from this page"]
        document.review.extend({"page": number, "reason": "Original page included as an image; check this page at your preferred zoom"}
                               for number in pages)
        document.extraction["original_page_recovery"] = pages
        atomic_json(canonical, document.to_dict())
        selected.append(source)

    def publish(source, folder, report):
        if report["outputs"]["epub"]["text_coverage"]["expected_words"] == 0:
            reason = "Document consists of original tables or figures; use image zoom when reading"
            if not any(r["reason"] == reason for r in report["review"]):
                report["review"].append({"reason": reason})
            report["status"] = "needs_review"
            atomic_json(folder / "conversion-report.json", report)
        publish_paper(args.publish_dir, folder, report)

    repaired = {"documents": {}}
    if selected:
        repaired = run_batch(list(dict.fromkeys(selected)), args.output_dir, args.work_dir, ReadingProfile(), workers=args.workers,
            options={"device": "cuda", "force": True}, epubcheck=args.epubcheck,
            checkpoint=args.output_dir / "repair-progress.json", on_complete=publish)
    for source, item in repaired["documents"].items():
        if item.get("folder"):
            report = json.loads((Path(item["folder"]) / "conversion-report.json").read_text(encoding="utf-8"))
            item.update(status=report["status"], review_items=len(report["review"]), outputs=report["outputs"])
        state["documents"][source] = item
    from scripts.repair_layout import repair_pdf
    import subprocess
    for item in state["documents"].values():
        if not item.get("folder"):
            continue
        folder = Path(item["folder"])
        report = json.loads((folder / "conversion-report.json").read_text(encoding="utf-8"))
        if not report["outputs"]["pdf"]["overfull_boxes"]:
            continue
        report = repair_pdf(folder)
        epub = folder / report["outputs"]["epub"]["filename"]
        check = folder / "epubcheck.json"
        result = subprocess.run(["java", "-jar", str(args.epubcheck.resolve()), str(epub.resolve()), "--json", str(check.resolve())],
            capture_output=True, encoding="utf-8", errors="replace", timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if result.returncode:
            raise ValueError("Repaired EPUB failed EPUBCheck: " + result.stdout[-2000:])
        report["epubcheck"]["valid"] = True
        atomic_json(folder / "conversion-report.json", report)
        item.update(status=report["status"], review_items=len(report["review"]), outputs=report["outputs"])
        publish_paper(args.publish_dir, folder, report)
        print("Repaired layout:", report["title"], flush=True)
    state["finished"] = sum(v["status"] in {"ready", "needs_review"} for v in state["documents"].values())
    state["failed"] = sum(v["status"] == "failed" for v in state["documents"].values())
    atomic_json(progress, state)
    write_catalog(args.publish_dir, inventory, state)
    return int(bool(state["failed"]))


if __name__ == "__main__":
    raise SystemExit(main())
