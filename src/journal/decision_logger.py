"""
Decision logger — writes one JSON object per line to a JSONL file.

Each line is a serialized ``DecisionRecord``, making the log queryable
with tools like ``jq``, ``grep``, or simple Python iteration.

Usage::

    logger = DecisionLogger("data/decisions.jsonl")
    record = DecisionRecord(symbol="DSY", decision="watch", ...)
    logger.write(record)

    for rec in logger.read():
        print(rec.symbol, rec.decision)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Generator

from src.models.schemas import DecisionRecord


class DecisionLogger:
    """JSONL writer/reader for ``DecisionRecord`` objects.

    Writes one ``DecisionRecord`` per line.  Lines are append-only.
    Reading re-opens the file each time so the caller always sees the
    latest data.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def write(self, record: DecisionRecord) -> None:
        """Append one ``DecisionRecord`` to the log file.

        Rotates the file when it exceeds ~10 MB, keeping up to 5 backups.
        """
        # Rotate if needed
        if self._path.exists() and self._path.stat().st_size > 10_000_000:  # 10 MB
            for i in range(4, 0, -1):
                old = self._path.with_suffix(f".jsonl.{i}")
                new = self._path.with_suffix(f".jsonl.{i + 1}")
                if old.exists():
                    if new.exists():
                        new.unlink()
                    old.rename(new)
            backup = self._path.with_suffix(".jsonl.1")
            if backup.exists():
                backup.unlink()
            self._path.rename(backup)

        line = record.to_json_line() + "\n"
        with open(self._path, "a") as f:
            f.write(line)

    def read(self) -> Generator[DecisionRecord, None, None]:
        """Yield every ``DecisionRecord`` in the file (latest open)."""
        if not self._path.exists():
            return
        with open(self._path) as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                yield DecisionRecord.from_json_line(stripped)

    def filter(self, *, symbol: str | None = None,
               decision: str | None = None) -> list[DecisionRecord]:
        """Return records matching the given filters."""
        results: list[DecisionRecord] = []
        for rec in self.read():
            if symbol is not None and rec.symbol != symbol:
                continue
            if decision is not None and rec.decision != decision:
                continue
            results.append(rec)
        return results
