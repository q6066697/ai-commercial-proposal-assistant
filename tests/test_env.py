"""
.env loading: values from the file fill in missing variables, but a
variable already set in the environment always wins.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from env import load_env


def test_env_file_fills_missing_but_never_overrides(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODEL=from-file\nLLM_TIMEOUT=180\n", encoding="utf-8")
    monkeypatch.setenv("LLM_MODEL", "from-environment")
    monkeypatch.delenv("LLM_TIMEOUT", raising=False)

    assert load_env(env_file) is True

    import os
    assert os.environ["LLM_MODEL"] == "from-environment"
    assert os.environ["LLM_TIMEOUT"] == "180"
    monkeypatch.delenv("LLM_TIMEOUT")


def test_missing_env_file_is_not_an_error(tmp_path):
    assert load_env(tmp_path / "absent.env") is False


def test_env_file_is_git_ignored():
    gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore.splitlines()
