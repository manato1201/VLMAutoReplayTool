from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vlm_auto_replay.prompts.model_client import (  # noqa: E402
    ScriptedFoundationModelClient,
    configure_model_client,
    reset_model_client,
)


@pytest.fixture
def scripted_client() -> ScriptedFoundationModelClient:
    client = ScriptedFoundationModelClient()
    configure_model_client(client)
    yield client
    reset_model_client()
