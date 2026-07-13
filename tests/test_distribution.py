import configparser
import subprocess
import sys
import zipfile


def test_built_wheel_contains_hermes_entrypoint_skill_and_manifest(tmp_path):
    output = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(output.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        entrypoint_name = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        parser = configparser.ConfigParser()
        parser.read_string(archive.read(entrypoint_name).decode("utf-8"))

    assert parser["hermes_agent.plugins"]["constellation"] == "hermes_constellation_plugin"
    assert "hermes_constellation_plugin/plugin.yaml" in names
    assert "hermes_constellation_plugin/skills/constellation/SKILL.md" in names
    assert "hermes_constellation_plugin/skills/constellation/references/architecture.md" in names
    assert "hermes_constellation_plugin/skills/constellation/references/token-aware-research.md" in names
