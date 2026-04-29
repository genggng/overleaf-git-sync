from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from ol_ce_sync.errors import OlSyncError
from ol_ce_sync.snapshot import collect_tree, compare_trees, is_ignored, safe_extract_zip
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
