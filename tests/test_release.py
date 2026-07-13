from pathlib import Path

import pytest

from constellation.release import ReleaseError, build_release


def test_release_compiler_copies_only_allowlisted_files(tmp_path: Path):
    source = tmp_path / "source"
    destination = tmp_path / "release"
    source.mkdir()
    (source / "README.md").write_text("public", encoding="utf-8")
    manifest = source / "lineage.yaml"
    manifest.write_text(
        "public_roots: ['README.md']\nfiles:\n  README.md:\n    lineage: handwritten-generic\n",
        encoding="utf-8",
    )

    report = build_release(source, destination, manifest)

    assert report["copied"] == ["README.md"]
    assert (destination / "README.md").read_text(encoding="utf-8") == "public"
    assert report["audit"]["passed"] is True
    assert report["tree_sha256"]
    assert report["files"][0]["path"] == "README.md"
    assert report["files"][0]["lineage"] == "handwritten-generic"
    assert report["files"][0]["sha256"]


def test_release_compiler_fails_on_unknown_file(tmp_path: Path):
    source = tmp_path / "source"
    destination = tmp_path / "release"
    source.mkdir()
    (source / "README.md").write_text("public", encoding="utf-8")
    (source / "private.txt").write_text("must not ship", encoding="utf-8")
    manifest = source / "lineage.yaml"
    manifest.write_text(
        "public_roots: ['.']\nfiles:\n  README.md:\n    lineage: handwritten-generic\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseError, match="Unknown public file"):
        build_release(source, destination, manifest)


def test_release_compiler_rejects_symlink(tmp_path: Path):
    source = tmp_path / "source"
    destination = tmp_path / "release"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (source / "linked.txt").symlink_to(outside)
    manifest = source / "lineage.yaml"
    manifest.write_text(
        "public_roots: ['linked.txt']\nfiles:\n  linked.txt:\n    lineage: handwritten-generic\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseError, match="symlink"):
        build_release(source, destination, manifest)


def test_release_compiler_rejects_unsafe_destination(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("public", encoding="utf-8")
    manifest = source / "lineage.yaml"
    manifest.write_text(
        "public_roots: ['README.md']\nfiles:\n  README.md:\n    lineage: handwritten-generic\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseError, match="inside the source"):
        build_release(source, source / "release", manifest)

    real_destination = tmp_path / "real-destination"
    real_destination.mkdir()
    linked_destination = tmp_path / "linked-destination"
    linked_destination.symlink_to(real_destination, target_is_directory=True)
    with pytest.raises(ReleaseError, match="symlink"):
        build_release(source, linked_destination, manifest)


def test_release_compiler_removes_staging_when_privacy_audit_fails(tmp_path: Path):
    source = tmp_path / "source"
    destination = tmp_path / "release"
    source.mkdir()
    private_path = "/" + "/".join(("home", "private-user", "vault"))
    (source / "README.md").write_text(private_path, encoding="utf-8")
    manifest = source / "lineage.yaml"
    manifest.write_text(
        "version: 1\npublic_roots: ['README.md']\nfiles:\n"
        "  README.md:\n    lineage: handwritten-generic\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseError, match="privacy audit failed"):
        build_release(source, destination, manifest)

    assert not destination.exists()


def test_release_compiler_ignores_runtime_cache_but_not_unknown_source(tmp_path: Path):
    source = tmp_path / "source"
    destination = tmp_path / "release"
    (source / "src/__pycache__").mkdir(parents=True)
    (source / "src/main.py").write_text("value = 1\n", encoding="utf-8")
    (source / "src/__pycache__/main.pyc").write_bytes(b"cache")
    manifest = source / "lineage.yaml"
    manifest.write_text(
        "version: 1\npublic_roots: ['src']\nfiles:\n"
        "  src/main.py:\n    lineage: handwritten-generic\n",
        encoding="utf-8",
    )
    build_release(source, destination, manifest)
    assert not (destination / "src/__pycache__").exists()

    (source / "src/undeclared.py").write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="Unknown public file"):
        build_release(source, tmp_path / "second-release", manifest)


def test_repository_public_manifest_compiles_an_exact_deterministic_tree(tmp_path: Path):
    source = Path.cwd()
    manifest = source / "resources/public-lineage.yaml"

    first = build_release(source, tmp_path / "first", manifest)
    second = build_release(source, tmp_path / "second", manifest)

    assert first["audit"]["passed"] is True
    assert first["file_count"] == len(first["copied"])
    assert first["tree_sha256"] == second["tree_sha256"]
    assert "resources/public-lineage.yaml" in first["copied"]
    assert "docs/integrations.md" in first["copied"]
