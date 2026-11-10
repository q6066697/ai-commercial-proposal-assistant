"""
Loads LLM settings (LLM_API_BASE / LLM_API_KEY / LLM_MODEL / LLM_TIMEOUT)
from a .env file in the project root. Variables already set in the process
environment win over .env, so `LLM_MODEL=... python ...` overrides the file
for one run. .env is git-ignored: it holds the API key.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = ENV_PATH) -> bool:
    """True if the file existed. Never overrides existing variables."""
    return load_dotenv(path, override=False)
