"""OpenAI-compatible LLM interpreter with retry and safe fallback.

Never logs or echoes the API key. On any failure each note falls back to a safe
``no_op`` so untrusted or unavailable LLM output cannot reach the optimizer.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.llm.prompt import build_messages
from app.schemas import BatterySpec

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20.0
MAX_ATTEMPTS = 2
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_FACTOR = 2

FALLBACK_EXPLANATION = "Directive could not be interpreted and was treated as no_op."

_FENCE_PATTERN = re.compile(r"^```[a-zA-Z0-9]*\s*|\s*```$")
_LIST_KEYS = ("directive_interpretation", "directives", "interpretations")


class LLMError(RuntimeError):
    """Raised internally when the provider cannot return usable directives."""


def _strip_fences(text: str) -> str:
    return _FENCE_PATTERN.sub("", text.strip()).strip()


def _extract_list(data: Any) -> Optional[list]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in _LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                return value
        for value in data.values():
            if isinstance(value, list):
                return value
    return None


def _parse_directives(content: Any) -> list[dict]:
    if not isinstance(content, str) or not content.strip():
        raise LLMError("provider returned empty or non-text content")
    parsed = json.loads(_strip_fences(content))
    entries = _extract_list(parsed)
    if entries is None:
        raise LLMError("provider did not return a JSON array")
    if not all(isinstance(entry, dict) for entry in entries):
        raise LLMError("JSON array entries must be objects")
    return entries


def _fallback(notes: list[str]) -> list[dict]:
    return [
        {
            "note_index": index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": FALLBACK_EXPLANATION,
        }
        for index, _note in enumerate(notes)
    ]


def _request_directives(messages: list[dict], settings: Settings) -> list[dict]:
    url = f"{settings.llm_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    base_body: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0,
    }

    for attempt in range(MAX_ATTEMPTS):
        if attempt > 0:
            time.sleep(BACKOFF_BASE_SECONDS * (BACKOFF_FACTOR ** (attempt - 1)))
        body = dict(base_body)
        if attempt == 0:
            body["response_format"] = {"type": "json_object"}
        try:
            response = httpx.post(
                url, json=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
            )
            if response.status_code == 400 and attempt == 0:
                logger.warning(
                    "LLM rejected JSON response mode; retrying without response_format"
                )
                continue
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            return _parse_directives(content)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
            logger.warning(
                "LLM attempt %d/%d failed (%s)",
                attempt + 1,
                MAX_ATTEMPTS,
                type(exc).__name__,
            )

    raise LLMError("provider did not return usable directives")


def interpret_notes(
    notes: list[str],
    battery: BatterySpec,
    settings: Optional[Settings] = None,
) -> list[dict]:
    """Interpret notes into raw directive dicts, falling back per note to no_op."""

    if not notes:
        return []
    settings = settings or get_settings()
    if not settings.llm_base_url or not settings.llm_api_key:
        logger.warning("LLM not configured; using no_op fallback for all notes")
        return _fallback(notes)

    try:
        return _request_directives(build_messages(notes, battery), settings)
    except LLMError as exc:
        logger.warning("LLM interpretation failed; using no_op fallback (%s)", exc)
        return _fallback(notes)
