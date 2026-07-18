import importlib.util
from pathlib import Path


class FakeContext:
    def __init__(self):
        self.tools = []
        self.commands = []
        self.cli_commands = []
        self.skills = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs["name"])

    def register_command(self, name, **kwargs):
        self.commands.append(name)

    def register_cli_command(self, **kwargs):
        self.cli_commands.append(kwargs["name"])

    def register_skill(self, name, path, description=""):
        self.skills.append(name)


def test_repository_root_is_a_loadable_filesystem_plugin():
    entry = Path("__init__.py").resolve()
    spec = importlib.util.spec_from_file_location("constellation_plugin_test", entry)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = FakeContext()

    module.register(context)

    assert len(context.tools) == 5
    assert context.commands == [
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
    assert context.cli_commands == ["constellation"]
    assert context.skills == ["constellation"]
