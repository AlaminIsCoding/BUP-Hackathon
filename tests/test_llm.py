"""Tests for the LLM interpreter: happy path, retries, and fallback."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.config import Settings
from app.llm import interpreter
from app.llm.interpreter import BACKOFF_BASE_SECONDS, FALLBACK_EXPLANATION
from app.schemas import BatterySpec

BATTERY = BatterySpec(
    capacity_kwh=220.0,
    initial_energy_kwh=110.0,
    minimum_energy_kwh=40.0,
    max_charge_kwh_per_hour=50.0,
    max_discharge_kwh_per_hour=50.0,
)

ENTRIES = [
    {
        "note_index": 0,
        "applies": True,
        "directive_type": "solar_reduction",
        "structured_adjustment": {"hours": [12, 13], "factor": 0.2},
        "explanation": "80% reduction leaves 20% usable.",
    }
]


def make_settings(api_key: str = "test-key") -> Settings:
    return Settings(
        llm_base_url="https://llm.example/v1",
        llm_model="test-model",
        llm_api_key=api_key,
        port=8000,
    )


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data=None) -> None:
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
            raise httpx.HTTPStatusError("error", request=request, response=self)


def content_response(content: str, status_code: int = 200) -> FakeResponse:
    return FakeResponse(
        status_code,
        {"choices": [{"message": {"content": content}}]},
    )


def json_response(entries, status_code: int = 200) -> FakeResponse:
    return content_response(json.dumps(entries), status_code)


def test_happy_path_sends_strict_request(mocker) -> None:
    post = mocker.patch(
        "app.llm.interpreter.httpx.post", return_value=json_response(ENTRIES)
    )
    result = interpreter.interpret_notes(["cut solar"], BATTERY, make_settings())
    assert result == ENTRIES

    call = post.call_args
    assert call.kwargs["json"]["temperature"] == 0
    assert call.kwargs["json"]["response_format"] == {"type": "json_object"}
    assert call.kwargs["json"]["model"] == "test-model"
    assert call.kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert call.kwargs["timeout"] == interpreter.REQUEST_TIMEOUT_SECONDS


def test_markdown_fenced_json_is_parsed(mocker) -> None:
    fenced = "```json\n" + json.dumps(ENTRIES) + "\n```"
    mocker.patch("app.llm.interpreter.httpx.post", return_value=content_response(fenced))
    assert interpreter.interpret_notes(["x"], BATTERY, make_settings()) == ENTRIES


def test_dict_wrapped_array_is_parsed(mocker) -> None:
    wrapped = json.dumps({"directives": ENTRIES})
    mocker.patch("app.llm.interpreter.httpx.post", return_value=content_response(wrapped))
    assert interpreter.interpret_notes(["x"], BATTERY, make_settings()) == ENTRIES


def test_malformed_json_then_valid_retries(mocker) -> None:
    sleep = mocker.patch("app.llm.interpreter.time.sleep")
    post = mocker.patch(
        "app.llm.interpreter.httpx.post",
        side_effect=[content_response("not json"), json_response(ENTRIES)],
    )
    result = interpreter.interpret_notes(["x"], BATTERY, make_settings())
    assert result == ENTRIES
    assert post.call_count == 2
    sleep.assert_called_once_with(BACKOFF_BASE_SECONDS)


def test_persistent_malformed_json_falls_back(mocker) -> None:
    mocker.patch("app.llm.interpreter.time.sleep")
    post = mocker.patch(
        "app.llm.interpreter.httpx.post", return_value=content_response("{oops")
    )
    result = interpreter.interpret_notes(["a", "b"], BATTERY, make_settings())
    assert len(result) == 2
    assert all(entry["directive_type"] == "no_op" for entry in result)
    assert all(entry["applies"] is False for entry in result)
    assert all(entry["structured_adjustment"] is None for entry in result)
    assert all(entry["explanation"] == FALLBACK_EXPLANATION for entry in result)
    assert [entry["note_index"] for entry in result] == [0, 1]
    assert post.call_count == 2


def test_http_429_backs_off_then_falls_back(mocker) -> None:
    sleep = mocker.patch("app.llm.interpreter.time.sleep")
    post = mocker.patch(
        "app.llm.interpreter.httpx.post", return_value=FakeResponse(429)
    )
    result = interpreter.interpret_notes(["x"], BATTERY, make_settings())
    assert result[0]["directive_type"] == "no_op"
    assert post.call_count == 2
    sleep.assert_called_once_with(BACKOFF_BASE_SECONDS)


def test_http_400_retries_without_response_format(mocker) -> None:
    mocker.patch("app.llm.interpreter.time.sleep")
    post = mocker.patch(
        "app.llm.interpreter.httpx.post",
        side_effect=[FakeResponse(400), json_response(ENTRIES)],
    )
    result = interpreter.interpret_notes(["x"], BATTERY, make_settings())
    assert result == ENTRIES
    assert post.call_count == 2
    assert "response_format" in post.call_args_list[0].kwargs["json"]
    assert "response_format" not in post.call_args_list[1].kwargs["json"]


def test_missing_configuration_falls_back_without_http(mocker) -> None:
    post = mocker.patch("app.llm.interpreter.httpx.post")
    result = interpreter.interpret_notes(["x"], BATTERY, make_settings(api_key=""))
    assert result[0]["directive_type"] == "no_op"
    post.assert_not_called()


def test_api_key_never_appears_in_logs(mocker, caplog) -> None:
    secret = "SUPER-SECRET-KEY"
    mocker.patch("app.llm.interpreter.time.sleep")
    mocker.patch(
        "app.llm.interpreter.httpx.post", return_value=content_response("not json")
    )
    with caplog.at_level(logging.WARNING):
        interpreter.interpret_notes(["x"], BATTERY, make_settings(api_key=secret))
    assert caplog.records
    assert secret not in caplog.text


def test_empty_notes_returns_empty(mocker) -> None:
    post = mocker.patch("app.llm.interpreter.httpx.post")
    assert interpreter.interpret_notes([], BATTERY, make_settings()) == []
    post.assert_not_called()


def test_prose_wrapped_array_is_extracted(mocker) -> None:
    prose = "Sure! Here is the JSON array:\n" + json.dumps(ENTRIES) + "\nLet me know if you need more."
    mocker.patch("app.llm.interpreter.httpx.post", return_value=content_response(prose))
    assert interpreter.interpret_notes(["x"], BATTERY, make_settings()) == ENTRIES


def test_single_directive_object_is_wrapped(mocker) -> None:
    mocker.patch(
        "app.llm.interpreter.httpx.post", return_value=json_response(ENTRIES[0])
    )
    assert interpreter.interpret_notes(["x"], BATTERY, make_settings()) == ENTRIES


def test_numeric_keyed_object_is_normalized(mocker) -> None:
    payload = {"0": ENTRIES[0], "1": {**ENTRIES[0], "note_index": 1}}
    mocker.patch("app.llm.interpreter.httpx.post", return_value=json_response(payload))
    result = interpreter.interpret_notes(["a", "b"], BATTERY, make_settings())
    assert [entry["note_index"] for entry in result] == [0, 1]


def test_content_parts_list_is_flattened(mocker) -> None:
    parts = [{"type": "text", "text": json.dumps(ENTRIES)}]
    mocker.patch(
        "app.llm.interpreter.httpx.post",
        return_value=FakeResponse(
            200, {"choices": [{"message": {"content": parts}}]}
        ),
    )
    assert interpreter.interpret_notes(["x"], BATTERY, make_settings()) == ENTRIES


def test_debug_mode_does_not_log_raw_content(mocker, caplog, monkeypatch) -> None:
    monkeypatch.setenv("LLM_DEBUG", "1")
    mocker.patch(
        "app.llm.interpreter.httpx.post", return_value=json_response(ENTRIES)
    )
    with caplog.at_level(logging.WARNING):
        interpreter.interpret_notes(["x"], BATTERY, make_settings())
    assert "LLM raw content" not in caplog.text
    assert "80% reduction leaves 20% usable." not in caplog.text
    assert "LLM provider=" in caplog.text
    assert "base_url=" not in caplog.text


def test_wrapper_with_multiple_notes(mocker) -> None:
    entries = [ENTRIES[0], {**ENTRIES[0], "note_index": 1}]
    mocker.patch(
        "app.llm.interpreter.httpx.post",
        return_value=json_response({"directives": entries}),
    )
    result = interpreter.interpret_notes(["a", "b"], BATTERY, make_settings())
    assert [entry["note_index"] for entry in result] == [0, 1]


def test_concatenated_objects_are_collected(mocker) -> None:
    content = json.dumps(ENTRIES[0]) + "\n" + json.dumps(
        {**ENTRIES[0], "note_index": 1}
    )
    mocker.patch("app.llm.interpreter.httpx.post", return_value=content_response(content))
    result = interpreter.interpret_notes(["a", "b"], BATTERY, make_settings())
    assert [entry["note_index"] for entry in result] == [0, 1]


def test_count_mismatch_retries_then_falls_back_for_all_notes(mocker) -> None:
    mocker.patch("app.llm.interpreter.time.sleep")
    post = mocker.patch(
        "app.llm.interpreter.httpx.post", return_value=json_response(ENTRIES)
    )
    result = interpreter.interpret_notes(["a", "b"], BATTERY, make_settings())
    assert post.call_count == 2
    assert len(result) == 2
    assert [entry["note_index"] for entry in result] == [0, 1]
    assert all(entry["directive_type"] == "no_op" for entry in result)
    assert all(entry["applies"] is False for entry in result)
