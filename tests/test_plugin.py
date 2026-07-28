import json

import hermes_constellation_plugin as plugin

from hermes_constellation_plugin import register


class FakeContext:
    def __init__(self):
        self.tools = []
        self.commands = []
        self.cli_commands = []
        self.skills = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_command(self, name, **kwargs):
        self.commands.append((name, kwargs))

    def register_cli_command(self, **kwargs):
        self.cli_commands.append(kwargs)

    def register_skill(self, name, path, description=""):
        self.skills.append({"name": name, "path": path, "description": description})


def test_plugin_registers_bounded_tool_and_command_surface():
    context = FakeContext()

    register(context)

    assert {item["name"] for item in context.tools} == {
        "constellation_status",
        "constellation_ingest",
        "constellation_validate",
        "constellation_search",
        "constellation_review",
    }
    assert all(item["toolset"] == "constellation" for item in context.tools)
    assert [name for name, _ in context.commands] == [
        "constellation",
        "research",
        "prep",
        "decay",
        "patterns",
        "trail",
        "classify",
        "analyze",
        "books",
        "hybrid",
        "watchlist",
        "health",
    ]
    assert [item["name"] for item in context.cli_commands] == ["constellation"]
    assert [item["name"] for item in context.skills] == ["constellation"]
    assert context.skills[0]["path"].name == "SKILL.md"


def test_plugin_handlers_return_versioned_json_errors():
    context = FakeContext()
    register(context)

    for tool in context.tools:
        payload = json.loads(tool["handler"]({}, task_id="test"))
        assert payload["version"] == 1
        assert payload["ok"] is False
        assert "error" in payload


def test_private_shortcuts_fail_closed_without_configured_vault(monkeypatch):
    monkeypatch.delenv("CONSTELLATION_VAULT", raising=False)

    assert plugin._handle_prep("01ARZ3NDEKTSV4RRFFQ69G5FAV") == (
        "CONSTELLATION_VAULT is not configured for this Hermes runtime"
    )


def test_root_slash_injects_configured_vault_for_active_vault_commands(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CONSTELLATION_VAULT", str(tmp_path))
    calls = []
    monkeypatch.setattr(plugin, "_run_cli_args", lambda argv: calls.append(argv) or "ok")

    assert plugin._handle_slash("validate") == "ok"
    assert plugin._handle_slash("search Nestle") == "ok"
    assert plugin._handle_slash("watch-run --watchlist-id W --source-ids S --content Alpha") == "ok"
    assert calls == [
        ["validate", str(tmp_path)],
        ["search", str(tmp_path), "Nestle"],
        ["watch-run", str(tmp_path), "--watchlist-id", "W", "--source-ids", "S", "--content", "Alpha"],
    ]


def test_root_slash_preserves_explicit_vault(monkeypatch, tmp_path):
    configured = tmp_path / "configured"
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("CONSTELLATION_VAULT", str(configured))
    calls = []
    monkeypatch.setattr(plugin, "_run_cli_args", lambda argv: calls.append(argv) or "ok")

    assert plugin._handle_slash(f"validate {explicit}") == "ok"
    assert calls == [["validate", str(explicit)]]


def test_root_slash_fails_closed_when_vault_is_implicit_but_unconfigured(monkeypatch):
    monkeypatch.delenv("CONSTELLATION_VAULT", raising=False)

    assert plugin._handle_slash("validate") == (
        "CONSTELLATION_VAULT is not configured for this Hermes runtime"
    )


def test_prep_shortcut_uses_configured_vault(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSTELLATION_VAULT", str(tmp_path))
    calls = []
    monkeypatch.setattr(plugin, "_run_cli_args", lambda argv: calls.append(argv) or "ok")

    assert plugin._handle_prep("01ARZ3NDEKTSV4RRFFQ69G5FAV") == "ok"
    assert calls == [["prep", str(tmp_path), "01ARZ3NDEKTSV4RRFFQ69G5FAV"]]


def test_research_url_uses_egress_gated_inquiry_runner(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSTELLATION_VAULT", str(tmp_path))
    captured = {}

    def fake_run(vault, inquiry, *, sensitivity, max_pages):
        captured.update(
            vault=vault,
            question=inquiry.question,
            sensitivity=sensitivity.value,
            max_pages=max_pages,
        )
        return {
            "status": "partial",
            "sources_discovered": 0,
            "sources_extracted": 0,
            "sources_failed": 0,
            "receipt_path": ".constellation/research-runs/test/receipt.json",
            "run_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "preserved_sources": [],
        }

    monkeypatch.setattr("constellation.research_runner.run_inquiry", fake_run)
    monkeypatch.setattr(
        "constellation.firecrawl_adapter.extract_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("direct extraction bypass")),
    )

    output = plugin._handle_research("https://research.example.test/page")

    assert "Status: partial" in output
    assert captured["question"] == "https://research.example.test/page"
    assert captured["sensitivity"] == "internal"
    assert captured["max_pages"] == 5
