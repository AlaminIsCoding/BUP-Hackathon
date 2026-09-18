"""Tests for provider-preset resolution in app.config."""

from __future__ import annotations

import pytest

from app.config import get_settings

_ENV_KEYS = (
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "OLLAMA_API_KEY",
    "PORT",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_defaults_to_openai() -> None:
    settings = get_settings()
    assert settings.llm_provider == "openai"
    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.llm_api_key == ""


def test_openrouter_preset_reads_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    settings = get_settings()
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"
    assert settings.llm_model == "deepseek/deepseek-v4.1-flash"
    assert settings.llm_api_key == "or-key"


def test_gemini_preset_base_url_has_no_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    settings = get_settings()
    assert settings.llm_base_url == (
        "https://generativelanguage.googleapis.com/v1beta/openai"
    )
    assert settings.llm_model == "gemini-2.5-flash"
    assert f"{settings.llm_base_url}/chat/completions" == (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )


def test_ollama_needs_no_key_and_gets_dummy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    settings = get_settings()
    assert settings.llm_base_url == "http://localhost:11434/v1"
    assert settings.llm_model == "llama3.2"
    assert settings.llm_api_key == "ollama"


def test_explicit_overrides_beat_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_BASE_URL", "https://gateway.example/v1/")
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "ignored-key")
    settings = get_settings()
    assert settings.llm_base_url == "https://gateway.example/v1"
    assert settings.llm_model == "custom-model"
    assert settings.llm_api_key == "generic-key"


def test_unknown_provider_falls_back_to_custom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "does-not-exist")
    monkeypatch.setenv("LLM_BASE_URL", "https://my-gateway.example/v1")
    monkeypatch.setenv("LLM_MODEL", "my-model")
    settings = get_settings()
    assert settings.llm_provider == "custom"
    assert settings.llm_base_url == "https://my-gateway.example/v1"
    assert settings.llm_model == "my-model"


def test_port_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "9123")
    assert get_settings().port == 9123
    monkeypatch.setenv("PORT", "not-a-number")
    assert get_settings().port == 8000
