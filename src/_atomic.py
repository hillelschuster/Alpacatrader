"""Minimal stdlib atomic JSON file write."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomically(data: Any, path: str | Path, **json_kwargs: Any) -> None:
    """Write *data* as JSON to *path* with an atomic same-directory swap.

    Creates parent directories if needed.  On failure the temp file is
    cleaned up and the target path is left untouched.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".tmp", prefix=p.name + ".", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, **json_kwargs))
            f.flush()
            os.fsync(fd)
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
