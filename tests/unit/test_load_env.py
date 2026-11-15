"""load_env: .env values become visible to node_model()/default_llm() without overriding real environment variables."""
from __future__ import annotations

import os

from agent.settings import load_env


def test_load_env_exports_without_overriding(tmp_path, monkeypatch) -> None:
    f = tmp_path / ".env"
    f.write_text("GB_TEST_KEY_A=from_file\nGB_TEST_KEY_B=from_file\n", encoding="utf-8")
    monkeypatch.delenv("GB_TEST_KEY_A", raising=False)
    monkeypatch.setenv("GB_TEST_KEY_B", "from_shell")
    try:
        assert load_env(f) is True
        assert os.environ["GB_TEST_KEY_A"] == "from_file"
        assert os.environ["GB_TEST_KEY_B"] == "from_shell"  # real environment wins
    finally:
        os.environ.pop("GB_TEST_KEY_A", None)


def test_load_env_missing_file_is_harmless(tmp_path) -> None:
    assert load_env(tmp_path / "nope.env") is False
