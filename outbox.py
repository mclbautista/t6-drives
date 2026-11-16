"""Writes the API could not take yet. One JSON file in the data volume, replaced atomically on every change."""

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class Outbox:
    """Entries are queued (waiting to be retried) or failed (the API refused them; they stay until dismissed).

    An entry is named by its idempotency key, so the same form submitted twice is one entry.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.lock = threading.RLock()

    def entries(self) -> list[dict[str, Any]]:
        try:
            return list(json.loads(self.path.read_text())["entries"])
        except FileNotFoundError:
            return []

    def _save(self, entries: list[dict[str, Any]]) -> None:
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".outbox-")
        try:
            with os.fdopen(descriptor, "w") as file:
                json.dump({"entries": entries}, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            os.unlink(temporary)
            raise

    def add(self, verb: str, inputs: dict[str, Any], key: str) -> None:
        with self.lock:
            entries = self.entries()
            if all(entry["key"] != key for entry in entries):
                queued_at = datetime.now(UTC).isoformat(timespec="seconds")
                entries.append(
                    {
                        "key": key,
                        "verb": verb,
                        "inputs": inputs,
                        "state": "queued",
                        "reason": None,
                        "queued_at": queued_at,
                    }
                )
                self._save(entries)

    def fail(self, key: str, reason: str) -> None:
        with self.lock:
            self._save(
                [
                    {**e, "state": "failed", "reason": reason} if e["key"] == key else e
                    for e in self.entries()
                ]
            )

    def remove(self, key: str) -> None:
        with self.lock:
            self._save([e for e in self.entries() if e["key"] != key])

    def dismiss(self, key: str) -> bool | None:
        """True when a failed entry was dismissed; False when it is still queued; None when there is no such entry."""
        with self.lock:
            found = next((e for e in self.entries() if e["key"] == key), None)
            if found is None:
                return None
            if found["state"] != "failed":
                return False
            self.remove(key)
            return True
