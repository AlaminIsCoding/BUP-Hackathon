"""Environment-backed application settings.

Secrets are read only from the environment and are never logged or echoed.

A provider preset selects sensible defaults for the OpenAI-compatible base URL,
model, and API-key env var. Explicit ``LLM_BASE_URL`` / ``LLM_MODEL`` /
``LLM_API_KEY`` always override the preset, so any OpenAI-compatible endpoint
still works without adding a provider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is in requirements
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

DEFAULT_PROVIDER = "openai"
DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_PORT = 8000


@dataclass(frozen=True)
class ProviderPreset:
    """Defaults for one OpenAI-compatible provider."""

    base_url: Optional[str]
    model: Optional[str]
    key_env: str
    requires_key: bool = True


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        key_env="OPENAI_API_KEY",
    ),
    "openrouter": ProviderPreset(
        base_url="https://openrouter.ai/api/v1",
        model="deepseek/deepseek-v4.1-flash",
        key_env="OPENROUTER_API_KEY",
    ),
    "gemini": ProviderPreset(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash",
        key_env="GEMINI_API_KEY",
    ),
    "ollama": ProviderPreset(
        base_url="http://localhost:11434/v1",
        model="llama3.2",
        key_env="OLLAMA_API_KEY",
        requires_key=False,
    ),
    "custom": ProviderPreset(
        base_url=None,
        model=None,
        key_env="LLM_API_KEY",
    ),
}


@dataclass(frozen=True)
class Settings:
    """Runtime configuration sourced from environment variables."""

    llm_base_url: str
    llm_model: str
    llm_api_key: str
    port: int
    llm_provider: str = "custom"


def _get_port() -> int:
    raw = os.getenv("PORT")
    if raw is None or raw.strip() == "":
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


def get_settings() -> Settings:
    """Build settings from the current environment."""

    provider = (os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if provider not in PROVIDER_PRESETS:
        provider = "custom"
    preset = PROVIDER_PRESETS[provider]

    base_url = os.getenv("LLM_BASE_URL") or preset.base_url or DEFAULT_LLM_BASE_URL
    model = os.getenv("LLM_MODEL") or preset.model or DEFAULT_LLM_MODEL
    api_key = os.getenv("LLM_API_KEY") or os.getenv(preset.key_env, "")
    if not api_key and not preset.requires_key:
        api_key = "ollama"

    return Settings(
        llm_base_url=base_url.rstrip("/"),
        llm_model=model,
        llm_api_key=api_key,
        port=_get_port(),
        llm_provider=provider,
    )
