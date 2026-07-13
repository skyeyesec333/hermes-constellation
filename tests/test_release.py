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
