"""Locate installed fonts; never keep font binaries in the source tree."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def font_files(directory: Path | None = None) -> dict[str, Path]:
    from fontTools.ttLib import TTFont

    root = directory or Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    result = {}
    for style, filename in {
        "regular": "verdana.ttf", "bold": "verdanab.ttf",
        "italic": "verdanai.ttf", "bolditalic": "verdanaz.ttf",
    }.items():
        path = root / filename
        if not path.is_file():
            raise FileNotFoundError(f"Verdana font not found: {path}. Use --font-dir.")
        with TTFont(path) as font:
            flags = font["OS/2"].fsType
            if flags & (2 | 512):
                raise ValueError(f"Font does not allow outline document embedding: {path.name}")
        result[style] = path.resolve()
    kpsewhich = shutil.which("kpsewhich")
    if kpsewhich:
        response = subprocess.run(
            [kpsewhich, "latinmodern-math.otf"], capture_output=True, text=True, check=False,
        )
        path = Path(response.stdout.strip())
        if response.returncode == 0 and path.is_file():
            result["symbols"] = path.resolve()
    return result


def cmap(path: Path) -> set[int]:
    from fontTools.ttLib import TTFont

    with TTFont(path) as font:
        return set(font.getBestCmap() or {})
