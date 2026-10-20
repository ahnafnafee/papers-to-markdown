"""Invoke the isolated sender without adding browser dependencies to conversion."""

import shutil
import subprocess
from pathlib import Path


def send_epubs(paths: list[Path], *, state_dir: Path | None = None, dry_run: bool = False) -> int:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("Kindle delivery needs uv to run its isolated browser environment.")
    script = Path(__file__).resolve().parents[1] / "kindle.py"
    command = [uv, "run", "--locked", "--script", str(script), "send", *[str(p.resolve(strict=True)) for p in paths]]
    if state_dir:
        command.extend(["--state-dir", str(state_dir)])
    if dry_run:
        command.append("--dry-run")
    return subprocess.run(command, check=False).returncode
