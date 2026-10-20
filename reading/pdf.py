"""XeLaTeX reading template matching the established Verdana layout."""

from __future__ import annotations

import re
import math
import shutil
import subprocess
from pathlib import Path

from .epub import citation_targets, plain_segments
from .fonts import cmap
from .model import Block, Document, ReadingProfile, source_label

_TEX = str.maketrans({
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$",
    "&": r"\&", "#": r"\#", "%": r"\%", "_": r"\_",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
})


def tex_escape(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).translate(_TEX)


def text_tex(block: Block, targets: dict[str, str], body_cmap: set[int],
             symbol_cmap: set[int], cache: Path | None = None, font_size: float = 13) -> str:
    def literal(text: str) -> str:
        result = []
        for index, char in enumerate(text):
            if char.isspace():
                result.append(" ")
            elif ord(char) in body_cmap:
                result.append(tex_escape(char))
                if char in {",", ";"} and index + 1 < len(text) and text[index + 1].isalpha():
                    result.append(r"\allowbreak{}")
            elif ord(char) in symbol_cmap:
                result.append("{\\symbolfont " + tex_escape(char) + "}")
            else:
                raise ValueError(f"Unrenderable glyph U+{ord(char):04X} in {block.id}")
        return "".join(result)

    def characters(text: str) -> str:
        result, previous = [], 0
        for match in re.finditer(r"https?://[A-Za-z0-9:/?&=%#._~+@!$*()\[\],;-]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/[A-Za-z0-9._~+/@!$*()\[\],;=-]+", text):
            result.append(literal(text[previous:match.start()]))
            command = r"\url{" if match.group().startswith(("http:", "https:")) else r"\nolinkurl{"
            result.append(command + match.group() + "}")
            previous = match.end()
        result.append(literal(text[previous:]))
        return "".join(result)

    def linked(text: str) -> str:
        parts, previous = [], 0
        for match in re.finditer(r"\[(\d+(?:\s*[,;\u2013-]\s*\d+)*)\]", text):
            parts.append(characters(text[previous:match.start()]))
            labels = []
            for piece in re.split(r"(\d+)", match.group(1)):
                target = targets.get(piece)
                labels.append(r"\hyperlink{" + target + "}{" + piece + "}"
                              if target and target != block.id else characters(piece))
            parts.append("[" + "".join(labels) + "]")
            previous = match.end()
        parts.append(characters(text[previous:]))
        return "".join(parts)

    result = []
    for text, style in plain_segments(block):
        if style.get("asset"):
            if cache is None:
                raise ValueError("Inline notation needs its source asset directory.")
            path = (cache / style["asset"]).resolve().as_posix()
            part = (r"\raisebox{-" + f'{style["depth_em"] * font_size:.4f}pt'
                + r"}{\includegraphics[height=" + f'{style["height_em"] * font_size:.4f}pt'
                + "]{" + path + "}}")
        else:
            part = linked(text)
            for flag, command in (("italic", "textit"), ("bold", "textbf"),
                                  ("sup", "textsuperscript"), ("sub", "textsubscript")):
                if style.get(flag):
                    part = "\\" + command + "{" + part + "}"
        if style.get("note_target"):
            part = (r"\hypertarget{" + style["note_anchor"] + r"}{}\hyperlink{"
                    + style["note_target"] + "}{" + part + "}")
        result.append(part)
    return "".join(result)


