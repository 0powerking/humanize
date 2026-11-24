"""I/O helpers: JSONL input loading, canonical hashing, atomic run persistence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .schemas import RunInput, RunRecord


class InputError(RuntimeError):
    """Raised when an input JSONL file cannot be parsed into RunInput objects."""


def now_iso() -> str:
    """Local time with UTC offset, second precision -- readable and unambiguous."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_json(obj: Any) -> str:
    """Stable serialization: sorted keys, no whitespace, UTF-8 text preserved."""
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def short_hash(obj: Any, length: int = 12) -> str:
    return stable_hash(obj)[:length]


@lru_cache(maxsize=1)
def git_commit() -> str:
    """Short HEAD hash, or 'unknown' when git is absent / not a repo.

    Never raises: a missing VCS must not block a run.
    """
    if shutil.which("git") is None:
        return "unknown"
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = proc.stdout.strip()
    return commit if proc.returncode == 0 and commit else "unknown"


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise InputError(f"input file not found: {p}")
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise InputError(f"{p}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise InputError(f"{p}:{lineno}: each line must be a JSON object")
        rows.append(row)
    return rows


def load_run_inputs(path: str | Path) -> list[RunInput]:
    p = Path(path)
    inputs: list[RunInput] = []
    for index, row in enumerate(read_jsonl(p), start=1):
        try:
            inputs.append(RunInput.model_validate(row))
        except Exception as exc:
            sample = row.get("sample_id", f"line#{index}")
            raise InputError(
                f"{p}: sample {sample!r} does not match RunInput schema: {exc}"
            ) from exc
    return inputs


def write_json(path: str | Path, payload: Any) -> Path:
    """Atomic write: tmp file in the same directory + os.replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


def append_jsonl(path: str | Path, rows: Iterable[Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            if hasattr(row, "model_dump"):
                row = row.model_dump(mode="json")
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return target


def persist_run_record(record: RunRecord, runs_dir: str | Path) -> Path:
    """One file per run: data/runs/<YYYY-MM-DD>/<sample_id>__<run_id8>.json."""
    day = record.created_at[:10]
    filename = f"{record.sample_id}__{record.run_id[:8]}.json"
    return write_json(Path(runs_dir) / day / filename, record)
