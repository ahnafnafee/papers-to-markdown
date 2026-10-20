"""Publish compact reading files and an accessible, searchable local catalogue."""

from __future__ import annotations

import os
import json
import re
import shutil
from html import escape
from pathlib import Path
from urllib.parse import quote

from .model import atomic_json, digest


def publish_paper(destination: Path, folder: Path, report: dict):
    for kind, directory in (("pdf", "PDFs"), ("epub", "EPUBs")):
        if kind not in report["outputs"]:
            continue
        item = report["outputs"][kind]
        source = folder / item["filename"]
        target = destination / directory / item["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and digest(target) == item["sha256"]:
            continue
        temporary = target.with_suffix(target.suffix + ".partial")
        shutil.copyfile(source, temporary)
        if digest(temporary) != item["sha256"]:
            raise RuntimeError("Reading-copy verification failed: " + str(target))
        os.replace(temporary, target)
    if report.get("review"):
        target = destination / "Review notes" / (folder.name + ".html")
        target.parent.mkdir(parents=True, exist_ok=True)
        notes = []
        for entry in report["review"]:
            label = (f'Original page {entry["page"]}: ' if entry.get("page") else "") + entry["reason"]
            detail = entry.get("sample") or ""
            notes.append("<li><p>" + escape(label) + "</p>" + ("<p>" + escape(detail) + "</p>" if detail else "") + "</li>")
        text = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Review notes</title>'
        text += '<style>body{font:17px/1.65 Verdana,sans-serif;max-width:850px;margin:3em auto;padding:0 1.5em}'
        text += 'li{margin:1em 0}h1{font-size:1.5em;line-height:1.4}a{color:#164c8b}</style>'
        text += '<p><a href="../START%20HERE.html">Library</a></p><h1>' + escape(report["title"]) + '</h1>'
        text += '<p>These automatic checks identify material worth comparing with the original. '
        text += 'A source crop keeps the original appearance where character encoding prevents reliable reformatting.</p>'
        text += '<p><a href="' + escape(Path(report["source"]).as_uri(), quote=True) + '">Open the original PDF</a></p><ul>'
        text += ''.join(notes) + '</ul></html>'
        target.write_text(text, encoding="utf-8")


def write_catalog(destination: Path, inventory: dict, state: dict):
    destination.mkdir(parents=True, exist_ok=True)
    entries, rows = [], []
    def paper_order(item):
        number = re.match(r"^(\d+)[.\s]", item["name"])
        return (int(number[1]) if number else 10**9, item["name"].casefold())

    for source in sorted(inventory["documents"], key=paper_order):
        item = state["documents"].get(source["source"], {"status": "pending"})
        title = item.get("title") or source.get("metadata_title") or Path(source["name"]).stem
        links = []
        exported = {}
        for kind, directory in (("pdf", "PDFs"), ("epub", "EPUBs")):
            output = item.get("outputs", {}).get(kind)
            if output:
                relative = directory + "/" + output["filename"]
                if (destination / relative).is_file():
                    links.append(f'<a href="{quote(relative)}">{kind.upper()}</a>')
                    exported[kind] = {**output, "relative_path": relative}
        status = {"ready": "Converted", "needs_review": "Converted; check flagged pages",
                  "failed": "Conversion failed", "pending": "Waiting", "extracting": "Reading source",
                  "formatting": "Formatting"}.get(item["status"], item["status"])
        review = []
        if item.get("folder"):
            report_path = Path(item["folder"]) / "conversion-report.json"
            if report_path.is_file():
                review = json.loads(report_path.read_text(encoding="utf-8"))["review"]
            review_path = "Review notes/" + Path(item["folder"]).name + ".html"
            if review and (destination / review_path).is_file():
                status = '<a href="' + quote(review_path) + '">Check flagged pages</a>'
            else:
                status = escape(status)
        else:
            status = escape(status)
        duplicates = ('; identical source: ' + ', '.join(Path(name).name for name in source["aliases"])) if source["aliases"] else ""
        rows.append('<tr><td>' + escape(title) + '<small>' + escape(source["name"] + duplicates)
            + '</small></td><td class="formats">' + " · ".join(links) + '</td><td>' + status
            + '</td></tr>')
        entries.append({**source, **item, "title": title, "reading_files": exported, "review": review})
    manifest = {"source_root": inventory["root"], "recursive": False,
                "source_files": inventory["source_files"], "unique_documents": inventory["unique_documents"],
                "finished": state["finished"], "failed": state["failed"], "documents": entries}
    atomic_json(destination / "conversion-details.json", manifest)
    text = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
    text += '<title>Paper reading library</title><style>body{font:17px/1.65 Verdana,sans-serif;color:#202020;'
    text += 'background:white;max-width:1150px;margin:3em auto;padding:0 1.5em}h1{font-size:1.7em;line-height:1.3}'
    text += 'input{font:inherit;padding:.5em;width:95%;margin:1em 0}table{width:100%;border-collapse:collapse}'
    text += 'th,td{text-align:left;vertical-align:top;padding:1em .6em;border-bottom:1px solid #bbb}'
    text += 'small{display:block;font-size:.72em;margin-top:.5em}.formats{white-space:nowrap}a{color:#164c8b}</style>'
    text += '<h1>Paper reading library</h1><p>Verdana, generous line spacing, and single-column reading copies.</p>'
    text += f'<p>{state["finished"]} of {inventory["unique_documents"]} unique papers converted. '
    text += 'Only PDFs directly in the source folder are included.</p>'
    text += '<p>Open a PDF here, or use an EPUB with Send to Kindle. EPUBs include a cover and linked contents. '
    text += '“Check flagged pages” means automatic checks found material to compare with the original. '
    text += 'Conversion details are saved alongside this catalogue.</p>'
    text += '<label for="search">Find a paper</label><input id="search" type="search" placeholder="Title or filename">'
    text += '<table><thead><tr><th>Paper</th><th>Read</th><th>Status</th></tr></thead><tbody>' + ''.join(rows)
    text += '</tbody></table><script>document.getElementById("search").addEventListener("input",function(){'
    text += 'let q=this.value.toLowerCase();document.querySelectorAll("tbody tr").forEach(function(r){'
    text += 'r.hidden=!r.textContent.toLowerCase().includes(q)})})</script></html>'
    target = destination / "START HERE.html"
    temporary = target.with_suffix(".partial")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)
    return manifest
