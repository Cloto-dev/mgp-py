"""parse_chat_think_result against the reply shapes OpenAI-compatible backends send.

`"tool_calls": null` on a plain answer, and tool arguments sent as a JSON object instead of
the string the API documents, both raised TypeError and turned a usable answer into an
internal error.
"""

import json

import pytest
from mcp_common.llm_provider import ProviderConfig, parse_chat_think_result

CONFIG = ProviderConfig(provider_id="test", model_id="m", display_name="Test")


def _reply(message, finish_reason="stop"):
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


@pytest.mark.parametrize(
    "tool_calls",
    [None, {}, "none", 5, [None, "x", {"id": "c1", "function": None}]],
    ids=["null", "object", "string", "number", "unusable-entries"],
)
def test_a_plain_answer_with_unusable_tool_calls_is_still_the_answer(tool_calls):
    result = parse_chat_think_result(
        CONFIG, _reply({"content": "the answer", "tool_calls": tool_calls})
    )
    assert result == {"type": "final", "content": "the answer"}


def test_tool_arguments_sent_as_an_object_are_used_as_they_are():
    call = {"id": "c1", "function": {"name": "lookup", "arguments": {"q": "x", "n": 2}}}
    result = parse_chat_think_result(
        CONFIG, _reply({"content": "", "tool_calls": [call]}, "tool_calls")
    )
    assert result["type"] == "tool_calls"
    assert result["calls"] == [{"id": "c1", "name": "lookup", "arguments": {"q": "x", "n": 2}}]


def test_tool_arguments_that_are_neither_string_nor_object_become_empty():
    call = {"id": "c1", "function": {"name": "lookup", "arguments": None}}
    result = parse_chat_think_result(
        CONFIG, _reply({"content": "", "tool_calls": [call]}, "tool_calls")
    )
    assert result["calls"] == [{"id": "c1", "name": "lookup", "arguments": {}}]


def test_string_arguments_are_still_decoded():
    """Control: the documented shape is what the change must not move."""
    call = {"id": "c1", "function": {"name": "lookup", "arguments": json.dumps({"q": "x"})}}
    result = parse_chat_think_result(
        CONFIG, _reply({"content": "", "tool_calls": [call]}, "tool_calls")
    )
    assert result["calls"] == [{"id": "c1", "name": "lookup", "arguments": {"q": "x"}}]
