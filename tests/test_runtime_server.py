from __future__ import annotations

from types import SimpleNamespace

import src.runtime_server as runtime_server


def test_worker_count_prefers_env(monkeypatch):
    monkeypatch.setenv("UVICORN_WORKERS", "4")
    assert runtime_server._worker_count() == 4
    monkeypatch.setenv("UVICORN_WORKERS", "invalid")
    monkeypatch.setattr(runtime_server.multiprocessing, "cpu_count", lambda: 6)
    assert runtime_server._worker_count() == 6


def test_main_sets_env_and_invokes_uvicorn(monkeypatch):
    monkeypatch.setattr(runtime_server, "_worker_count", lambda: 2)
    monkeypatch.setenv("PORT", "9090")
    recorded: dict[str, object] = {}

    def _fake_run(app, *, host, port, factory, workers, lifespan):
        recorded.update(
            {
                "app": app,
                "host": host,
                "port": port,
                "factory": factory,
                "workers": workers,
                "lifespan": lifespan,
            }
        )

    monkeypatch.setattr(runtime_server, "uvicorn", SimpleNamespace(run=_fake_run))
    runtime_server.main()
    assert recorded["app"] == "src.main:create_app"
    assert recorded["host"] == "0.0.0.0"
    assert recorded["port"] == 9090
    assert recorded["workers"] == 2
