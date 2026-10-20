"""Convert the explicitly inventoried top-level collection without descending."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reading.batch import run_batch
from reading.library import publish_paper, write_catalog
from reading.model import ReadingProfile, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("output/pdf/library"))
    parser.add_argument("--work-dir", type=Path, default=Path(".cache/reading"))
    parser.add_argument("--publish-dir", type=Path)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--device", default="cuda", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--epubcheck", type=Path)
    parser.add_argument("--limit", type=int, help="Bounded initial sample in source order.")
    parser.add_argument("--prioritize", nargs="*", default=[], help="Process these filename prefixes first.")
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    root = Path(inventory["root"]).resolve(strict=True)
    if inventory.get("recursive") is not False:
        raise ValueError("The library inventory must explicitly exclude subfolders.")
    documents = inventory["documents"]
    documents.sort(key=lambda item: (int(item["name"].split(".")[0]) if item["name"].split(".")[0].isdigit() else 99999,
                                     item["name"].casefold()))
    sources = []
    for document in documents:
        for name in [document["source"], *document["aliases"]]:
            path = Path(name).resolve(strict=True)
            if path.parent != root or path.suffix.lower() != ".pdf":
                raise ValueError("Inventory includes a source outside the top-level PDF folder: " + str(path))
            if digest(path) != document["sha256"]:
                raise ValueError("An inventoried source has changed: " + str(path))
        sources.append(Path(document["source"]))
    if args.limit:
        sources = sources[:args.limit]
    if args.prioritize:
        sources.sort(key=lambda p: not any(p.name.startswith(prefix) for prefix in args.prioritize))
    def completed(source, folder, report):
        if args.publish_dir:
            publish_paper(args.publish_dir, folder, report)
    state = run_batch(sources, args.output_dir, args.work_dir, ReadingProfile(), workers=args.workers,
        options={"device": args.device}, epubcheck=args.epubcheck, on_complete=completed)
    if args.publish_dir:
        write_catalog(args.publish_dir, inventory, state)
    return 1 if state["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
