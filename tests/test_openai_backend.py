from __future__ import annotations

import types
from typing import Any

from src.services.metrics import NullMetrics, PrometheusMetrics
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


def test_responses_path_uses_output_text(monkeypatch):
    captured_kwargs = {}

    class FakeResponses:
        @staticmethod
        def create(**kwargs):
            captured_kwargs.update(kwargs)
            return types.SimpleNamespace(output_text="chunk-output")

    class FakeClient:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.chat = types.SimpleNamespace(completions=None)
            self.responses = FakeResponses()

    monkeypatch.setattr("src.services.openai_backend.OpenAI", lambda api_key=None: FakeClient(api_key=api_key))

    output = call_llm(
        prompt="payload",
        use_responses=True,
        model="gpt-resp",
        json_mode=False,
        api_key="abc",
    )

    assert output == "chunk-output"
    assert captured_kwargs == {"model": "gpt-resp", "input": "payload"}


def test_responses_path_fallback_to_content(monkeypatch):
    tree_node = types.SimpleNamespace(
        content=[types.SimpleNamespace(text=types.SimpleNamespace(value="tree-output"))]
    )

    class FakeResponses:
        @staticmethod
        def create(**_kwargs):
            return types.SimpleNamespace(output_text=None, output=[tree_node])

    class FakeClient:
        def __init__(self):
            self.responses = FakeResponses()
            self.chat = types.SimpleNamespace()

    monkeypatch.setattr("src.services.openai_backend.OpenAI", lambda api_key=None: FakeClient())

    output = call_llm(prompt="payload", use_responses=True, model="gpt", json_mode=False)

    assert output == "tree-output"


def test_null_metrics_logs_debug(caplog):
    metrics = NullMetrics()
    with caplog.at_level("DEBUG"):
        metrics.observe_latency("stage", 0.5, stage="summary")
        metrics.increment("events", 2, stage="summary")
    assert "Metric ignored" in caplog.text
    assert "Counter ignored" in caplog.text


def test_prometheus_metrics_timing(monkeypatch):
    observed: list[tuple[str, Any]] = []

    class FakeSeries:
        def __init__(self, label: str) -> None:
            self.label = label

        def labels(self, **labels: Any) -> "FakeSeries":
            observed.append((f"{self.label}.labels", labels))
            return self

        def observe(self, value: float) -> None:
            observed.append((f"{self.label}.observe", value))

        def inc(self, value: float) -> None:
            observed.append((f"{self.label}.inc", value))

    monkeypatch.setattr(PrometheusMetrics, "_LATENCY", FakeSeries("latency"))
    monkeypatch.setattr(PrometheusMetrics, "_COUNTERS", FakeSeries("counter"))

    metrics = PrometheusMetrics()
    metrics.observe_latency("chunk", 0.25, stage="summary")
    metrics.increment("chunk_processed", 3, stage="summary")
    with metrics.time("chunk_time", stage="summary"):
        pass

    assert observed[0][0] == "latency.labels"
    assert observed[2][0] == "counter.labels"
    assert any(entry[0] == "latency.observe" for entry in observed)
    assert any(entry[0] == "counter.inc" for entry in observed)


def test_prometheus_instrument_app(monkeypatch):
    captured_routes: list[str] = []

    class FakeApp:
        def __init__(self) -> None:
            self.state = types.SimpleNamespace()

        def get(self, route: str):
            def decorator(func):
                captured_routes.append(route)
                return func

            return decorator

    import prometheus_client

    monkeypatch.setattr(prometheus_client, "generate_latest", lambda: b"metrics")
    monkeypatch.setattr(prometheus_client, "CONTENT_TYPE_LATEST", "text/plain")

    app = FakeApp()
    metrics = PrometheusMetrics.instrument_app(app)
    assert isinstance(metrics, PrometheusMetrics)
    assert app.state._prometheus_instrumented is True
    assert "/metrics" in captured_routes

    # Second call should reuse instrumentation without duplicating routes.
    captured_routes.clear()
    same_metrics = PrometheusMetrics.instrument_app(app)
    assert same_metrics is metrics
    assert captured_routes == []
