from pathlib import Path

from constellation.privacy import audit_tree


def test_privacy_audit_passes_fictional_example_test_data(tmp_path: Path):
    (tmp_path / "demo.md").write_text(
        "Fictional contact: hello@northstar.example.test", encoding="utf-8"
    )

    report = audit_tree(tmp_path, canaries=[])

    assert report["passed"] is True
    assert report["findings"] == []


def test_privacy_audit_detects_canary_and_private_home_path(tmp_path: Path):
    canary = "".join(("PRIVATE", "_RELEASE_", "CANARY_7K9Q"))
    private_path = "/" + "/".join(("home", "private-user", "notes"))
    (tmp_path / "leak.txt").write_text(
        f"{canary} {private_path}", encoding="utf-8"
    )

    report = audit_tree(tmp_path, canaries=[canary])

    assert report["passed"] is False
    rules = {finding["rule"] for finding in report["findings"]}
    assert "release-canary" in rules
    assert "absolute-home-path" in rules


def test_privacy_audit_rejects_symlink(tmp_path: Path):
    outside = tmp_path.parent / "outside-private.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "linked.txt").symlink_to(outside)

    report = audit_tree(tmp_path, canaries=[])

    assert report["passed"] is False
    assert any(item["rule"] == "symlink" for item in report["findings"])


def test_privacy_audit_detects_secret_material_and_forbidden_artifacts(tmp_path: Path):
    secret_name = "".join(("API", "_KEY"))
    secret_value = "sk-" + "fictional-not-a-real-secret-123456"
    (tmp_path / "config.txt").write_text(f"{secret_name}={secret_value}\n", encoding="utf-8")
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")

    report = audit_tree(tmp_path, canaries=[])

    rules = {finding["rule"] for finding in report["findings"]}
    assert report["passed"] is False
    assert "possible-secret" in rules
    assert "forbidden-artifact" in rules


def test_privacy_audit_rejects_symlinked_root(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    report = audit_tree(linked, canaries=[])

    assert report["passed"] is False
    assert any(item["rule"] == "symlink-root" for item in report["findings"])
