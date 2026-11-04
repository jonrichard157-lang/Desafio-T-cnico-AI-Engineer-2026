"""Fake Anthropic client for tests (scripted responses, no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def tool_use(id_: str, name: str, input_: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def response(*blocks: SimpleNamespace, stop: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


class FakeClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))  # snapshot of the request at call time
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
