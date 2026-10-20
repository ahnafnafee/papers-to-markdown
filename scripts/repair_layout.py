"""Finish recorded PDF layout repairs without changing source wording."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from reading.model import Document, atomic_json, digest
from reading.validate import validate_epub, validate_pdf


def soft_wrap(line: str) -> str:
    saved = []

    def protect(match):
        saved.append(match[0])
        return "\x01" + str(len(saved) - 1) + "\x02"

    line = re.sub(r"\\includegraphics(?:\[[^\]]*\])?\{[^}]*\}|\\(?:url|nolinkurl)\{[^}]*\}"
                  r"|\{[-+]?\d+(?:\.\d+)?(?:pt|bp|em|in|cm|mm)?\}", protect, line)
    line = re.sub(r"[A-Za-z0-9*]{35,}",
        lambda m: r"\allowbreak{}".join(m[0][i:i + 10] for i in range(0, len(m[0]), 10)), line)
    line = re.sub(r"(?:\\_){8,}",
        lambda m: r"\allowbreak{}".join(m[0][i:i + 16] for i in range(0, len(m[0]), 16)), line)
    line = re.sub(r"(?<!\\)([/=?:;.,()<>-])", lambda m: m[0] + r"\allowbreak{}", line)
    line = line.replace(r"\&", r"\&\allowbreak{}")
    for i, value in enumerate(saved):
        line = line.replace("\x01" + str(i) + "\x02", value)
    return line


def repair_pdf(folder: Path) -> dict:
    from reading.fonts import font_files
    from reading.pipeline import preserve_source_glyphs

    receipt = folder / "conversion-report.json"
    report = json.loads(receipt.read_text(encoding="utf-8"))
    warnings = report["outputs"]["pdf"]["overfull_boxes"]
    if not warnings:
        return report
    tex = folder / "tex" / "reading.tex"
    lines = tex.read_text(encoding="utf-8").splitlines()
    changed = set()
    for warning in warnings:
        match = re.search(r"at lines (\d+)--(\d+)", warning)
        if match:
            for number in range(int(match[1]), int(match[2]) + 1):
                lines[number - 1] = soft_wrap(lines[number - 1])
                changed.add(number)
    vertical = any("vbox" in warning for warning in warnings)
    if vertical:
        lines = [line for line in lines if line not in {
            r"\par\begin{minipage}{\linewidth}\raggedright", r"\end{minipage}\par"}]
    backup = tex.with_name("reading.before-layout-repair.tex")
    if not backup.exists():
        shutil.copyfile(tex, backup)
    tex.write_text("\n".join(lines) + "\n", encoding="utf-8")
    executable = shutil.which("xelatex")
    for _ in range(2):
        result = subprocess.run([executable, "-no-shell-escape", "-halt-on-error", "-interaction=nonstopmode", "reading.tex"],
            cwd=tex.parent, capture_output=True, encoding="utf-8", errors="replace", timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW if __import__('os').name == 'nt' else 0)
        if result.returncode:
            raise RuntimeError("Layout repair failed: " + result.stdout[-3000:] + result.stderr[-1000:])
    log = (tex.parent / "reading.log").read_text(encoding="utf-8", errors="replace")
    overflow = re.findall(r"Overfull \\[hv]box[^\n]*(?:\n[^\n]*)?", log)
    if "Missing character:" in log or overflow:
        raise ValueError("Layout repair still needs attention: " + repr(overflow))
    document = Document.from_dict(json.loads((Path(report["extraction_cache"]) / "document.json").read_text(encoding="utf-8")))
    preserve_source_glyphs(document, Path(report["source"]), Path(report["extraction_cache"]), font_files())
    output = folder / report["outputs"]["pdf"]["filename"]
    shutil.copyfile(tex.parent / "reading.pdf", output)
    report["outputs"]["pdf"].update(validate_pdf(output, document), overfull_boxes=[])
    report["review"] = [r for r in report["review"] if r["reason"] != "Typesetting overflow"]
    report["manual_layout"] = {"tex_lines_with_optional_breaks": sorted(changed),
                               "long_captions_may_continue_on_next_page": vertical,
                               "source_wording_unchanged": True}
    # Long identifiers also need wrapping on narrow reflowable reading screens.
    epub = folder / report["outputs"]["epub"]["filename"]
    temporary = epub.with_suffix(".epub.partial")
    with zipfile.ZipFile(epub) as original, zipfile.ZipFile(temporary, "w") as repaired:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "OEBPS/reading.css":
                data += b"\np, h1, h2, h3, h4 { overflow-wrap: anywhere; word-wrap: break-word; }\n"
            repaired.writestr(info, data)
    temporary.replace(epub)
    report["outputs"]["epub"].update(validate_epub(epub, document))
    report["status"] = "needs_review" if report["review"] else "ready"
    atomic_json(receipt, report)
    return report
