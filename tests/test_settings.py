"""
API service settings: where values come from, and that the database password
never shows up in logs or errors. No database needed.
"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from settings import Settings

POSTGRES_VARS = ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in POSTGRES_VARS:
        monkeypatch.delenv(name, raising=False)


def write_env(tmp_path, text, encoding="utf-8"):
    path = tmp_path / ".env"
    path.write_text(text, encoding=encoding)
    return path


def test_values_come_from_env_file_and_llm_variables_are_ignored(tmp_path):
    path = write_env(tmp_path, "LLM_API_KEY=x\nPOSTGRES_PASSWORD=dummy\nPOSTGRES_PORT=5544\n")
    s = Settings(_env_file=path)
    assert s.postgres_port == 5544
    assert s.postgres_password.get_secret_value() == "dummy"


def test_process_environment_wins_over_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", "from-environment")
    path = write_env(tmp_path, "POSTGRES_PASSWORD=dummy\nPOSTGRES_DB=from-file\n")
    assert Settings(_env_file=path).postgres_db == "from-environment"


def test_env_file_saved_with_bom(tmp_path):
    path = write_env(tmp_path, "POSTGRES_PASSWORD=dummy\n", encoding="utf-8-sig")
    assert Settings(_env_file=path).postgres_password.get_secret_value() == "dummy"


@pytest.mark.parametrize("text", ["", "POSTGRES_PASSWORD=\n"])
def test_missing_or_empty_password_is_an_error(tmp_path, text):
    with pytest.raises(ValidationError, match="postgres_password"):
        Settings(_env_file=write_env(tmp_path, text))


def test_password_is_masked_and_escaped(tmp_path):
    path = write_env(tmp_path, "POSTGRES_PASSWORD='p@ss/word'\n")
    s = Settings(_env_file=path)
    assert "p@ss" not in repr(s) and "p@ss" not in str(s.database_url)
    assert "p%40ss%2Fword" in s.database_url.render_as_string(hide_password=False)


def test_env_example_has_no_secret_values():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        name, _, value = line.lstrip("# ").partition("=")
        if name.endswith(("_PASSWORD", "_KEY")):
            assert value == "", f"{name} must be empty in .env.example"
