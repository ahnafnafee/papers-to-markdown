"""Atomic, credential-free delivery receipts and a process-wide send lock."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from .browser import KindleError


@contextmanager
def state_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "sender.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise KindleError("Another Kindle command is already using this profile. Finish or close it first.") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Receipts:
    def __init__(self, directory: Path, account: str):
        self.path = directory / "receipts.json"
        self.account = account
        self.data = {"schema_version": 1, "accounts": {}}
        if self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                if self.data["schema_version"] != 1 or not isinstance(self.data["accounts"], dict):
                    raise ValueError("Unsupported receipt format.")
            except (ValueError, KeyError, TypeError):
                raise KindleError("The Kindle receipt file is unreadable. Preserve it and choose another --state-dir before sending.") from None
        self.records = self.data["accounts"].setdefault(account, {})
        if not isinstance(self.records, dict):
            raise KindleError("The Kindle account receipts are unreadable.")

    def get(self, identity: str, destination: str) -> dict | None:
        record = self.records.get(identity + ":" + destination)
        if record is not None and (not isinstance(record, dict) or record.get("status") not in {
                "preparing", "accepted", "submitting", "uncertain", "not-submitted"}):
            raise KindleError("The existing delivery receipt has an unknown status. Preserve it and check Amazon before sending again.")
        return record

    def save(self, identity: str, destination: str, record: dict) -> None:
        self.records[identity + ":" + destination] = record
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(self.data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        try:
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
