"""Validate EPUB containers and retain their own publication metadata."""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

MAX_BYTES = 209_715_200  # The web application's observed 200 MiB limit.


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def xml_member(archive: zipfile.ZipFile, name: str):
    member = archive.getinfo(name)
    if member.file_size > 2_000_000:
        raise ValueError("EPUB metadata is unexpectedly large.")
    return ET.fromstring(archive.read(member))


@dataclass(frozen=True)
class Publication:
    path: Path
    sha256: str
    size: int
    title: str
    authors: tuple[str, ...]

    @property
    def author(self) -> str:
        return ", ".join(self.authors)

    def read_verified(self) -> bytes:
        with self.path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) != self.size or hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError(f"The EPUB changed after selection: {self.path.name}")
        return data


def inspect_epub(path: Path) -> Publication:
    path = path.resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".epub":
        raise ValueError(f"Select an EPUB file: {path.name}")
    size = path.stat().st_size
    if not 0 < size <= MAX_BYTES:
        raise ValueError(f"EPUB must be nonempty and no larger than 200 MiB: {path.name}")
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.getinfo("mimetype").file_size > 256 or archive.read("mimetype") != b"application/epub+zip":
                raise ValueError("Invalid EPUB media type.")
            container = xml_member(archive, "META-INF/container.xml")
            rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
            name = rootfile.get("full-path", "") if rootfile is not None else ""
            parts = PurePosixPath(name)
            if not name or parts.is_absolute() or ".." in parts.parts or "\\" in name:
                raise ValueError("Invalid EPUB package location.")
            package = xml_member(archive, name)
            titles = package.findall(".//{http://purl.org/dc/elements/1.1/}title")
            creators = package.findall(".//{http://purl.org/dc/elements/1.1/}creator")
            title = next((" ".join("".join(t.itertext()).split()) for t in titles if "".join(t.itertext()).strip()), path.stem)
            authors = tuple(" ".join("".join(c.itertext()).split()) for c in creators if "".join(c.itertext()).strip())
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError(f"Invalid EPUB container: {path.name}") from error
    return Publication(path, file_hash(path), size, title, authors)


def select_publications(inputs: list[Path], recursive: bool = False) -> list[Publication]:
    result, seen = [], set()
    for entry in inputs:
        entry = entry.resolve(strict=True)
        candidates = sorted(entry.rglob("*") if recursive else entry.iterdir()) if entry.is_dir() else [entry]
        for path in candidates:
            if entry.is_dir() and (not path.is_file() or path.suffix.lower() != ".epub"):
                continue
            publication = inspect_epub(path)
            if publication.sha256 not in seen:
                seen.add(publication.sha256)
                result.append(publication)
    if not result:
        raise ValueError("No EPUB files selected.")
    return result
