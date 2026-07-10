from __future__ import annotations

import unicodedata
import zipfile
from pathlib import Path

import pytest

from ol_ce_sync.errors import OlSyncError
from ol_ce_sync.snapshot import (
    collect_tree,
    compare_trees,
    is_ignored,
    reset_directory_from_snapshot,
    safe_extract_zip,
)
from ol_ce_sync.utils.text import is_text_path


def test_safe_extract_zip_blocks_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.tex", "bad")

    with pytest.raises(OlSyncError):
        safe_extract_zip(archive, tmp_path / "out")


def test_safe_extract_zip_blocks_absolute_paths(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("/evil.tex", "bad")

    with pytest.raises(OlSyncError):
        safe_extract_zip(archive, tmp_path / "out")


def test_safe_extract_zip_rejects_casefold_collision(tmp_path: Path) -> None:
    archive = tmp_path / "collision.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Figure.tex", "one")
        zf.writestr("figure.tex", "two")

    with pytest.raises(OlSyncError, match="collide"):
        safe_extract_zip(archive, tmp_path / "out")


def test_safe_extract_zip_rejects_unicode_normalization_collision(tmp_path: Path) -> None:
    archive = tmp_path / "unicode-collision.zip"
    composed = "café.tex"
    decomposed = unicodedata.normalize("NFD", composed)
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(composed, "one")
        zf.writestr(decomposed, "two")

    with pytest.raises(OlSyncError, match="collide"):
        safe_extract_zip(archive, tmp_path / "out")


def test_ignore_rules_and_tree_compare(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text("hello", encoding="utf-8")
    (tmp_path / ".ol-sync").mkdir()
    (tmp_path / ".ol-sync" / "config.toml").write_text("secret", encoding="utf-8")
    (tmp_path / "main.aux").write_text("generated", encoding="utf-8")

    patterns = [".ol-sync/", "*.aux"]
    tree = collect_tree(tmp_path, patterns)

    assert tree == {"main.tex": b"hello"}
    assert is_ignored(".ol-sync/config.toml", patterns)
    assert is_ignored("main.aux", patterns)

    diff = compare_trees({"main.tex": b"hello"}, {"main.tex": b"goodbye", "new.tex": b"x"})
    assert diff.added == ("new.tex",)
    assert diff.modified == ("main.tex",)
    assert diff.deleted == ()


def test_text_binary_detection() -> None:
    assert is_text_path("main.tex", b"\xff")
    assert not is_text_path("figure.png", b"hello")
    assert not is_text_path("blob.dat", b"\x00\x01")
    assert is_text_path("unknown.dat", b"hello")


def test_reset_directory_from_snapshot_preserves_ignored_untracked_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    snapshot = tmp_path / "snapshot"
    repo.mkdir()
    snapshot.mkdir()

    (repo / "Chapter").mkdir()
    (repo / "Chapter" / "main.tex").write_text("old", encoding="utf-8")
    (repo / "Chapter" / "scratch.aux").write_text("keep", encoding="utf-8")
    (snapshot / "Chapter").mkdir()
    (snapshot / "Chapter" / "main.tex").write_text("new", encoding="utf-8")

    reset_directory_from_snapshot(repo, snapshot, ["*.aux"])

    assert (repo / "Chapter" / "main.tex").read_text(encoding="utf-8") == "new"
    assert (repo / "Chapter" / "scratch.aux").read_text(encoding="utf-8") == "keep"


def test_reset_directory_from_snapshot_removes_empty_sync_dirs_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    snapshot = tmp_path / "snapshot"
    repo.mkdir()
    snapshot.mkdir()

    (repo / "build").mkdir()
    (repo / "build" / "keep.log").write_text("keep", encoding="utf-8")
    (repo / "notes").mkdir()
    (repo / "notes" / "draft.txt").write_text("drop", encoding="utf-8")

    reset_directory_from_snapshot(repo, snapshot, ["*.log"])

    assert (repo / "build" / "keep.log").exists()
    assert not (repo / "notes").exists()
