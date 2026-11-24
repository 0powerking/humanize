from __future__ import annotations

from pathlib import Path

import pytest

from humanize.config import PersonaBook, PipelineConfig, load_config, load_personas
from humanize.io import load_run_inputs
from humanize.lexicon import Lexicon, load_lexicon
from humanize.llm import MockClient
from humanize.pipeline import HumanizePipeline
from humanize.schemas import RunInput

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def config() -> PipelineConfig:
    return load_config(ROOT / "config/pipeline.yaml")


@pytest.fixture(scope="session")
def personas() -> PersonaBook:
    return load_personas(ROOT / "config/persona.yaml")


@pytest.fixture(scope="session")
def lexicon() -> Lexicon:
    return load_lexicon(ROOT / "config/ai_smell_patterns.yaml")


@pytest.fixture(scope="session")
def seed_inputs() -> list[RunInput]:
    return load_run_inputs(ROOT / "data/eval/seed_examples.jsonl")


@pytest.fixture()
def client(lexicon: Lexicon) -> MockClient:
    return MockClient(lexicon=lexicon)


@pytest.fixture()
def pipeline(
    config: PipelineConfig,
    personas: PersonaBook,
    lexicon: Lexicon,
    client: MockClient,
) -> HumanizePipeline:
    return HumanizePipeline(
        config=config,
        personas=personas,
        lexicon=lexicon,
        client=client,
    )


def sample_by_id(seed_inputs: list[RunInput], sample_id: str) -> RunInput:
    return next(item for item in seed_inputs if item.sample_id == sample_id)


@pytest.fixture()
def sample(seed_inputs: list[RunInput]):
    def _get(sample_id: str) -> RunInput:
        return sample_by_id(seed_inputs, sample_id)

    return _get
