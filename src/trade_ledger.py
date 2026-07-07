"""Minimal JSONL trade ledger for confirmed fills."""

from __future__ import annotations

import json
from pathlib import Path


class TradeLedger:
    """Appends one JSON object per confirmed fill to a JSONL file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def append(self, record: dict) -> None:
        """Write a single JSON line to the ledger file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
