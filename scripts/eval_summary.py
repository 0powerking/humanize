"""Aggregate persisted run records for the evaluation layer.

    python scripts/eval_summary.py --runs data/runs
    python scripts/eval_summary.py --runs data/runs --json

Reads ``rewrite.messages`` for rhythm/length and ``govern`` for residue/sensitive.
Old schema-1.0/1.1 records are skipped rather than silently mis-aggregated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from humanize.evaluate import format_markdown, summarize  # noqa: E402
from humanize.schemas import RunRecord  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", default=str(ROOT / "data/runs"))
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args()

    root = Path(args.runs)
    files = sorted(root.rglob("*.json")) if root.is_dir() else [root]
    records: list[RunRecord] = []
    skipped = 0
    for path in files:
        try:
            records.append(RunRecord.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception:
            skipped += 1

    if not records:
        print(f"error: no valid run records under {root} (skipped {skipped})", file=sys.stderr)
        return 2

    summary = summarize(records)
    summary["skipped_invalid_records"] = skipped
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(format_markdown(summary))
        if skipped:
            print(f"skipped invalid records: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
