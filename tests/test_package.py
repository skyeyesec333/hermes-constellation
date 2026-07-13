from pathlib import Path

import yaml


def test_package_exposes_version():
    import constellation

    assert constellation.__version__ == "0.1.0a1"


def test_plugin_manifest_is_valid():
    manifest = yaml.safe_load(Path("plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == "constellation"
    assert manifest["version"] == "0.1.0a1"
    assert len(manifest["provides_tools"]) == 5
