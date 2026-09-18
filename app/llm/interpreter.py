"""OpenAI-compatible LLM interpreter with retry and safe fallback.

Never logs or echoes the API key. On any failure each note falls back to a safe
``no_op`` so untrusted or unavailable LLM output cannot reach the optimizer.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.llm.prompt import build_messages
from app.schemas import BatterySpec

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 12.0
TOTAL_LLM_BUDGET_SECONDS = 21.0
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


def _content_text(content: Any) -> str:
    """Flatten the common Chat Completions content shapes into plain text."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


def _parse_balanced(text: str, start: int) -> Optional[tuple[Any, int]]:
    """Parse one balanced JSON value starting at ``start``; return (value, end)."""

    opener = text[start]
    closer = "]" if opener == "[" else "}"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : index + 1]), index
                except json.JSONDecodeError:
                    return None
    return None


def _json_values(text: str) -> list[Any]:
    """Parse JSON from provider text, tolerating fences, prose and concatenation."""

    cleaned = _strip_fences(text)
    try:
        return [json.loads(cleaned)]
    except json.JSONDecodeError:
        pass

    values: list[Any] = []
    index = 0
    while index < len(cleaned):
        candidates = [
            position
            for position in (cleaned.find("[", index), cleaned.find("{", index))
            if position != -1
        ]
        if not candidates:
            break
        start = min(candidates)
        parsed = _parse_balanced(cleaned, start)
        if parsed is None:
            index = start + 1
            continue
        value, end = parsed
        values.append(value)
        index = end + 1
    return values


def _extract_list(data: Any) -> Optional[list]:
    if isinstance(data, list):
        return data
    if isinstance(data, str):
        try:
            return _extract_list(json.loads(data))
        except (json.JSONDecodeError, ValueError):
            return None
    if isinstance(data, dict):
        for key in _LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                return value
        for value in data.values():
            if isinstance(value, list):
                return value
        if "directive_type" in data:
            return [data]
        if data and all(str(key).lstrip("-").isdigit() for key in data):
            return [data[key] for key in sorted(data, key=lambda key: int(key))]
    return None


def _parse_directives(content: Any) -> list[dict]:
    text = _content_text(content)
    if not text.strip():
        raise LLMError("provider returned empty or non-text content")

    values = _json_values(text)
    entries: Optional[list] = None
    if len(values) == 1:
        entries = _extract_list(values[0])
    elif values and all(isinstance(value, dict) for value in values):
        entries = values
    if entries is None and values:
        entries = _extract_list(values[0])

    if entries is None:
        raise LLMError("provider did not return a JSON array")
    if not all(isinstance(entry, dict) for entry in entries):
        raise LLMError("JSON array entries must be objects")
    return entries


def _sort_entries(entries: list) -> list:
    """Order directive objects by note_index when every index is a plain int."""

    if entries and all(isinstance(entry, dict) for entry in entries) and all(
        isinstance(entry.get("note_index"), int)
        and not isinstance(entry.get("note_index"), bool)
        for entry in entries
    ):
        return sorted(entries, key=lambda entry: entry["note_index"])
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


def _request_directives(
    messages: list[dict],
    settings: Settings,
    expected_count: Optional[int] = None,
) -> list[dict]:
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

    if os.getenv("LLM_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
        logger.warning(
            "LLM provider=%s model=%s base_url=%s expected=%s",
            settings.llm_provider,
            settings.llm_model,
            settings.llm_base_url,
            expected_count,
        )

    deadline = time.monotonic() + TOTAL_LLM_BUDGET_SECONDS
    best_partial: Optional[list] = None

    for attempt in range(MAX_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning("LLM time budget exhausted before attempt %d", attempt + 1)
            break
        if attempt > 0:
            time.sleep(BACKOFF_BASE_SECONDS * (BACKOFF_FACTOR ** (attempt - 1)))
        body = dict(base_body)
        if attempt == 0:
            body["response_format"] = {"type": "json_object"}
        timeout = min(REQUEST_TIMEOUT_SECONDS, max(0.1, remaining))
        try:
            response = httpx.post(
                url, json=body, headers=headers, timeout=timeout
            )
            if response.status_code == 400 and attempt == 0:
                logger.warning(
                    "LLM rejected JSON response mode; retrying without response_format"
                )
                continue
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if os.getenv("LLM_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
                raw = _content_text(content)
                logger.warning("LLM raw content (len=%d): %s", len(raw), raw[:2000])
            entries = _sort_entries(_parse_directives(content))
            if expected_count is None or len(entries) == expected_count:
                return entries
            best_partial = entries
            raise LLMError(
                f"expected {expected_count} directives but received {len(entries)}"
            )
        except (
            httpx.HTTPError,
            LLMError,
            ValueError,
            KeyError,
            TypeError,
            IndexError,
        ) as exc:
            logger.warning(
                "LLM attempt %d/%d failed (%s)",
                attempt + 1,
                MAX_ATTEMPTS,
                type(exc).__name__,
            )

    if best_partial:
        logger.warning(
            "Using partial interpretation with %d entr(y/ies)", len(best_partial)
        )
        return best_partial
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
        return _request_directives(
            build_messages(notes, battery), settings, expected_count=len(notes)
        )
    except LLMError as exc:
        logger.warning("LLM interpretation failed; using no_op fallback (%s)", exc)
        return _fallback(notes)
