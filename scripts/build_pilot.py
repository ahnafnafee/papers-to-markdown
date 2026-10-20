"""Build the three-paper pilot from the user's existing PDF folders."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--paper-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("output/pdf/pilot"))
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    papers = [
        (args.reference_dir / "1. AVR - Augmented Vehicular Reality.pdf", {
            "name": "AVR", "title": "AVR: Augmented Vehicular Reality",
            "authors": ["Hang Qiu", "Fawad Ahmad", "Fan Bai", "Marco Gruteser", "Ramesh Govindan"]}),
        (args.reference_dir / "2. DeepMix - Hybrid 3D Object Detection for Headsets.pdf", {
            "name": "DeepMix", "title": "DeepMix: Mobility-aware, Lightweight, and Hybrid 3D Object Detection for Headsets",
            "authors": ["Yongjie Guan", "Xueyu Hou", "Nan Wu", "Bo Han", "Tao Han"]}),
        (args.paper_store / "104. Unifying distillation and privileged information.pdf", {
            "name": "Unifying-Distillation", "title": "Unifying Distillation and Privileged Information",
            "authors": ["David Lopez-Paz", "Léon Bottou", "Bernhard Schölkopf", "Vladimir Vapnik"]}),
    ]
    overrides, sources = {}, []
    for source, metadata in papers:
        source = source.resolve(strict=True)
        with source.open("rb") as stream:
            key = hashlib.file_digest(stream, "sha256").hexdigest()
        overrides[key] = metadata
        sources.append(source)
    root = Path(__file__).resolve().parents[1]
    metadata_path = root / ".cache" / "reading" / "pilot-metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(overrides, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    uv = shutil.which("uv")
    if not uv:
        parser.error("Install uv to create the isolated reading environment.")
    command = [uv, "run", "--script", str(root / "read.py"), *map(str, sources),
        "--metadata", str(metadata_path), "--output-dir", str(args.output_dir.resolve()), "--device", args.device]
    if args.force:
        command.append("--force")
    return subprocess.run(command, cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
