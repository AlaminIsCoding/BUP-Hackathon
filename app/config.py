"""Environment-backed application settings.

Secrets are read only from the environment and are never logged or echoed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_PORT = 8000


def _get_port() -> int:
    raw = os.getenv("PORT")
    if raw is None or raw.strip() == "":
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


@dataclass(frozen=True)
class Settings:
    """Runtime configuration sourced from environment variables."""

    llm_base_url: str
    llm_model: str
    llm_api_key: str
    port: int


def get_settings() -> Settings:
    """Build settings from the current environment."""

    return Settings(
        llm_base_url=os.getenv("LLM_BASE_URL", DEFAULT_LLM_BASE_URL).rstrip("/"),
        llm_model=os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        port=_get_port(),
    )