def render_pdf(document: Document, cache: Path, output: Path, profile: ReadingProfile,
               fonts: dict[str, Path]) -> dict:
    executable = shutil.which("xelatex")
    if not executable:
        raise FileNotFoundError("XeLaTeX is required for PDF output. Install TeX Live or MiKTeX.")
    work = output.parent / "tex"
    work.mkdir(parents=True, exist_ok=True)
    body_cmap = cmap(fonts["regular"])
    symbol_cmap = cmap(fonts["symbols"]) if "symbols" in fonts else set()
    targets = citation_targets(document)
    font_path = fonts["regular"].parent.as_posix() + "/"
    header = document.title if len(document.title) <= 65 else document.title[:62] + "..."
    p = profile
    source = [r"\documentclass[12pt]{article}", r"\usepackage{fontspec}",
        r"\setmainfont{" + fonts["regular"].name + "}[Path={" + font_path + "},"
        "BoldFont={" + fonts["bold"].name + "},ItalicFont={" + fonts["italic"].name + "},"
        "BoldItalicFont={" + fonts["bolditalic"].name + "}]",
        r"\usepackage[paperwidth=" + f"{p.page_width}in,paperheight={p.page_height}in,"
        f"left={p.margin_left}in,right={p.margin_right}in,top={p.margin_top}in,"
        f"bottom={p.margin_bottom}in,headheight=16pt,headsep=18pt" + r"]{geometry}",
        r"\usepackage{microtype,graphicx,adjustbox,needspace,fancyhdr,lastpage,xurl}",
        r"\usepackage[unicode,hidelinks]{hyperref}\urlstyle{same}",
        r"\hypersetup{pdftitle={" + tex_escape(document.title) + "},pdfauthor={"
        + tex_escape(", ".join(document.authors)) + "}}",
        r"\setlength{\parindent}{0pt}", r"\setlength{\parskip}{" + f"{p.paragraph_spacing}pt" + "}",
        r"\widowpenalty=10000\clubpenalty=10000",
        r"\pagestyle{fancy}\fancyhf{}",
        r"\fancyhead[L]{\fontsize{9}{11}\selectfont "
        + (text_tex(Block("header", "paragraph", header), {}, body_cmap, symbol_cmap)
           if all(c.isspace() or ord(c) in body_cmap | symbol_cmap for c in header) else "Research paper") + "}",
        r"\fancyhead[R]{\fontsize{9}{11}\selectfont Reading copy}",
        r"\fancyfoot[C]{\fontsize{10}{12}\selectfont Page \thepage\ of \pageref*{LastPage}}",
        r"\renewcommand{\headrulewidth}{0.3pt}",
        r"\setlength{\emergencystretch}{2em}", r"\hyphenpenalty=10000\exhyphenpenalty=10000",
    ]
    if "symbols" in fonts:
        source.append(r"\newfontfamily\symbolfont{" + fonts["symbols"].name + "}[Path={"
                      + fonts["symbols"].parent.as_posix() + "/}]")
    source += [r"\begin{document}", r"\fontsize{" + f"{p.font_size}" + "}{"
               + f"{p.line_spacing}" + r"}\selectfont\raggedright"]
    seen_pages = set()
    for block in document.blocks:
        # Comments expose only stable internal IDs, never extracted TeX commands.
        source.append("% " + block.id)
        image_width = min(p.width_pt, block.asset_width_pt * 2)
        if block.asset_role == "source_text":
            image_width = min(p.width_pt, block.asset_width_pt * p.font_size / (block.asset_text_size or 10))
        elif block.kind == "equation":
            image_width = min(p.width_pt, block.asset_width_pt * p.font_size / 10)
        max_image_height = (p.page_height - p.margin_top - p.margin_bottom) * 72 * .75
        if block.asset and block.kind != "equation":
            caption_height = math.ceil(len(block.caption) * 7 / p.width_pt) * 18
            max_image_height = min(max_image_height,
                (p.page_height - p.margin_top - p.margin_bottom) * 72 - caption_height - 80)
            max_image_height = max(100, max_image_height)
            natural_height = block.asset_height_pt * image_width / max(1, block.asset_width_pt)
            needed = min(max_image_height, natural_height) + caption_height + 40
            source.append(r"\Needspace{" + f"{needed:.3f}bp" + "}")
        pages = {r.page for r in block.regions}
        if pages - seen_pages:
            source.append(r"\par\addvspace{8pt}{\fontsize{9}{12}\selectfont "
                          + tex_escape(source_label(block)) + r"\par}\nobreak")
            seen_pages.update(pages)
        source.append(r"\hypertarget{" + block.id + "}{}")
        if block.asset:
            asset = (cache / block.asset).resolve().as_posix()
            width = image_width
            if block.kind != "equation":
                source.append(r"\par\begin{minipage}{\linewidth}\raggedright")
            alignment = "flushleft" if block.asset_role == "source_text" else "center"
            source.append(r"\par\begin{" + alignment + r"}\begin{adjustbox}{max width=\linewidth,max totalheight="
                          + f"{max_image_height:.3f}bp" + "}")
            source.append(r"\includegraphics[width=" + f"{width:.3f}bp" + "]{" + asset + "}")
            source.append(r"\end{adjustbox}\end{" + alignment + "}")
            if block.caption:
                caption = Block(block.id + "c", "caption", text=block.caption, spans=block.spans)
                source.append(r"{\fontsize{12}{18}\selectfont "
                    + text_tex(caption, targets, body_cmap, symbol_cmap, cache, 12) + r"\par}")
            if block.kind != "equation":
                source.append(r"\end{minipage}\par")
            continue
        text = text_tex(block, targets, body_cmap, symbol_cmap, cache, p.font_size)
        if block.kind in {"title", "heading"}:
            size, leading = (19, 26) if block.kind == "title" else ((15, 22) if block.level == 1 else (14, 21))
            source.append(r"\par\addvspace{12pt}\Needspace{90pt}\pdfbookmark["
                + str(min(3, block.level)) + "]{" + tex_escape(block.text) + "}{toc-" + block.id + "}")
            source.append(r"{\fontsize{" + str(size) + "}{" + str(leading)
                + r"}\selectfont\bfseries " + text + r"\par}\nobreak\vspace{3pt}")
        elif block.kind == "footnote":
            source.append(r"{\fontsize{11}{17}\selectfont " + text + r"\par}")
            if block.backlinks:
                links = " / ".join(r"\hyperlink{" + b["anchor"] + "}{"
                    + (str(i + 1) if len(block.backlinks) > 1 else "Return to text") + "}"
                    for i, b in enumerate(block.backlinks))
                if len(block.backlinks) > 1:
                    links = "Return to text: " + links
                source.append(r"{\fontsize{9}{12}\selectfont " + links + r"\par}")
        else:
            source.append(text + r"\par")
    source.append(r"\end{document}")
    tex_path = work / "reading.tex"
    tex_path.write_text("\n".join(source) + "\n", encoding="utf-8")
    for _ in range(2):
        result = subprocess.run([executable, "-no-shell-escape", "-halt-on-error",
            "-interaction=nonstopmode", "-file-line-error", "reading.tex"], cwd=work,
            capture_output=True, encoding="utf-8", errors="replace", timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0)
        if result.returncode:
            raise RuntimeError(f"PDF typesetting failed (exit {result.returncode}):\n"
                               + result.stdout[-4000:] + "\n" + result.stderr[-2000:])
    log = (work / "reading.log").read_text(encoding="utf-8", errors="replace")
    missing = [line for line in log.splitlines() if "Missing character:" in line]
    if missing:
        raise ValueError("The PDF has missing glyphs:\n" + "\n".join(missing[:10]))
    shutil.copyfile(work / "reading.pdf", output)
    warnings = re.findall(r"Overfull \\[hv]box[^\n]*(?:\n[^\n]*)?", log)
    return {"overfull_boxes": warnings}
