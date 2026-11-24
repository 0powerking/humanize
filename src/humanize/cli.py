"""CLI: run / validate / schema.

All paths are resolved relative to the current working directory, so run these
from the repository root (see README).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .cache import CachedClient, CacheStore
from .config import ConfigError, PipelineConfig, load_config, load_personas
from .cost import BudgetTracker
from .env import load_env
from .io import InputError, load_run_inputs, persist_run_record
from .lexicon import Lexicon, LexiconError, load_lexicon
from .llm import DeepSeekClient, LLMClient, MockClient
from .pipeline import HumanizePipeline
from .prompts import PromptError, PromptStore
from .schemas import (
    GovernOutput,
    RewriteOutput,
    RunInput,
    RunRecord,
    SignalBundle,
)

DEFAULT_CONFIG = "config/pipeline.yaml"
DEFAULT_PERSONAS = "config/persona.yaml"
DEFAULT_LEXICON = "config/ai_smell_patterns.yaml"
DEFAULT_INPUT = "data/eval/seed_examples.jsonl"

_SCHEMA_TARGETS = {
    "input": RunInput,
    "signals": SignalBundle,
    "rewrite": RewriteOutput,
    "govern": GovernOutput,
    "record": RunRecord,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="humanize",
        description="Offline humanization rewrite pipeline (phase 1 skeleton)",
    )
    parser.add_argument("--version", action="version", version=f"humanize {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the pipeline over a JSONL input file")
    run.add_argument("--input", default=DEFAULT_INPUT)
    run.add_argument("--config", default=DEFAULT_CONFIG)
    run.add_argument("--personas", default=DEFAULT_PERSONAS)
    run.add_argument("--lexicon", default=DEFAULT_LEXICON)
    run.add_argument("--out", default=None, help="run record directory (default: config)")
    run.add_argument("--provider", choices=["mock", "deepseek"], default=None)
    run.add_argument("--sample", action="append", default=None, help="only these sample ids")
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--dry-run", action="store_true", help="do not persist run records")
    run.add_argument("--no-cache", action="store_true", help="bypass the disk cache")
    run.add_argument("--quiet", action="store_true", help="no text preview")
    run.set_defaults(func=_cmd_run)

    validate = sub.add_parser("validate", help="validate config, lexicon and inputs")
    validate.add_argument("--input", default=DEFAULT_INPUT)
    validate.add_argument("--config", default=DEFAULT_CONFIG)
    validate.add_argument("--personas", default=DEFAULT_PERSONAS)
    validate.add_argument("--lexicon", default=DEFAULT_LEXICON)
    validate.set_defaults(func=_cmd_validate)

    schema = sub.add_parser("schema", help="print the JSON Schema of one stage contract")
    schema.add_argument("stage", choices=sorted(_SCHEMA_TARGETS))
    schema.set_defaults(func=_cmd_schema)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, InputError, LexiconError, PromptError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except NotImplementedError as exc:
        print(f"not implemented: {exc}", file=sys.stderr)
        return 3


def _cmd_run(args: argparse.Namespace) -> int:
    load_env()
    config = load_config(args.config)
    personas = load_personas(args.personas)
    lexicon = load_lexicon(args.lexicon)
    inputs = load_run_inputs(args.input)

    if args.sample:
        wanted = set(args.sample)
        missing = wanted - {item.sample_id for item in inputs}
        if missing:
            raise InputError(f"sample id(s) not found in {args.input}: {sorted(missing)}")
        inputs = [item for item in inputs if item.sample_id in wanted]
    if args.limit is not None:
        inputs = inputs[: args.limit]
    if not inputs:
        raise InputError(f"no samples to run in {args.input}")

    client = _build_client(args.provider, config, lexicon)
    if config.cache.enabled and not args.no_cache:
        client = CachedClient(
            client,
            CacheStore(config.cache.dir),
            config.prompt_versions,
        )
    pipeline = HumanizePipeline(
        config=config,
        personas=personas,
        lexicon=lexicon,
        client=client,
    )
    runs_dir = Path(args.out or config.paths.runs_dir)
    budget = BudgetTracker(config.cost.budget_yuan)
    warned_printed = False
    run_count = 0

    for item in inputs:
        record = pipeline.run(item)
        run_count += 1
        saved = None if args.dry_run else persist_run_record(record, runs_dir)
        _print_summary(record, saved, show_text=not args.quiet)
        budget.add(record.usage.cost_yuan)

        if budget.exceeded:
            print(
                f"error: estimated cost {budget.spent_yuan:.4f} CNY reached the "
                f"{budget.budget_yuan:.2f} CNY budget; stopped before remaining samples",
                file=sys.stderr,
            )
            break
        if budget.warned and not warned_printed:
            print(
                f"warning: estimated cost {budget.spent_yuan:.4f} CNY has used "
                f"{budget.spent_yuan / budget.budget_yuan:.0%} of the budget",
                file=sys.stderr,
            )
            warned_printed = True

    print(
        f"\n{run_count} run(s), provider={client.name}, dry_run={args.dry_run}, "
        f"estimated_cost={budget.spent_yuan:.4f} CNY / budget {budget.budget_yuan:.2f}"
    )
    return 4 if budget.exceeded else 0


def _cmd_validate(args: argparse.Namespace) -> int:
    load_env()
    config = load_config(args.config)
    personas = load_personas(args.personas)
    lexicon = load_lexicon(args.lexicon)
    prompt_store = PromptStore(config.paths.prompts_dir)
    prompt_store.check_versions(config.prompt_versions)
    inputs = load_run_inputs(args.input)
    print(
        f"ok: {len(inputs)} input(s), {len(lexicon.entries)} lexicon rules, "
        f"{len(personas.personas)} persona(s), {len(prompt_store.ids())} prompt(s), "
        f"provider={config.provider}"
    )
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    model = _SCHEMA_TARGETS[args.stage]
    print(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2))
    return 0


def _build_client(
    provider: str | None, config: PipelineConfig, lexicon: Lexicon
) -> LLMClient:
    chosen = provider or os.environ.get("HUMANIZE_PROVIDER") or config.provider
    if chosen == "mock":
        return MockClient(lexicon=lexicon)
    if chosen == "deepseek":
        api_key = os.environ.get(config.api_key_env, "").strip()
        if not api_key:
            raise ConfigError(
                f"{config.api_key_env} is not set; put it in .env "
                "(gitignored) or export it before running"
            )
        prompt_store = PromptStore(config.paths.prompts_dir)
        prompt_store.check_versions(config.prompt_versions)
        return DeepSeekClient(
            api_key=api_key,
            prompt_store=prompt_store,
            base_url=config.base_url,
            timeout_s=config.api.timeout_s,
            max_retries=config.api.max_retries,
            backoff_base_s=config.api.backoff_base_s,
            json_mode=config.api.json_mode,
        )
    raise ConfigError(f"unknown provider {chosen!r}; expected 'mock' or 'deepseek'")


def _print_summary(record: RunRecord, saved: Path | None, show_text: bool) -> None:
    signals = record.signals
    gate = "noop" if record.rewrite.noop else "rewrite"
    print(
        f"[{record.sample_id}] scene={record.input.context.scene.value} "
        f"ai_smell={signals.ai_smell.score:.2f} gate={gate} "
        f"ratio={record.rewrite.rewrite_ratio:.2f} "
        f"risk={len(record.rewrite.risk_flags)} "
        f"cache={record.usage.cache_hits} "
        f"residue={len(record.govern.residue.hits)} "
        f"sensitive={record.govern.sensitive.action}"
        + (f" degraded={','.join(signals.meta.degraded)}" if signals.meta.degraded else "")
    )
    if show_text:
        print(f"    raw: {_preview(record.input.raw_reply)}")
        print(f"    out: {_preview(record.govern.final_text)}")
        if record.rewrite.change_notes:
            ops = "; ".join(note.op for note in record.rewrite.change_notes[:3])
            print(f"    ops: {_preview(ops, 80)}")
    if saved is not None:
        print(f"    -> {saved}")


def _preview(text: str, limit: int = 64) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


if __name__ == "__main__":
    raise SystemExit(main())
