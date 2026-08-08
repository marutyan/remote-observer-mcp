from types import SimpleNamespace

from remote_observer_mcp import observers


def test_extension_registration_discovers_trusted_modules_in_order(monkeypatch):
    calls: list[tuple[object, object]] = []
    imported: list[str] = []
    server = object()
    config = object()

    monkeypatch.setattr(
        observers.pkgutil,
        "iter_modules",
        lambda paths: [
            SimpleNamespace(name="systemd"),
            SimpleNamespace(name="system"),
            SimpleNamespace(name="docker"),
        ],
    )

    def fake_import(name: str):
        imported.append(name)
        return SimpleNamespace(register_tools=lambda got_server, got_config: calls.append((got_server, got_config)))

    monkeypatch.setattr(observers.importlib, "import_module", fake_import)

    observers.register_extension_tools(server, config)

    assert imported == [
        "remote_observer_mcp.observers.docker",
        "remote_observer_mcp.observers.systemd",
    ]
    assert calls == [(server, config), (server, config)]
