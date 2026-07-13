import json

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
    assert [name for name, _ in context.commands] == ["constellation"]
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
