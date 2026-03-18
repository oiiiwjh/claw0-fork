"""Anthropic-compatible shim backed by the OpenAI Responses API."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from types import ModuleType
from typing import Any


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _coerce_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return vars(obj)


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


class _AnthropicLikeResponse:
    def __init__(self, response: Any, content: list[Any], stop_reason: str) -> None:
        self._response = response
        self.content = content
        self.stop_reason = stop_reason
        self.id = _get_attr(response, "id", "")
        self.model = _get_attr(response, "model", "")
        self.output_text = extract_text(response)
        self.raw_response = response


def create_client(api_key: str | None = None, base_url: str | None = None) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI SDK not installed. Install dependencies from requirements.openai.txt."
        ) from exc
    return OpenAI(api_key=api_key, base_url=base_url)


def _content_blocks_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    chunks: list[str] = []
    for block in content:
        btype = _get_attr(block, "type", "")
        if btype == "text":
            text = _get_attr(block, "text", "")
            if text:
                chunks.append(text)
        elif hasattr(block, "text"):
            text = _get_attr(block, "text", "")
            if text:
                chunks.append(text)
    return "\n".join(chunks)


def _text_item(role: str, text: str) -> dict[str, Any]:
    text_type = "input_text" if role == "user" else "output_text"
    return {
        "type": "message",
        "role": role,
        "content": [{"type": text_type, "text": text}],
    }


def _assistant_tool_item(block: Any) -> dict[str, Any]:
    tool_id = _get_attr(block, "id", "") or f"call_{uuid.uuid4().hex[:12]}"
    tool_input = _get_attr(block, "input", {}) or {}
    if not isinstance(tool_input, dict):
        tool_input = {"value": tool_input}
    return {
        "type": "function_call",
        "call_id": tool_id,
        "name": _get_attr(block, "name", "unknown_tool"),
        "arguments": json.dumps(tool_input, ensure_ascii=False),
    }


def _tool_result_item(block: Any) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": _get_attr(block, "tool_use_id", "") or f"call_{uuid.uuid4().hex[:12]}",
        "output": str(_get_attr(block, "content", "")),
    }


def _message_to_input_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = message.get("role", "user")
    content = message.get("content", "")
    items: list[dict[str, Any]] = []

    if isinstance(content, str):
        return [_text_item(role, content)]

    if not isinstance(content, list):
        return [_text_item(role, str(content))]

    text_chunks: list[str] = []
    for block in content:
        btype = _get_attr(block, "type", "")
        if btype == "text":
            text = _get_attr(block, "text", "")
            if text:
                text_chunks.append(text)
        elif btype == "tool_use":
            if text_chunks:
                items.append(_text_item(role, "\n".join(text_chunks)))
                text_chunks = []
            items.append(_assistant_tool_item(block))
        elif btype == "tool_result":
            if text_chunks:
                items.append(_text_item(role, "\n".join(text_chunks)))
                text_chunks = []
            items.append(_tool_result_item(block))
        elif hasattr(block, "text"):
            text = _get_attr(block, "text", "")
            if text:
                text_chunks.append(text)
        else:
            text_chunks.append(str(block))

    if text_chunks:
        items.append(_text_item(role, "\n".join(text_chunks)))
    return items


def build_followup_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        items.extend(_message_to_input_items(message))
    return items


def _anthropic_tools_to_openai(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None

    converted = []
    for tool in tools:
        converted.append({
            "type": "function",
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        })
    return converted


def _extract_text_parts(item: Any) -> list[str]:
    parts: list[str] = []
    content = _get_attr(item, "content", None)

    if isinstance(content, list):
        for part in content:
            ptype = _get_attr(part, "type", "")
            if ptype in ("output_text", "text", "input_text"):
                text = _get_attr(part, "text", "")
                if text:
                    parts.append(text)

    text = _get_attr(item, "text", "")
    if text:
        parts.append(text)
    return parts


def extract_text(response: Any) -> str:
    output_text = getattr(response, "output_text", "")
    if output_text:
        return output_text

    parts: list[str] = []
    for item in _get_attr(response, "output", []) or []:
        if _get_attr(item, "type", "") == "message":
            parts.extend(_extract_text_parts(item))
    return "".join(parts)


def _parse_tool_arguments(item: Any) -> dict[str, Any]:
    raw_arguments = _get_attr(item, "arguments", "") or "{}"
    if isinstance(raw_arguments, dict):
        return raw_arguments
    try:
        parsed = json.loads(raw_arguments)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    except json.JSONDecodeError:
        return {
            "_raw_arguments": raw_arguments,
            "_parse_error": "Invalid JSON arguments returned by model.",
        }


def extract_tool_calls(response: Any) -> list[_ToolUseBlock]:
    calls: list[_ToolUseBlock] = []
    for item in _get_attr(response, "output", []) or []:
        if _get_attr(item, "type", "") != "function_call":
            continue
        call_id = (
            _get_attr(item, "call_id", "")
            or _get_attr(item, "id", "")
            or f"call_{uuid.uuid4().hex[:12]}"
        )
        calls.append(_ToolUseBlock(
            id=call_id,
            name=_get_attr(item, "name", "unknown_tool"),
            input=_parse_tool_arguments(item),
        ))
    return calls


def response_stop_reason(response: Any) -> str:
    if extract_tool_calls(response):
        return "tool_use"

    status = _get_attr(response, "status", "")
    if status == "incomplete":
        details = _get_attr(response, "incomplete_details", {})
        return str(_get_attr(details, "reason", "incomplete"))
    return "end_turn"


def _build_content(response: Any) -> list[Any]:
    content: list[Any] = []
    text = extract_text(response)
    if text:
        content.append(_TextBlock(text=text))
    content.extend(extract_tool_calls(response))
    return content


def run_response(
    client: Any,
    *,
    model: str,
    system_prompt: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    **kwargs: Any,
) -> _AnthropicLikeResponse:
    request: dict[str, Any] = {
        "model": model,
        "input": build_followup_input(messages),
    }
    if system_prompt:
        request["instructions"] = system_prompt
    if max_tokens is not None:
        request["max_output_tokens"] = max_tokens

    converted_tools = _anthropic_tools_to_openai(tools)
    if converted_tools:
        request["tools"] = converted_tools

    for key, value in kwargs.items():
        if value is None:
            continue
        request[key] = _coerce_dict(value) if hasattr(value, "model_dump") else value

    response = client.responses.create(**request)
    content = _build_content(response)
    stop_reason = response_stop_reason(response)
    return _AnthropicLikeResponse(response=response, content=content, stop_reason=stop_reason)


class _MessagesAPI:
    def __init__(self, owner: "Anthropic") -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> _AnthropicLikeResponse:
        return run_response(
            self._owner._ensure_client(),
            model=kwargs.pop("model"),
            system_prompt=kwargs.pop("system", None),
            messages=kwargs.pop("messages", []),
            tools=kwargs.pop("tools", None),
            max_tokens=kwargs.pop("max_tokens", None),
            **kwargs,
        )


class Anthropic:
    """Drop-in subset of anthropic.Anthropic used by the teaching scripts."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **_: Any) -> None:
        self._api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        self._base_url = base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")
        self._client: Any | None = None
        self.messages = _MessagesAPI(self)

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = create_client(self._api_key, self._base_url)
        return self._client


def install_module_shim() -> ModuleType:
    module = ModuleType("anthropic")
    module.Anthropic = Anthropic
    return module
