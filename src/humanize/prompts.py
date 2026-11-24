"""Prompt templates: one markdown file per module.

File format (see ``prompts/README.md``)::

    ---
    id: signal.sarcasm
    version: sig.sarcasm.v1
    ---
    ## system
    ...
    ## user
    ... {{PAYLOAD_JSON}} ...

Prompts are loaded by id, never concatenated into one giant system prompt.
``check_versions`` keeps the files and ``config/pipeline.yaml`` in sync, because
a silent drift there would poison the cache (ADR-004).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_PAYLOAD_PLACEHOLDER = "{{PAYLOAD_JSON}}"


class PromptError(RuntimeError):
    """Missing, malformed or out-of-sync prompt file."""


@dataclass(frozen=True)
class RenderedPrompt:
    prompt_id: str
    version: str
    system: str
    user: str

    @property
    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


@dataclass(frozen=True)
class _Template:
    prompt_id: str
    version: str
    system: str
    user: str
    path: Path


class PromptStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise PromptError(f"prompts directory not found: {self.root}")

        templates: dict[str, _Template] = {}
        for path in sorted(self.root.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            if not text.lstrip().startswith("---"):
                continue  # README and other prose files are skipped on purpose
            template = _parse(path, text)
            if template.prompt_id in templates:
                raise PromptError(f"duplicate prompt id {template.prompt_id!r} in {path}")
            templates[template.prompt_id] = template
        if not templates:
            raise PromptError(f"no prompt files found under {self.root}")
        self._templates = templates

    def ids(self) -> list[str]:
        return sorted(self._templates)

    def version(self, prompt_id: str) -> str:
        return self._get(prompt_id).version

    def render(self, prompt_id: str, payload: Mapping[str, Any]) -> RenderedPrompt:
        import json

        template = self._get(prompt_id)
        rendered_user = template.user.replace(
            _PAYLOAD_PLACEHOLDER,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        return RenderedPrompt(
            prompt_id=prompt_id,
            version=template.version,
            system=template.system,
            user=rendered_user,
        )

    def check_versions(self, expected: Mapping[str, str]) -> None:
        problems: list[str] = []
        for prompt_id, expected_version in expected.items():
            template = self._templates.get(prompt_id)
            if template is None:
                problems.append(f"missing prompt file for id={prompt_id!r}")
            elif template.version != expected_version:
                problems.append(
                    f"{prompt_id}: file version {template.version!r} != "
                    f"config {expected_version!r}"
                )
        if problems:
            raise PromptError("prompt/config version drift: " + "; ".join(problems))

    def _get(self, prompt_id: str) -> _Template:
        try:
            return self._templates[prompt_id]
        except KeyError:
            raise PromptError(
                f"unknown prompt id {prompt_id!r}; available: {', '.join(self.ids())}"
            ) from None


def _parse(path: Path, text: str) -> _Template:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PromptError(f"{path}: missing YAML front matter")

    closing = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        raise PromptError(f"{path}: unterminated YAML front matter")

    try:
        meta = yaml.safe_load("\n".join(lines[1:closing])) or {}
    except yaml.YAMLError as exc:
        raise PromptError(f"{path}: invalid front matter: {exc}") from exc

    prompt_id = meta.get("id")
    version = meta.get("version")
    if not isinstance(prompt_id, str) or not prompt_id:
        raise PromptError(f"{path}: front matter must define a string 'id'")
    if not isinstance(version, str) or not version:
        raise PromptError(f"{path}: front matter must define a string 'version'")

    sections = _sections(path, "\n".join(lines[closing + 1 :]))
    return _Template(
        prompt_id=prompt_id,
        version=version,
        system=sections["system"],
        user=sections["user"],
        path=path,
    )


def _sections(path: Path, body: str) -> dict[str, str]:
    buffers: dict[str, list[str]] = {"system": [], "user": []}
    current: str | None = None
    for line in body.splitlines():
        marker = line.strip().lower()
        if marker == "## system":
            current = "system"
            continue
        if marker == "## user":
            current = "user"
            continue
        if current is not None:
            buffers[current].append(line)

    sections = {name: "\n".join(chunk).strip() for name, chunk in buffers.items()}
    for name in ("system", "user"):
        if not sections[name]:
            raise PromptError(f"{path}: section '## {name}' is missing or empty")
    if _PAYLOAD_PLACEHOLDER not in sections["user"]:
        raise PromptError(f"{path}: '## user' must contain {_PAYLOAD_PLACEHOLDER}")
    return sections
