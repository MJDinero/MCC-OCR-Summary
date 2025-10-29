from __future__ import annotations

import types

from src.services.openai_backend import call_llm


def test_chat_json_mode(monkeypatch):
    captured_kwargs = {}

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            captured_kwargs.update(kwargs)
            response = types.SimpleNamespace()
            response.choices = [
                types.SimpleNamespace(message=types.SimpleNamespace(content='{"ok": true}'))
            ]
            return response

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.chat = FakeChat()
            self.responses = types.SimpleNamespace()

    monkeypatch.setattr("src.services.openai_backend.OpenAI", lambda api_key=None: FakeClient(api_key=api_key))

    output = call_llm(prompt="{}", use_responses=False, model="gpt", json_mode=True)

    assert output == "{\"ok\": true}"
    assert captured_kwargs["response_format"] == {"type": "json_object"}
    assert captured_kwargs["model"] == "gpt"
    assert captured_kwargs["messages"][0]["content"] == "{}"
