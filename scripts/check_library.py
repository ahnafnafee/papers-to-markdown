"""Check every published reading file and render contact sheets for inspection."""

import argparse
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reading.library import publish_paper, write_catalog
from reading.model import atomic_json, digest
from reading.pipeline import receipt_matches, renderer_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--progress", type=Path, default=Path("output/pdf/library/batch-progress.json"))
    parser.add_argument("--publish-dir", type=Path, required=True)
    parser.add_argument("--sheets", type=Path)
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    state = json.loads(args.progress.read_text(encoding="utf-8"))
    records, problems, panels = [], [], []
    expected_renderer = renderer_key()
    from PIL import Image, ImageDraw
    if args.sheets:
        import pypdfium2 as pdfium
        args.sheets.mkdir(parents=True, exist_ok=True)

    def flush_sheet():
        if not panels:
            return
        sheet = Image.new("RGB", (1200, 2100), "#dddddd")
        for index, panel in enumerate(panels):
            sheet.paste(panel, ((index % 4) * 300, (index // 4) * 420))
        number = len(list(args.sheets.glob("sheet-*.png"))) + 1
        sheet.save(args.sheets / f"sheet-{number:03}.png")
        panels.clear()

    for source in inventory["documents"]:
        original = Path(source["source"])
        for path in [original, *map(Path, source["aliases"])]:
            if path.parent.resolve() != Path(inventory["root"]).resolve() or digest(path) != source["sha256"]:
                problems.append("Original changed or outside scope: " + str(path))
        item = state["documents"].get(str(original), {})
        if item.get("status") not in {"ready", "needs_review"}:
            if not args.partial:
                problems.append("No complete reading pair: " + original.name)
            continue
        folder = Path(item["folder"])
        report = json.loads((folder / "conversion-report.json").read_text(encoding="utf-8"))
        if report["source_pages"] != source["pages"]:
            problems.append("Source page count differs from the independent inventory: " + original.name)
        if report["request"]["renderer"] != expected_renderer or not receipt_matches(folder, report["request"]):
            problems.append("Stale or damaged output receipt: " + original.name)
            continue
        if not report.get("epubcheck", {}).get("valid"):
            problems.append("Formal EPUBCheck missing: " + original.name)
        publish_paper(args.publish_dir, folder, report)
        for kind, directory in (("pdf", "PDFs"), ("epub", "EPUBs")):
            output = report["outputs"][kind]
            if digest(args.publish_dir / directory / output["filename"]) != output["sha256"]:
                problems.append("Published hash mismatch: " + output["filename"])
        records.append({"source": str(original), "title": report["title"], "status": report["status"],
                        "source_pages": report["source_pages"], "pdf_pages": report["outputs"]["pdf"]["pages"],
                        "review": report["review"], "epubcheck_valid": report["epubcheck"]["valid"]})
        if args.sheets:
            from io import BytesIO
            with zipfile.ZipFile(folder / report["outputs"]["epub"]["filename"]) as archive:
                cover = Image.open(BytesIO(archive.read("OEBPS/cover.png"))).convert("RGB")
            with pdfium.PdfDocument(folder / report["outputs"]["pdf"]["filename"]) as pdf:
                page = pdf[0]
                first = page.render(scale=.65).to_pil().convert("RGB")
                page.close()
            for image, label in ((cover, "Cover"), (first, "PDF page 1")):
                image.thumbnail((288, 390))
                panel = Image.new("RGB", (300, 420), "white")
                panel.paste(image, ((300 - image.width) // 2, 25))
                ImageDraw.Draw(panel).text((6, 5), original.name.split(".")[0] + " | " + label, fill="black")
                panels.append(panel)
                if len(panels) == 20:
                    flush_sheet()
        print(f"Checked {len(records)}: {original.name}", flush=True)
    if args.sheets:
        flush_sheet()
    write_catalog(args.publish_dir, inventory, state)
    result = {"checked": len(records), "source_files": inventory["source_files"],
              "source_pages": sum(r["source_pages"] for r in records),
              "reading_pdf_pages": sum(r["pdf_pages"] for r in records),
              "status_counts": dict(Counter(r["status"] for r in records)),
              "problems": problems, "records": records}
    atomic_json(args.progress.parent / "library-validation.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "records"}, indent=2))
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
