"""Versioned intermediate document shared by every reading-copy exporter."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

SCHEMA_VERSION = 1
RENDERER_VERSION = "1"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class Region:
    page: int
    bbox: list[float]
    page_size: list[float]
    printed_label: str = ""


@dataclass
class Block:
    id: str
    kind: str
    text: str = ""
    level: int = 1
    regions: list[Region] = field(default_factory=list)
    caption_regions: list[Region] = field(default_factory=list)
    asset: str | None = None
    asset_width_pt: float = 0
    asset_height_pt: float = 0
    caption: str = ""
    table_html: str = ""
    # Offsets refer to `text`, allowing emphasis and notes to survive reflow.
    spans: list[dict] = field(default_factory=list)
    backlinks: list[dict] = field(default_factory=list)
    asset_role: str = ""
    asset_text_size: float = 0


@dataclass
class Document:
    source_hash: str
    source_name: str
    title: str
    authors: list[str]
    pages: int
    blocks: list[Block]
    removed: list[dict] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    extraction: dict = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Document:
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported reading document version; extract the source again.")
        fields = dict(data)
        fields["blocks"] = [
            Block(**{**b, "regions": [Region(**r) for r in b["regions"]],
                     "caption_regions": [Region(**r) for r in b.get("caption_regions", [])]})
            for b in data["blocks"]
        ]
        result = cls(**fields)
        ids = [b.id for b in result.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate block identifiers in reading document.")
        if not result.blocks:
            raise ValueError("Extraction contains no readable blocks.")
        return result


@dataclass(frozen=True)
class ReadingProfile:
    name: str = "verdana-reading"
    font_family: str = "Verdana"
    font_size: float = 13
    line_spacing: float = 20
    paragraph_spacing: float = 10
    page_width: float = 8.5
    page_height: float = 11
    margin_left: float = 1.05
    margin_right: float = 1.05
    margin_top: float = 0.9
    margin_bottom: float = 0.85

    def __post_init__(self) -> None:
        values = asdict(self)
        import math

        for key, value in values.items():
            if isinstance(value, (int, float)) and (not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid reading-profile value: {key}")
        if self.font_size <= 0 or self.line_spacing < self.font_size:
            raise ValueError("Line spacing must be at least the positive font size.")
        if self.page_width <= self.margin_left + self.margin_right:
            raise ValueError("Page margins leave no reading width.")
        if self.page_height <= self.margin_top + self.margin_bottom:
            raise ValueError("Page margins leave no reading height.")

    @property
    def key(self) -> str:
        return fingerprint(asdict(self))

    @property
    def width_pt(self) -> float:
        return (self.page_width - self.margin_left - self.margin_right) * 72


def source_label(block: Block) -> str:
    pages = sorted({r.page for r in block.regions})
    if not pages:
        return ""
    labels = {r.printed_label for r in block.regions if r.printed_label and r.printed_label != str(r.page)}
    printed = " (printed p. " + ", ".join(sorted(labels)) + ")" if labels else ""
    return "Original p. " + ", ".join(str(x) for x in pages) + printed


def document_assets(document: Document) -> set[str]:
    return {asset for block in document.blocks
            for asset in [block.asset, *(s.get("asset") for s in block.spans)] if asset}
